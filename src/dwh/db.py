"""Database schema and operations for DWH."""

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE blobs (
    hash TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mime_type TEXT,
    stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE drops (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    tree_fingerprint TEXT
);

CREATE TABLE entries (
    id TEXT PRIMARY KEY,
    drop_id TEXT NOT NULL REFERENCES drops(id),
    blob_hash TEXT NOT NULL REFERENCES blobs(hash),
    filename TEXT NOT NULL,
    relative_path TEXT,
    source_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE REFERENCES entries(id),
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE triage_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    drop_id TEXT NOT NULL,
    checked_out_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE transformation_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    transformation_id TEXT NOT NULL,
    input_spec TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE transformations (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    input_spec TEXT NOT NULL,
    result_drop_id TEXT REFERENCES drops(id),
    result_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE transformation_inputs (
    transformation_id TEXT NOT NULL REFERENCES transformations(id),
    entry_id TEXT NOT NULL REFERENCES entries(id),
    input_path TEXT NOT NULL,
    PRIMARY KEY (transformation_id, entry_id)
);

CREATE INDEX idx_entries_drop_id ON entries(drop_id);
CREATE INDEX idx_entries_blob_hash ON entries(blob_hash);
CREATE INDEX idx_documents_entry_id ON documents(entry_id);
CREATE INDEX idx_documents_category ON documents(category);
CREATE INDEX idx_drops_tree_fingerprint ON drops(tree_fingerprint);
CREATE INDEX idx_transformation_inputs_entry ON transformation_inputs(entry_id);
"""


def init_db(db_path: Path) -> None:
    """Initialize database with schema."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Create schema
        conn.executescript(SCHEMA_SQL)

        # Set schema version
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
        )

        conn.commit()
    finally:
        conn.close()


def connect(db_path: Path) -> sqlite3.Connection:
    """Connect to database with row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    """Get current schema version."""
    try:
        result = conn.execute("SELECT version FROM schema_version").fetchone()
        return result["version"] if result else None
    except sqlite3.OperationalError:
        return None


def migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate database from version 1 to version 2.

    Adds transformation tables for provenance tracking.
    """
    conn.executescript("""
        CREATE TABLE transformation_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            transformation_id TEXT NOT NULL,
            input_spec TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE transformations (
            id TEXT PRIMARY KEY,
            message TEXT NOT NULL,
            actor TEXT NOT NULL,
            input_spec TEXT NOT NULL,
            result_drop_id TEXT REFERENCES drops(id),
            result_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE transformation_inputs (
            transformation_id TEXT NOT NULL REFERENCES transformations(id),
            entry_id TEXT NOT NULL REFERENCES entries(id),
            input_path TEXT NOT NULL,
            PRIMARY KEY (transformation_id, entry_id)
        );

        CREATE INDEX idx_transformation_inputs_entry ON transformation_inputs(entry_id);

        UPDATE schema_version SET version = 2;
    """)
    conn.commit()


MIGRATIONS = {
    1: migrate_v1_to_v2,
}


def migrate_database(conn: sqlite3.Connection) -> list[int]:
    """Run any pending migrations.

    Returns:
        List of version numbers that were applied
    """
    current_version = get_schema_version(conn)
    if current_version is None:
        raise RuntimeError("Database not initialized (no schema_version table)")

    applied = []
    while current_version < SCHEMA_VERSION:
        next_version = current_version + 1
        migration_fn = MIGRATIONS.get(current_version)

        if migration_fn is None:
            raise RuntimeError(
                f"No migration from version {current_version} to {next_version}"
            )

        migration_fn(conn)
        applied.append(next_version)
        current_version = next_version

    return applied


def needs_migration(conn: sqlite3.Connection) -> bool:
    """Check if database needs migration."""
    current_version = get_schema_version(conn)
    return current_version is not None and current_version < SCHEMA_VERSION
