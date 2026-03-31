"""Tests for database schema migrations."""

import sqlite3
from pathlib import Path

from dwh import db


def test_v1_to_v2_migration(tmp_path: Path):
    """Test migration from schema v1 to v2."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Create v1 schema (without transformation tables)
    conn.executescript("""
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

        CREATE INDEX idx_entries_drop_id ON entries(drop_id);
        CREATE INDEX idx_entries_blob_hash ON entries(blob_hash);
        CREATE INDEX idx_documents_entry_id ON documents(entry_id);
        CREATE INDEX idx_documents_category ON documents(category);
        CREATE INDEX idx_drops_tree_fingerprint ON drops(tree_fingerprint);

        INSERT INTO schema_version (version) VALUES (1);
    """)
    conn.commit()

    # Verify we're on v1
    assert db.get_schema_version(conn) == 1

    # Verify transformation tables don't exist
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='transformations'"
    ).fetchone()
    assert result is None

    # Check if migration is needed
    assert db.needs_migration(conn) is True

    # Run migration
    applied = db.migrate_database(conn)

    # Verify migration applied
    assert applied == [2]
    assert db.get_schema_version(conn) == 2

    # Verify transformation tables now exist
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {row["name"] for row in tables}

    assert "transformation_state" in table_names
    assert "transformations" in table_names
    assert "transformation_inputs" in table_names

    # Verify we can use the new tables
    conn.execute(
        """INSERT INTO transformations
           (id, message, actor, input_spec, result_type)
           VALUES ('t_test', 'test', 'user', 'drop:d_test', 'import')"""
    )
    conn.commit()

    result = conn.execute("SELECT * FROM transformations WHERE id = 't_test'").fetchone()
    assert result["message"] == "test"

    conn.close()


def test_migration_already_up_to_date(tmp_path: Path):
    """Test migration when database is already up to date."""
    db_path = tmp_path / "test.db"

    # Initialize with current schema
    db.init_db(db_path)

    conn = db.connect(db_path)

    # Verify we're already at latest version
    assert db.get_schema_version(conn) == db.SCHEMA_VERSION
    assert db.needs_migration(conn) is False

    # Migration should return empty list
    applied = db.migrate_database(conn)
    assert applied == []

    conn.close()


def test_migration_idempotent(tmp_path: Path):
    """Test that running migration twice is safe."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Create v1 schema
    conn.executescript("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        CREATE TABLE blobs (hash TEXT PRIMARY KEY, size INTEGER NOT NULL);
        CREATE TABLE drops (id TEXT PRIMARY KEY, message TEXT NOT NULL, actor TEXT NOT NULL);
        CREATE TABLE entries (
            id TEXT PRIMARY KEY,
            drop_id TEXT NOT NULL,
            blob_hash TEXT NOT NULL,
            filename TEXT NOT NULL,
            relative_path TEXT,
            source_path TEXT NOT NULL
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE triage_state (id INTEGER PRIMARY KEY CHECK(id = 1), drop_id TEXT NOT NULL);
        INSERT INTO schema_version (version) VALUES (1);
    """)
    conn.commit()

    # First migration
    db.migrate_database(conn)
    assert db.get_schema_version(conn) == 2

    # Second migration (should be no-op)
    applied = db.migrate_database(conn)
    assert applied == []
    assert db.get_schema_version(conn) == 2

    conn.close()
