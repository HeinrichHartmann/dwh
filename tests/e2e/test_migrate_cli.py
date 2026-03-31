"""E2E tests for migration CLI command."""

import sqlite3

from tests.conftest import run_cli


def test_migrate_command_on_fresh_warehouse(runner, tmp_warehouse):
    """Migration command on fresh warehouse shows already up to date."""
    result = run_cli(runner, ["migrate"])

    assert result.exit_code == 0
    assert "Current schema version: 2" in result.output
    assert "Target schema version: 2" in result.output
    assert "Database is up to date" in result.output


def test_migrate_command_upgrades_v1_to_v2(runner, tmp_warehouse):
    """Migration command upgrades v1 database to v2."""
    # Downgrade database to v1 by removing transformation tables
    db_path = tmp_warehouse / ".dwh" / "dwh.db"
    conn = sqlite3.connect(db_path)

    # Drop transformation tables and downgrade version
    conn.executescript("""
        DROP TABLE IF EXISTS transformation_inputs;
        DROP TABLE IF EXISTS transformations;
        DROP TABLE IF EXISTS transformation_state;
        DROP INDEX IF EXISTS idx_transformation_inputs_entry;
        UPDATE schema_version SET version = 1;
    """)
    conn.commit()
    conn.close()

    # Run migration
    result = run_cli(runner, ["migrate"])

    assert result.exit_code == 0
    assert "Current schema version: 1" in result.output
    assert "Target schema version: 2" in result.output
    assert "Applying migrations" in result.output
    assert "Migrated to version 2" in result.output
    assert "Migration complete" in result.output

    # Verify we can now use transform commands
    result = run_cli(runner, ["transform", "status"])
    assert result.exit_code == 0
    assert "No transformation active" in result.output


def test_transform_command_detects_outdated_schema(runner, tmp_warehouse, sample_files):
    """Transform commands detect outdated schema and suggest migration."""
    # Import a drop first
    result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
    drop_id = result.output.split("Drop ID: ")[1].split("\n")[0]

    # Downgrade database to v1
    db_path = tmp_warehouse / ".dwh" / "dwh.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        DROP TABLE IF EXISTS transformation_inputs;
        DROP TABLE IF EXISTS transformations;
        DROP TABLE IF EXISTS transformation_state;
        UPDATE schema_version SET version = 1;
    """)
    conn.commit()
    conn.close()

    # Try to start transformation
    result = run_cli(runner, ["transform", "start", f"drop:{drop_id}"])

    assert result.exit_code == 1
    assert "Database schema is outdated" in result.output
    assert "version 1, need 2" in result.output
    assert "dwh migrate" in result.output
