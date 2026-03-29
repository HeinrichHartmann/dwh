"""Blob storage operations."""

import getpass
import hashlib
import mimetypes
import shutil
import sqlite3
import uuid
from pathlib import Path


def compute_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def get_blob_path(store_dir: Path, file_hash: str) -> Path:
    """Get the storage path for a blob based on its hash."""
    # Use first 2 chars as first dir, next 2 as second dir
    return store_dir / file_hash[:2] / file_hash[2:4] / file_hash


def store_blob(file_path: Path, store_dir: Path, conn: sqlite3.Connection) -> tuple[str, str]:
    """
    Store a file as a blob and create database records.

    Returns: (blob_hash, document_id)
    """
    # Compute hash
    file_hash = compute_hash(file_path)
    file_size = file_path.stat().st_size
    mime_type, _ = mimetypes.guess_type(file_path)

    # Check if blob already exists in DB
    cursor = conn.execute("SELECT hash FROM blobs WHERE hash = ?", (file_hash,))
    blob_exists = cursor.fetchone() is not None

    if not blob_exists:
        # Store blob in filesystem
        blob_path = get_blob_path(store_dir, file_hash)
        blob_path.parent.mkdir(parents=True, exist_ok=True)

        # Only copy if not already there (could happen if DB was reset)
        if not blob_path.exists():
            shutil.copy2(file_path, blob_path)

        # Insert blob record
        conn.execute(
            "INSERT INTO blobs (hash, size, mime_type) VALUES (?, ?, ?)",
            (file_hash, file_size, mime_type or "application/octet-stream")
        )

    # Always create a new document record (same blob can be ingested multiple times)
    document_id = str(uuid.uuid4())
    source = f"inbox/{file_path.name}"

    conn.execute(
        """INSERT INTO documents (id, blob_hash, original_name, source, state)
           VALUES (?, ?, ?, ?, 'stored')""",
        (document_id, file_hash, file_path.name, source)
    )

    conn.commit()
    return file_hash, document_id


def scan_inbox(inbox_dir: Path) -> list[Path]:
    """Scan inbox directory for files recursively."""
    if not inbox_dir.exists():
        return []

    files = []
    for item in inbox_dir.rglob("*"):
        if item.is_file() and not item.name.startswith('.'):
            files.append(item)

    return sorted(files)


def collect_files(paths: list[Path]) -> list[Path]:
    """
    Collect all files from given paths.

    Paths can be files or directories. Directories are scanned recursively.
    """
    files = []
    for path in paths:
        if not path.exists():
            raise ValueError(f"Path does not exist: {path}")

        if path.is_file():
            if not path.name.startswith('.'):
                files.append(path)
        elif path.is_dir():
            for item in path.rglob("*"):
                if item.is_file() and not item.name.startswith('.'):
                    files.append(item)

    return sorted(files)


def import_files(
    paths: list[Path],
    message: str,
    warehouse_root: Path,
    store_dir: Path,
    conn: sqlite3.Connection
) -> tuple[str, int]:
    """
    Import files with transaction tracking.

    Returns: (import_id, files_stored_count)
    """
    # Collect all files from paths
    files = collect_files(paths)

    if not files:
        raise ValueError("No files found to import")

    # Create import transaction
    import_id = str(uuid.uuid4())
    username = getpass.getuser()

    conn.execute(
        "INSERT INTO imports (id, message, username) VALUES (?, ?, ?)",
        (import_id, message, username)
    )

    # Store each file and link to import
    stored_count = 0
    for file_path in files:
        try:
            # Store blob and create document
            file_hash, document_id = store_blob(file_path, store_dir, conn)

            # Compute path relative to warehouse root
            try:
                rel_path = file_path.relative_to(warehouse_root)
            except ValueError:
                # If file is outside warehouse, use absolute path
                rel_path = file_path.resolve()

            # Link document to import
            conn.execute(
                "INSERT INTO import_files (import_id, document_id, original_path) VALUES (?, ?, ?)",
                (import_id, document_id, str(rel_path))
            )

            stored_count += 1
        except Exception as e:
            # Rollback on error
            conn.rollback()
            raise RuntimeError(f"Failed to import {file_path}: {e}") from e

    conn.commit()
    return import_id, stored_count
