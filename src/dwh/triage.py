"""Triage workflow operations."""

import getpass
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dwh import drop as drop_module
from dwh import history as history_module


class TriageError(Exception):
    """Base exception for triage operations."""

    pass


class NoTriageInProgressError(TriageError):
    """No triage in progress."""

    def __init__(self):
        super().__init__("No triage in progress")


@dataclass
class TriageMatch:
    """A matched file from triage to documents."""

    entry_id: str
    triage_path: Path
    document_path: Path
    category: str
    name: str


def triage_checkout(
    drop_id: str | None,
    warehouse_root: Path,
    history_dir: Path,
    triage_dir: Path,
    conn: sqlite3.Connection,
) -> drop_module.Drop:
    """
    Checkout a drop for triage.

    - Clears triage/ directory
    - Copies files from drop to triage/
    - Records triage state in database
    """
    # Get drop to triage
    if drop_id:
        d = drop_module.drop_inspect(drop_id, conn)
    else:
        # Get latest drop that hasn't been fully classified
        drops = drop_module.drop_list(conn)
        if not drops:
            raise TriageError("No drops available to triage")

        # For now, just use the most recent drop
        # TODO: Skip drops where all entries are already documents
        d = drop_module.drop_inspect(drops[0].id, conn)

    # Clear triage directory
    if triage_dir.exists():
        shutil.rmtree(triage_dir)
    triage_dir.mkdir(parents=True)

    # Find drop in history
    drop_dir = history_module.find_drop_in_history(history_dir, d.id)
    if not drop_dir:
        raise TriageError(f"Drop {d.id} not found in history")

    tree_dir = drop_dir / "tree"

    # Copy files to triage/
    for file in tree_dir.rglob("*"):
        if file.is_file():
            relative_path = file.relative_to(tree_dir)
            dest = triage_dir / relative_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, dest)

    # Record triage state
    conn.execute("DELETE FROM triage_state")  # Clear old state
    conn.execute("INSERT INTO triage_state (id, drop_id) VALUES (1, ?)", (d.id,))
    conn.commit()

    return d


def triage_sync(
    warehouse_root: Path, triage_dir: Path, history_dir: Path, conn: sqlite3.Connection
) -> dict:
    """
    Sync triage: match files, create classifications, update database.

    Per ADR-003, scans warehouse root for classified files (skips system dirs).

    Returns: {
        "classified": int,
        "skipped": int,
        "ambiguous": list[str]
    }
    """
    # Get current triage state
    state_row = conn.execute("SELECT drop_id FROM triage_state WHERE id = 1").fetchone()
    if not state_row:
        raise NoTriageInProgressError()

    triaging_drop_id = state_row["drop_id"]

    # Get all entries from the drop being triaged
    entries = conn.execute(
        "SELECT * FROM entries WHERE drop_id = ?", (triaging_drop_id,)
    ).fetchall()

    # Build entry lookup by hash
    entries_by_hash = {}
    for entry in entries:
        h = entry["blob_hash"]
        if h not in entries_by_hash:
            entries_by_hash[h] = []
        entries_by_hash[h].append(entry)

    # Scan triage/ for remaining files
    triage_files = {}
    if triage_dir.exists():
        for file in triage_dir.rglob("*"):
            if file.is_file():
                file_hash = drop_module.compute_hash(file)
                rel_path = file.relative_to(triage_dir)
                triage_files[str(rel_path)] = file_hash

    # Scan warehouse root for classified files (ADR-003: categories at root)
    # Skip system directories: .dwh, _history, _triage
    system_dirs = {".dwh", "_history", "_triage"}
    document_files = {}

    for item in warehouse_root.iterdir():
        # Skip system directories
        if item.name in system_dirs:
            continue

        # Scan user categories
        if item.is_dir():
            for file in item.rglob("*"):
                if file.is_file():
                    file_hash = drop_module.compute_hash(file)
                    rel_path = file.relative_to(warehouse_root)
                    document_files[str(rel_path)] = (file_hash, file)

    # Match files: find entries that moved from triage/ to warehouse root
    matches = []
    ambiguous = []

    for doc_path_str, (doc_hash, doc_file) in document_files.items():
        doc_path = Path(doc_path_str)

        # Skip if this entry is already classified
        existing = conn.execute(
            "SELECT id FROM documents WHERE entry_id IN (SELECT id FROM entries WHERE blob_hash = ?)",
            (doc_hash,),
        ).fetchone()
        if existing:
            continue  # Already a document

        # Find matching entries by hash
        matching_entries = entries_by_hash.get(doc_hash, [])

        if len(matching_entries) == 0:
            # File in documents/ but not from this drop - skip
            continue
        elif len(matching_entries) == 1:
            # Unambiguous match
            entry = matching_entries[0]
            category = str(doc_path.parent) if doc_path.parent != Path(".") else ""
            name = doc_path.name

            matches.append(
                TriageMatch(
                    entry_id=entry["id"],
                    triage_path=triage_dir / entry["relative_path"],
                    document_path=doc_file,
                    category=category,
                    name=name,
                )
            )
        else:
            # Ambiguous: multiple entries with same hash
            ambiguous.append(doc_path_str)

    # Create classification record in history if we have matches
    classifications = []
    if matches:
        seq_num = history_module.get_next_history_number(history_dir)
        classify_file = history_dir / f"{seq_num:03d}_classify.json"

        for match in matches:
            # Insert document record
            cursor = conn.execute(
                """INSERT INTO documents (entry_id, name, category)
                   VALUES (?, ?, ?)""",
                (match.entry_id, match.name, match.category),
            )
            document_id = cursor.lastrowid

            classifications.append(
                {
                    "entry_id": match.entry_id,
                    "document_id": document_id,
                    "category": match.category,
                    "name": match.name,
                }
            )

        # Write classification record
        classification_record = {
            "type": "classify",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "actor": getpass.getuser(),
            "message": "Triage sync",
            "classifications": classifications,
        }

        with open(classify_file, "w") as f:
            json.dump(classification_record, f, indent=2)

        conn.commit()

    # Clear triage state and directory only if all files were processed
    if len(triage_files) == 0:
        # All files were classified or removed, safe to clean up
        conn.execute("DELETE FROM triage_state")
        conn.commit()

        if triage_dir.exists():
            shutil.rmtree(triage_dir)
    else:
        # Files remain in triage, keep state and directory
        pass

    return {
        "classified": len(matches),
        "skipped": len(triage_files),
        "ambiguous": ambiguous,
    }


def get_triage_state(conn: sqlite3.Connection) -> str | None:
    """Get the drop_id currently being triaged, or None."""
    row = conn.execute("SELECT drop_id FROM triage_state WHERE id = 1").fetchone()
    return row["drop_id"] if row else None
