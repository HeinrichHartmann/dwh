"""Database schema and operations for DWH."""

import sqlite3
from pathlib import Path


SCHEMA_VERSION = 1

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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

CREATE INDEX idx_entries_drop_id ON entries(drop_id);
CREATE INDEX idx_entries_blob_hash ON entries(blob_hash);
CREATE INDEX idx_documents_entry_id ON documents(entry_id);
CREATE INDEX idx_documents_category ON documents(category);
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
