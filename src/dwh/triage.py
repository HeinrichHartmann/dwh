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
    Per ADR-006, creates tombstone documents for deleted files (excluded by user).

    Returns: {
        "filed": int,
        "excluded": int,
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

    # Build entry lookup by hash and by id
    entries_by_hash = {}
    entries_by_id = {}
    for entry in entries:
        h = entry["blob_hash"]
        if h not in entries_by_hash:
            entries_by_hash[h] = []
        entries_by_hash[h].append(entry)
        entries_by_id[entry["id"]] = entry

    # Scan triage/ for remaining files
    triage_files = {}
    if triage_dir.exists():
        for file in triage_dir.rglob("*"):
            if file.is_file():
                file_hash = drop_module.compute_hash(file)
                rel_path = file.relative_to(triage_dir)
                triage_files[str(rel_path)] = file_hash

    # Scan warehouse root for classified files (ADR-003: categories at root)
    # Skip system directories: .dwh, _history, _triage, _staging
    system_dirs = {".dwh", "_history", "_triage", "_staging"}
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
    filed_matches = []
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

            filed_matches.append(
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

    # Find excluded entries (ADR-006: deleted from triage = excluded)
    # Entry is excluded if: not already classified, not in warehouse, not in triage
    excluded_entries = []
    filed_entry_ids = {match.entry_id for match in filed_matches}

    for entry in entries:
        # Check if already has document
        existing_doc = conn.execute(
            "SELECT id FROM documents WHERE entry_id = ?", (entry["id"],)
        ).fetchone()
        if existing_doc:
            continue  # Already classified

        # Check if in warehouse (filed this sync)
        if entry["id"] in filed_entry_ids:
            continue  # Being filed this sync

        # Check if still in triage
        entry_rel_path = entry["relative_path"]
        if entry_rel_path in triage_files:
            continue  # Still in triage

        # Not classified, not filed, not in triage -> excluded (deleted by user)
        excluded_entries.append(entry)

    # Create classification records
    classifications = []

    # Create documents for filed entries
    if filed_matches or excluded_entries:
        seq_num = history_module.get_next_history_number(history_dir)
        classify_file = history_dir / f"{seq_num:03d}_classify.json"

        for match in filed_matches:
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

        # Create tombstone documents for excluded entries
        for entry in excluded_entries:
            cursor = conn.execute(
                """INSERT INTO documents (entry_id, name, category)
                   VALUES (?, ?, '')""",
                (entry["id"], entry["filename"]),
            )
            document_id = cursor.lastrowid

            classifications.append(
                {
                    "entry_id": entry["id"],
                    "document_id": document_id,
                    "category": "",  # Tombstone marker
                    "name": entry["filename"],
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
        "filed": len(filed_matches),
        "excluded": len(excluded_entries),
        "skipped": len(triage_files),
        "ambiguous": ambiguous,
    }


def get_triage_state(conn: sqlite3.Connection) -> str | None:
    """Get the drop_id currently being triaged, or None."""
    row = conn.execute("SELECT drop_id FROM triage_state WHERE id = 1").fetchone()
    return row["drop_id"] if row else None


def get_drop_triage_status(drop_id: str, conn: sqlite3.Connection) -> dict:
    """Get triage status for a drop.

    Returns: {
        "status": "complete" | "in_progress" | "pending",
        "total_entries": int,
        "classified_entries": int,
    }
    """
    # Count total entries
    total = conn.execute(
        "SELECT COUNT(*) as count FROM entries WHERE drop_id = ?", (drop_id,)
    ).fetchone()["count"]

    # Count classified entries (includes both filed and excluded)
    classified = conn.execute(
        """SELECT COUNT(*) as count
           FROM documents d
           JOIN entries e ON d.entry_id = e.id
           WHERE e.drop_id = ?""",
        (drop_id,),
    ).fetchone()["count"]

    # Determine status
    if classified == 0:
        status = "pending"
    elif classified < total:
        status = "in_progress"
    else:
        status = "complete"

    return {
        "status": status,
        "total_entries": total,
        "classified_entries": classified,
    }


def get_next_untriaged_drop(conn: sqlite3.Connection) -> str | None:
    """Get next drop from triage queue (LIFO: newest first).

    Returns drop_id of next incomplete drop, or None if queue is empty.
    """
    # Get all drops ordered by creation (newest first)
    drops = conn.execute("""SELECT id FROM drops ORDER BY created_at DESC""").fetchall()

    for drop_row in drops:
        drop_id = drop_row["id"]
        status = get_drop_triage_status(drop_id, conn)

        if status["status"] != "complete":
            return drop_id

    return None  # All drops complete


def get_blob_classification(blob_hash: str, conn: sqlite3.Connection) -> str | None:
    """Get most recent classification category for a blob.

    Returns the category from the most recent document record with this blob hash,
    or None if blob has never been classified.

    Uses "last classification wins" strategy (ADR-005).
    """
    row = conn.execute(
        """SELECT d.category
           FROM documents d
           JOIN entries e ON d.entry_id = e.id
           WHERE e.blob_hash = ?
           ORDER BY d.created_at DESC
           LIMIT 1""",
        (blob_hash,),
    ).fetchone()

    return row["category"] if row else None


def triage_suggest(
    triage_dir: Path, staging_dir: Path, conn: sqlite3.Connection
) -> dict:
    """Auto-classify known blobs from triage to staging.

    Moves files with known classifications from _triage/ to _staging/,
    leaving unknown files in _triage/ for manual classification.

    Returns: {
        "auto_classified": [(filename, category), ...],
        "needs_manual": [filename, ...]
    }
    """
    # Check triage state
    state_row = conn.execute("SELECT drop_id FROM triage_state WHERE id = 1").fetchone()
    if not state_row:
        raise NoTriageInProgressError()

    # Clear staging directory
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    auto_classified = []
    needs_manual = []

    # Scan triage directory
    if not triage_dir.exists():
        return {"auto_classified": auto_classified, "needs_manual": needs_manual}

    for file in triage_dir.rglob("*"):
        if not file.is_file():
            continue

        # Compute hash
        file_hash = drop_module.compute_hash(file)

        # Check for previous classification
        category = get_blob_classification(file_hash, conn)

        if category is not None:
            # Known blob - move to staging
            target = staging_dir / category / file.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(file), str(target))
            auto_classified.append((file.name, category))
        else:
            # Unknown blob - leave in triage
            needs_manual.append(file.name)

    return {"auto_classified": auto_classified, "needs_manual": needs_manual}


def triage_merge(
    staging_dir: Path, warehouse_root: Path, history_dir: Path, conn: sqlite3.Connection
) -> dict:
    """Merge staged files to warehouse and create classifications.

    Moves files from _staging/ to warehouse root and creates classification records.

    Returns: {
        "merged": [(filename, category), ...]
    }
    """
    # Check triage state
    state_row = conn.execute("SELECT drop_id FROM triage_state WHERE id = 1").fetchone()
    if not state_row:
        raise NoTriageInProgressError()

    drop_id = state_row["drop_id"]

    # Collect all files in staging
    merged = []
    classifications = []

    if not staging_dir.exists():
        return {"merged": merged}

    for file in staging_dir.rglob("*"):
        if not file.is_file():
            continue

        # Compute category from path relative to staging
        rel_path = file.relative_to(staging_dir)
        category = str(rel_path.parent)

        # Move to warehouse
        target = warehouse_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(file), str(target))

        # Find entry_id for this file
        file_hash = drop_module.compute_hash(target)
        entry = conn.execute(
            "SELECT id FROM entries WHERE drop_id = ? AND blob_hash = ?",
            (drop_id, file_hash),
        ).fetchone()

        if not entry:
            continue  # Shouldn't happen, but skip if no match

        # Check if entry already has a document (skip if already classified)
        existing_doc = conn.execute(
            "SELECT id FROM documents WHERE entry_id = ?", (entry["id"],)
        ).fetchone()
        if existing_doc:
            continue  # Entry already classified, skip

        # Insert document record (creates classification)
        cursor = conn.execute(
            """INSERT INTO documents (entry_id, name, category)
               VALUES (?, ?, ?)""",
            (entry["id"], file.name, category),
        )

        classifications.append(
            {
                "entry_id": entry["id"],
                "document_id": cursor.lastrowid,
                "category": category,
                "name": file.name,
            }
        )

        merged.append((file.name, category))

    # Write classification event to history
    if classifications:
        seq_num = history_module.get_next_history_number(history_dir)
        classify_file = history_dir / f"{seq_num:03d}_classify.json"

        classification_record = {
            "type": "classify",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "actor": getpass.getuser(),
            "message": "Triage merge (auto-classified)",
            "classifications": classifications,
        }

        with open(classify_file, "w") as f:
            json.dump(classification_record, f, indent=2)

        conn.commit()

    # Clear staging directory
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    return {"merged": merged}
