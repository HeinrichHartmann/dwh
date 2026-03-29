"""Drop and entry operations."""

import getpass
import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class DropError(Exception):
    """Base exception for drop operations."""
    pass


class DropNotFoundError(DropError):
    """Drop not found."""
    def __init__(self, drop_id: str):
        super().__init__(f"Drop not found: {drop_id}")
        self.drop_id = drop_id


@dataclass
class Entry:
    """File entry within a drop."""
    id: str
    drop_id: str
    blob_hash: str
    filename: str
    relative_path: str
    size: int


@dataclass
class Drop:
    """Drop record."""
    id: str
    message: str
    actor: str
    created_at: str
    entries: list[Entry]


@dataclass
class DropSummary:
    """Drop summary for listing."""
    id: str
    message: str
    actor: str
    created_at: str
    entry_count: int


def generate_drop_id() -> str:
    """Generate drop ID: d_YYYYMMDD_HHMMSS_hash8."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    # Add random hash suffix for uniqueness
    random_data = f"{now.isoformat()}_{id(object())}".encode()
    hash_suffix = hashlib.sha256(random_data).hexdigest()[:8]

    return f"d_{timestamp}_{hash_suffix}"


def generate_entry_id(drop_id: str, relative_path: Path) -> str:
    """Generate deterministic entry ID from drop_id + relative_path."""
    data = f"{drop_id}:{relative_path}".encode()
    hash_hex = hashlib.sha256(data).hexdigest()[:16]
    return f"e_{hash_hex}"


def compute_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def expand_paths(paths: list[Path]) -> list[Path]:
    """Expand paths to individual files, recursively traversing directories."""
    files = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            for item in path.rglob("*"):
                if item.is_file():
                    files.append(item)
    return files


def generate_receipt(drop_id: str, message: str, actor: str) -> dict:
    """Generate receipt JSON (metadata only)."""
    return {
        "type": "drop",
        "drop_id": drop_id,
        "message": message,
        "actor": actor,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def write_receipt(drop_dir: Path, receipt: dict) -> None:
    """Write receipt.json to drop directory."""
    receipt_path = drop_dir / "receipt.json"
    with open(receipt_path, "w") as f:
        json.dump(receipt, f, indent=2)


def derive_entries(tree_dir: Path, drop_id: str) -> list[Entry]:
    """Derive entries by scanning tree/ contents."""
    entries = []
    for file in sorted(tree_dir.rglob("*")):
        if file.is_file():
            relative_path = file.relative_to(tree_dir)
            entries.append(Entry(
                id=generate_entry_id(drop_id, relative_path),
                drop_id=drop_id,
                filename=file.name,
                relative_path=str(relative_path),
                blob_hash=compute_hash(file),
                size=file.stat().st_size,
            ))
    return entries


def apply_drop_to_db(conn: sqlite3.Connection, receipt: dict, entries: list[Entry]) -> None:
    """Apply drop to database."""
    # Insert drop record
    conn.execute(
        "INSERT INTO drops (id, message, actor, created_at) VALUES (?, ?, ?, ?)",
        (receipt["drop_id"], receipt["message"], receipt["actor"], receipt["created_at"])
    )

    # Insert blob and entry records
    for entry in entries:
        # Insert or ignore blob (deduplication)
        conn.execute(
            "INSERT OR IGNORE INTO blobs (hash, size) VALUES (?, ?)",
            (entry.blob_hash, entry.size)
        )

        # Insert entry
        conn.execute(
            """INSERT INTO entries (id, drop_id, blob_hash, filename, relative_path, source_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry.id, entry.drop_id, entry.blob_hash, entry.filename,
             entry.relative_path, "")
        )

    conn.commit()


def compute_relative_path(file_path: Path, input_paths: list[Path]) -> Path:
    """Compute relative path for file within the import context.

    If importing a single file, use just the filename.
    If importing from a directory, preserve structure relative to parent.
    """
    file_path = file_path.resolve()

    # Find which input path this file belongs to
    for input_path in input_paths:
        input_path = input_path.resolve()
        try:
            # Check if file is under this input path
            if input_path.is_file() and file_path == input_path:
                # Single file import - use just filename
                return Path(file_path.name)
            elif input_path.is_dir() and file_path.is_relative_to(input_path):
                # Directory import - preserve structure
                return file_path.relative_to(input_path)
        except ValueError:
            continue

    # Fallback: just use filename
    return Path(file_path.name)


def drop_import(paths: list[Path], message: str, warehouse_root: Path, history_dir: Path, conn: sqlite3.Connection) -> Drop:
    """Import files and create a drop in history."""
    from dwh.history import get_next_history_number

    # Generate drop ID and metadata
    drop_id = generate_drop_id()
    actor = getpass.getuser()
    seq_num = get_next_history_number(history_dir)

    # Create history folder
    drop_dir = history_dir / f"{seq_num:03d}_drop_{drop_id}"
    tree_dir = drop_dir / "tree"
    tree_dir.mkdir(parents=True)

    # Expand and copy files to tree/, preserving structure
    for file_path in expand_paths(paths):
        relative_path = compute_relative_path(file_path, paths)
        dest = tree_dir / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest)

    # Write receipt (metadata only)
    receipt = generate_receipt(drop_id, message, actor)
    write_receipt(drop_dir, receipt)

    # Derive entries from tree/ and update database
    entries = derive_entries(tree_dir, drop_id)
    apply_drop_to_db(conn, receipt, entries)

    return Drop(
        id=drop_id,
        message=message,
        actor=actor,
        created_at=receipt["created_at"],
        entries=entries
    )


def drop_list(conn: sqlite3.Connection) -> list[DropSummary]:
    """List all drops."""
    rows = conn.execute("""
        SELECT d.id, d.message, d.actor, d.created_at, COUNT(e.id) as entry_count
        FROM drops d
        LEFT JOIN entries e ON e.drop_id = d.id
        GROUP BY d.id
        ORDER BY d.created_at DESC
    """).fetchall()

    return [DropSummary(
        id=row["id"],
        message=row["message"],
        actor=row["actor"],
        created_at=row["created_at"],
        entry_count=row["entry_count"]
    ) for row in rows]


def drop_inspect(drop_id: str, conn: sqlite3.Connection) -> Drop:
    """Get full drop details."""
    drop_row = conn.execute(
        "SELECT * FROM drops WHERE id = ?", (drop_id,)
    ).fetchone()

    if not drop_row:
        raise DropNotFoundError(drop_id)

    entries = conn.execute(
        "SELECT * FROM entries WHERE drop_id = ? ORDER BY relative_path", (drop_id,)
    ).fetchall()

    return Drop(
        id=drop_row["id"],
        message=drop_row["message"],
        actor=drop_row["actor"],
        created_at=drop_row["created_at"],
        entries=[Entry(
            id=e["id"],
            drop_id=e["drop_id"],
            blob_hash=e["blob_hash"],
            filename=e["filename"],
            relative_path=e["relative_path"],
            size=0  # Size not needed for inspect
        ) for e in entries]
    )


def drop_export(drop_id: str, dest: Path, history_dir: Path) -> int:
    """Export drop to destination directory."""
    from dwh.history import find_drop_in_history

    drop_dir = find_drop_in_history(history_dir, drop_id)
    if not drop_dir:
        raise DropNotFoundError(drop_id)

    tree_dir = drop_dir / "tree"
    dest.mkdir(parents=True, exist_ok=True)

    # Copy tree/ contents to destination
    count = 0
    for file in tree_dir.rglob("*"):
        if file.is_file():
            relative_path = file.relative_to(tree_dir)
            entry_dest = dest / relative_path
            entry_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, entry_dest)
            count += 1

    return count
