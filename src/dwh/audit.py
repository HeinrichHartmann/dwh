"""Warehouse audit operations."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dwh import drop as drop_module


class AuditError(Exception):
    """Base exception for audit operations."""

    pass


@dataclass
class OrphanedFile:
    """A file in warehouse but not tracked in database."""

    path: str
    hash: str
    size: int


@dataclass
class MissingFile:
    """A document in database but file not found on disk."""

    document_id: int
    entry_id: str
    path: str
    hash: str


@dataclass
class RelocatedFile:
    """A file moved from its recorded location."""

    document_id: int
    entry_id: str
    expected_path: str
    actual_path: str
    hash: str


@dataclass
class DuplicateBlob:
    """Same blob content in multiple locations."""

    hash: str
    locations: list[tuple[str, int]]  # [(path, document_id), ...]


@dataclass
class AuditResult:
    """Result of warehouse audit."""

    orphans: list[OrphanedFile]
    missing: list[MissingFile]
    relocated: list[RelocatedFile]
    duplicates: list[DuplicateBlob]
    total_files: int
    total_documents: int


def audit_warehouse(
    warehouse_root: Path, audit_path: Path, conn: sqlite3.Connection
) -> AuditResult:
    """Audit warehouse filesystem consistency.

    Args:
        warehouse_root: Warehouse root directory
        audit_path: Path to audit (relative to warehouse_root)
        conn: Database connection

    Returns:
        AuditResult with orphans, missing, relocated, duplicates
    """
    # Resolve audit path
    full_audit_path = warehouse_root / audit_path
    if not full_audit_path.exists():
        raise AuditError(f"Path not found: {audit_path}")

    # Scan filesystem (skip system dirs)
    system_dirs = {".dwh", "_history", "_triage", "_staging", ".dwh_test_config"}
    fs_files = {}  # {rel_path: hash}
    hash_locations = {}  # {hash: [paths]}

    for item in full_audit_path.rglob("*"):
        if item.is_file():
            # Check if any parent is a system dir
            rel_to_root = item.relative_to(warehouse_root)
            if any(part in system_dirs for part in rel_to_root.parts):
                continue

            file_hash = drop_module.compute_hash(item)
            rel_path = str(rel_to_root)
            fs_files[rel_path] = file_hash

            if file_hash not in hash_locations:
                hash_locations[file_hash] = []
            hash_locations[file_hash].append(rel_path)

    # Get all documents in audit scope
    if audit_path == Path("."):
        # Audit entire warehouse
        docs = conn.execute(
            """
            SELECT d.id, d.entry_id, d.name, d.category, e.blob_hash
            FROM documents d
            JOIN entries e ON d.entry_id = e.id
            WHERE d.category != ''
        """
        ).fetchall()
    else:
        # Audit specific subtree
        prefix = str(audit_path)
        docs = conn.execute(
            """
            SELECT d.id, d.entry_id, d.name, d.category, e.blob_hash
            FROM documents d
            JOIN entries e ON d.entry_id = e.id
            WHERE d.category != '' AND (d.category = ? OR d.category LIKE ?)
        """,
            (prefix, f"{prefix}/%"),
        ).fetchall()

    # Check results
    orphans = []
    missing = []
    relocated = []
    db_hashes = set()

    # Check each document
    for doc in docs:
        expected_path = f"{doc['category']}/{doc['name']}"
        doc_hash = doc["blob_hash"]
        db_hashes.add(doc_hash)

        if expected_path in fs_files:
            # File exists at expected location
            if fs_files[expected_path] != doc_hash:
                # Content mismatch - shouldn't happen but skip for now
                pass
        else:
            # File not at expected location
            if doc_hash in hash_locations:
                # File exists but in wrong location (relocated)
                actual_paths = hash_locations[doc_hash]
                for actual_path in actual_paths:
                    relocated.append(
                        RelocatedFile(
                            document_id=doc["id"],
                            entry_id=doc["entry_id"],
                            expected_path=expected_path,
                            actual_path=actual_path,
                            hash=doc_hash,
                        )
                    )
            else:
                # File completely missing
                missing.append(
                    MissingFile(
                        document_id=doc["id"],
                        entry_id=doc["entry_id"],
                        path=expected_path,
                        hash=doc_hash,
                    )
                )

    # Check for orphans
    for path, file_hash in fs_files.items():
        if file_hash not in db_hashes:
            file_path = warehouse_root / path
            orphans.append(
                OrphanedFile(path=path, hash=file_hash, size=file_path.stat().st_size)
            )

    # Check for duplicates
    duplicates = []
    for file_hash, locations in hash_locations.items():
        if len(locations) > 1:
            # Get document info for each location
            docs_for_hash = [doc for doc in docs if doc["blob_hash"] == file_hash]

            # Build location list with document IDs
            location_info = []
            for loc in locations:
                # Find document at this location
                doc_at_loc = None
                for doc in docs_for_hash:
                    if f"{doc['category']}/{doc['name']}" == loc:
                        doc_at_loc = doc
                        break

                if doc_at_loc:
                    location_info.append((loc, doc_at_loc["id"]))
                else:
                    # Orphaned duplicate
                    location_info.append((loc, None))

            duplicates.append(DuplicateBlob(hash=file_hash, locations=location_info))

    return AuditResult(
        orphans=orphans,
        missing=missing,
        relocated=relocated,
        duplicates=duplicates,
        total_files=len(fs_files),
        total_documents=len(docs),
    )
