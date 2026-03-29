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
    auto_classified_count: int = 0
    tree_fingerprint: str | None = None


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


def compute_tree_fingerprint(entries: list["Entry"]) -> str:
    """Compute fingerprint of an entry tree.

    Fingerprint is SHA-256 hash of sorted (relative_path, blob_hash) pairs.
    This allows detection of duplicate drops with identical content.

    Args:
        entries: List of Entry objects from a drop

    Returns:
        Hex string of tree fingerprint
    """
    # Create sorted list of (relative_path, blob_hash) tuples
    pairs = sorted((e.relative_path, e.blob_hash) for e in entries)

    # Compute hash of concatenated pairs
    sha256 = hashlib.sha256()
    for rel_path, blob_hash in pairs:
        # Encode as "path:hash\n" for each entry
        sha256.update(f"{rel_path}:{blob_hash}\n".encode("utf-8"))

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
            entries.append(
                Entry(
                    id=generate_entry_id(drop_id, relative_path),
                    drop_id=drop_id,
                    filename=file.name,
                    relative_path=str(relative_path),
                    blob_hash=compute_hash(file),
                    size=file.stat().st_size,
                )
            )
    return entries


def apply_drop_to_db(
    conn: sqlite3.Connection, receipt: dict, entries: list[Entry]
) -> None:
    """Apply drop to database."""
    # Compute tree fingerprint from entries
    tree_fingerprint = compute_tree_fingerprint(entries)

    # Insert drop record
    conn.execute(
        "INSERT INTO drops (id, message, actor, created_at, tree_fingerprint) VALUES (?, ?, ?, ?, ?)",
        (
            receipt["drop_id"],
            receipt["message"],
            receipt["actor"],
            receipt["created_at"],
            tree_fingerprint,
        ),
    )

    # Insert blob and entry records
    for entry in entries:
        # Insert or ignore blob (deduplication)
        conn.execute(
            "INSERT OR IGNORE INTO blobs (hash, size) VALUES (?, ?)",
            (entry.blob_hash, entry.size),
        )

        # Insert entry
        conn.execute(
            """INSERT INTO entries (id, drop_id, blob_hash, filename, relative_path, source_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entry.id,
                entry.drop_id,
                entry.blob_hash,
                entry.filename,
                entry.relative_path,
                "",
            ),
        )

    conn.commit()


def apply_classification_to_db(conn: sqlite3.Connection, record: dict) -> None:
    """Apply classification record to database."""
    for classification in record.get("classifications", []):
        # Insert document record (ignore if already exists)
        conn.execute(
            """INSERT OR IGNORE INTO documents (entry_id, name, category)
               VALUES (?, ?, ?)""",
            (
                classification["entry_id"],
                classification["name"],
                classification["category"],
            ),
        )
    conn.commit()


def rebuild_database(history_dir: Path, db_path: Path) -> dict:
    """Rebuild database by replaying history.

    Returns: {
        "drops": int,
        "classifications": int
    }
    """
    from dwh import db

    # Delete existing database and create fresh one
    if db_path.exists():
        db_path.unlink()

    db.init_db(db_path)
    conn = db.connect(db_path)

    drops_count = 0
    classifications_count = 0

    # Replay history in order
    for item in sorted(history_dir.iterdir()):
        if item.is_dir() and "_drop_" in item.name:
            # Load drop receipt
            receipt_path = item / "receipt.json"
            if not receipt_path.exists():
                continue

            receipt = json.loads(receipt_path.read_text())
            drop_id = receipt["drop_id"]

            # Derive entries from tree/
            tree_dir = item / "tree"
            if not tree_dir.exists():
                continue

            entries = derive_entries(tree_dir, drop_id)

            # Apply to database
            apply_drop_to_db(conn, receipt, entries)
            drops_count += 1

        elif item.is_file() and item.suffix == ".json" and "_classify" in item.name:
            # Load classification record
            record = json.loads(item.read_text())

            # Apply to database
            apply_classification_to_db(conn, record)
            classifications_count += 1

    conn.close()

    return {"drops": drops_count, "classifications": classifications_count}


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


def is_in_tree_path(file_path: Path, warehouse_root: Path) -> bool:
    """Check if file path is within warehouse (excluding system dirs).

    Returns True if:
    - Path is within warehouse_root
    - Path is NOT in system directories (.dwh, _history, _triage)
    """
    try:
        resolved_file = file_path.resolve()
        resolved_root = warehouse_root.resolve()

        # Check if file is within warehouse
        rel_path = resolved_file.relative_to(resolved_root)

        # System directories that are not allowed for import
        system_dirs = {".dwh", "_history", "_triage"}

        # Check if first component is a system directory
        first_part = rel_path.parts[0] if rel_path.parts else ""
        return first_part not in system_dirs

    except ValueError:
        # Not within warehouse_root
        return False


def create_classifications(
    entry_map: dict[str, tuple[Entry, Path, str]],
    history_dir: Path,
    conn: sqlite3.Connection,
    message: str = "Auto-classify",
) -> int:
    """Create classification records for entries.

    Args:
        entry_map: Map of entry_id -> (entry, file_path, category)
        history_dir: History directory path
        conn: Database connection
        message: Classification message

    Returns:
        Number of classifications created
    """
    from dwh.history import get_next_history_number

    if not entry_map:
        return 0

    seq_num = get_next_history_number(history_dir)
    classify_file = history_dir / f"{seq_num:03d}_classify.json"

    classifications = []
    for entry_id, (entry, file_path, category) in entry_map.items():
        # Insert document record
        cursor = conn.execute(
            """INSERT INTO documents (entry_id, name, category)
               VALUES (?, ?, ?)""",
            (entry_id, file_path.name, category),
        )
        document_id = cursor.lastrowid

        classifications.append(
            {
                "entry_id": entry_id,
                "document_id": document_id,
                "category": category,
                "name": file_path.name,
            }
        )

    # Write classification record
    classification_record = {
        "type": "classify",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": getpass.getuser(),
        "message": message,
        "classifications": classifications,
    }

    with open(classify_file, "w") as f:
        json.dump(classification_record, f, indent=2)

    conn.commit()

    return len(classifications)


def compute_tree_fingerprint_from_paths(paths: list[Path]) -> str:
    """Compute tree fingerprint from file paths before import.

    This allows duplicate detection before actually importing.

    Args:
        paths: List of file/directory paths to import

    Returns:
        Tree fingerprint hex string
    """
    # Expand paths to individual files
    files = expand_paths(paths)

    # Compute (relative_path, blob_hash) pairs
    pairs = []
    for file_path in files:
        # Compute relative path (same as import would do)
        rel_path = compute_relative_path(file_path, paths)
        # Compute blob hash
        blob_hash = compute_hash(file_path)
        pairs.append((str(rel_path), blob_hash))

    # Sort and hash (same as compute_tree_fingerprint)
    pairs.sort()
    sha256 = hashlib.sha256()
    for rel_path, blob_hash in pairs:
        sha256.update(f"{rel_path}:{blob_hash}\n".encode("utf-8"))

    return sha256.hexdigest()


def check_duplicate_drop(
    tree_fingerprint: str, conn: sqlite3.Connection
) -> dict | None:
    """Check if a drop with the same tree fingerprint exists.

    Args:
        tree_fingerprint: Tree fingerprint to check
        conn: Database connection

    Returns:
        Drop metadata dict if duplicate found, None otherwise
    """
    row = conn.execute(
        """SELECT id, message, actor, created_at
           FROM drops
           WHERE tree_fingerprint = ?
           ORDER BY created_at DESC
           LIMIT 1""",
        (tree_fingerprint,),
    ).fetchone()

    if row:
        return {
            "drop_id": row["id"],
            "message": row["message"],
            "actor": row["actor"],
            "created_at": row["created_at"],
        }

    return None


def drop_import(
    paths: list[Path],
    message: str,
    warehouse_root: Path,
    history_dir: Path,
    conn: sqlite3.Connection,
) -> Drop:
    """Import files and create a drop in history.

    Auto-classifies files that are imported from within the warehouse
    (excluding system directories).
    """
    from dwh.history import get_next_history_number

    # Generate drop ID and metadata
    drop_id = generate_drop_id()
    actor = getpass.getuser()
    seq_num = get_next_history_number(history_dir)

    # Create history folder
    drop_dir = history_dir / f"{seq_num:03d}_drop_{drop_id}"
    tree_dir = drop_dir / "tree"
    tree_dir.mkdir(parents=True)

    # Track in-tree files for auto-classification
    # Map: file_path -> (category, relative_path_in_tree)
    in_tree_files: dict[Path, tuple[str, Path]] = {}

    # Expand and copy files to tree/, preserving structure
    for file_path in expand_paths(paths):
        # Check if this file is in-tree (within warehouse, not in system dirs)
        if is_in_tree_path(file_path, warehouse_root):
            # Compute category from file's location in warehouse
            resolved_file = file_path.resolve()
            resolved_root = warehouse_root.resolve()
            rel_to_warehouse = resolved_file.relative_to(resolved_root)

            # Category is the parent directory path (or empty if at root)
            category = (
                str(rel_to_warehouse.parent)
                if rel_to_warehouse.parent != Path(".")
                else ""
            )

            # Store for later classification
            relative_path = compute_relative_path(file_path, paths)
            in_tree_files[file_path] = (category, relative_path)

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

    # Compute tree fingerprint
    tree_fingerprint = compute_tree_fingerprint(entries)

    # Auto-classify in-tree files
    auto_classified_count = 0
    if in_tree_files:
        # Build map of entry_id -> (entry, file_path, category)
        # Match entries by their relative path in tree
        entry_map: dict[str, tuple[Entry, Path, str]] = {}

        for entry in entries:
            # Check if this entry corresponds to an in-tree file
            entry_tree_path = Path(entry.relative_path)
            for file_path, (category, tree_rel_path) in in_tree_files.items():
                if entry_tree_path == tree_rel_path:
                    entry_map[entry.id] = (entry, file_path, category)
                    break

        # Create classifications
        auto_classified_count = create_classifications(
            entry_map, history_dir, conn, message="Auto-classify (in-tree import)"
        )

    return Drop(
        id=drop_id,
        message=message,
        actor=actor,
        created_at=receipt["created_at"],
        entries=entries,
        auto_classified_count=auto_classified_count,
        tree_fingerprint=tree_fingerprint,
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

    return [
        DropSummary(
            id=row["id"],
            message=row["message"],
            actor=row["actor"],
            created_at=row["created_at"],
            entry_count=row["entry_count"],
        )
        for row in rows
    ]


def drop_inspect(drop_id: str, conn: sqlite3.Connection) -> Drop:
    """Get full drop details."""
    drop_row = conn.execute("SELECT * FROM drops WHERE id = ?", (drop_id,)).fetchone()

    if not drop_row:
        raise DropNotFoundError(drop_id)

    # Join with blobs to get file sizes
    entries = conn.execute(
        """SELECT e.*, b.size
           FROM entries e
           JOIN blobs b ON e.blob_hash = b.hash
           WHERE e.drop_id = ?
           ORDER BY e.relative_path""",
        (drop_id,),
    ).fetchall()

    return Drop(
        id=drop_row["id"],
        message=drop_row["message"],
        actor=drop_row["actor"],
        created_at=drop_row["created_at"],
        entries=[
            Entry(
                id=e["id"],
                drop_id=e["drop_id"],
                blob_hash=e["blob_hash"],
                filename=e["filename"],
                relative_path=e["relative_path"],
                size=e["size"],
            )
            for e in entries
        ],
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
