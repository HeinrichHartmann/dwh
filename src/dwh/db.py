"""Database schema and operations."""

import sqlite3
from pathlib import Path
from typing import Optional


SCHEMA = """
-- Blobs: Immutable file content
CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mime_type TEXT,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Documents: Logical document records
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    blob_hash TEXT NOT NULL REFERENCES blobs(hash),
    original_name TEXT NOT NULL,
    source TEXT,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    state TEXT DEFAULT 'stored' CHECK(state IN ('stored', 'classified', 'published'))
);

-- Classifications: Semantic metadata
CREATE TABLE IF NOT EXISTS classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id),
    domain TEXT,
    kind TEXT,
    counterparty TEXT,
    year INTEGER,
    period_start DATE,
    period_end DATE,
    tags TEXT,  -- JSON array
    confidence REAL DEFAULT 1.0 CHECK(confidence BETWEEN 0.0 AND 1.0),
    reviewed_at TIMESTAMP,
    reviewed_by TEXT CHECK(reviewed_by IN ('human', 'auto')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Placements: Filesystem projection mapping
CREATE TABLE IF NOT EXISTS placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id),
    path TEXT NOT NULL,
    is_primary BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_id, path)
);

-- Imports: Transaction records for file imports
CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    username TEXT NOT NULL,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Import files: Links documents to import transactions
CREATE TABLE IF NOT EXISTS import_files (
    import_id TEXT NOT NULL REFERENCES imports(id),
    document_id TEXT NOT NULL REFERENCES documents(id),
    original_path TEXT NOT NULL,
    PRIMARY KEY (import_id, document_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_documents_blob_hash ON documents(blob_hash);
CREATE INDEX IF NOT EXISTS idx_documents_state ON documents(state);
CREATE INDEX IF NOT EXISTS idx_classifications_document_id ON classifications(document_id);
CREATE INDEX IF NOT EXISTS idx_classifications_domain ON classifications(domain);
CREATE INDEX IF NOT EXISTS idx_classifications_year ON classifications(year);
CREATE INDEX IF NOT EXISTS idx_placements_document_id ON placements(document_id);
CREATE INDEX IF NOT EXISTS idx_placements_path ON placements(path);
CREATE INDEX IF NOT EXISTS idx_imports_imported_at ON imports(imported_at);
CREATE INDEX IF NOT EXISTS idx_import_files_import_id ON import_files(import_id);
CREATE INDEX IF NOT EXISTS idx_import_files_document_id ON import_files(document_id);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize database with schema."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get database connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn
