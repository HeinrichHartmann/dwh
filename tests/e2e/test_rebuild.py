"""E2E tests for database rebuild."""

import sqlite3

from tests.conftest import run_cli, extract_drop_id


class TestRebuild:
    """Test database rebuild from history."""

    def test_rebuild_empty_warehouse(self, runner, tmp_warehouse):
        """Rebuild with no history should succeed."""
        result = run_cli(runner, ["rebuild"])

        assert result.exit_code == 0
        assert "0 drops" in result.output
        assert "0 classifications" in result.output

    def test_rebuild_after_import(self, runner, tmp_warehouse, sample_files):
        """Rebuild after import restores drops."""
        # Import files
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)

        # Delete database
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        db_path.unlink()

        # Rebuild
        result = run_cli(runner, ["rebuild"])

        assert result.exit_code == 0
        assert "1 drops" in result.output

        # Verify drop is in database
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        drops = conn.execute("SELECT * FROM drops WHERE id = ?", (drop_id,)).fetchone()
        assert drops is not None
        assert drops["message"] == "Test"

        # Verify entries are in database
        entries = conn.execute(
            "SELECT * FROM entries WHERE drop_id = ?", (drop_id,)
        ).fetchall()
        assert len(entries) == 4

        conn.close()

    def test_rebuild_after_triage(self, runner, tmp_warehouse, sample_files):
        """Rebuild after triage restores classifications."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # Move files (ADR-003: categories at root)
        docs_dir = tmp_warehouse / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / "file1.txt").rename(docs_dir / "file1.txt")

        # Sync
        run_cli(runner, ["triage", "sync"])

        # Delete database
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        db_path.unlink()

        # Rebuild
        result = run_cli(runner, ["rebuild"])

        assert result.exit_code == 0
        assert "1 drops" in result.output
        assert "1 classifications" in result.output

        # Verify document is in database
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        documents = conn.execute("SELECT * FROM documents").fetchall()
        assert len(documents) == 1
        assert documents[0]["name"] == "file1.txt"

        conn.close()

    def test_rebuild_preserves_file_sizes(self, runner, tmp_warehouse, single_file):
        """Rebuild preserves blob sizes."""
        # Import file
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        drop_id = extract_drop_id(result.output)

        # Get original size
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        original_size = conn.execute(
            "SELECT size FROM blobs WHERE hash IN (SELECT blob_hash FROM entries WHERE drop_id = ?)",
            (drop_id,),
        ).fetchone()["size"]

        conn.close()

        # Rebuild
        db_path.unlink()
        run_cli(runner, ["rebuild"])

        # Verify size is preserved
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        rebuilt_size = conn.execute(
            "SELECT size FROM blobs WHERE hash IN (SELECT blob_hash FROM entries WHERE drop_id = ?)",
            (drop_id,),
        ).fetchone()["size"]

        assert rebuilt_size == original_size
        conn.close()

    def test_rebuild_multiple_drops(self, runner, tmp_warehouse, single_file, tmp_path):
        """Rebuild with multiple drops."""
        # Import multiple drops
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])

        second_file = tmp_path / "second.txt"
        second_file.write_text("second content")
        run_cli(runner, ["drop", "import", "-m", "Second", str(second_file)])

        # Delete database
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        db_path.unlink()

        # Rebuild
        result = run_cli(runner, ["rebuild"])

        assert result.exit_code == 0
        assert "2 drops" in result.output

        # Verify both drops exist
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        drops = conn.execute("SELECT * FROM drops ORDER BY created_at").fetchall()
        assert len(drops) == 2
        assert drops[0]["message"] == "First"
        assert drops[1]["message"] == "Second"

        conn.close()

    def test_rebuild_idempotent(self, runner, tmp_warehouse, sample_files):
        """Rebuilding multiple times produces same result."""
        # Import files
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])

        # Rebuild twice
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        db_path.unlink()

        run_cli(runner, ["rebuild"])

        # Get state after first rebuild
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        entries_1 = conn.execute("SELECT * FROM entries ORDER BY id").fetchall()
        conn.close()

        # Rebuild again
        db_path.unlink()
        run_cli(runner, ["rebuild"])

        # Get state after second rebuild
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        entries_2 = conn.execute("SELECT * FROM entries ORDER BY id").fetchall()
        conn.close()

        # Should be identical
        assert len(entries_1) == len(entries_2)
        for e1, e2 in zip(entries_1, entries_2):
            assert e1["id"] == e2["id"]
            assert e1["blob_hash"] == e2["blob_hash"]
            assert e1["filename"] == e2["filename"]

    def test_rebuild_without_warehouse_fails(self, runner, tmp_path):
        """Rebuild outside warehouse should fail."""
        import os

        # Change to a directory without a warehouse
        orig_dir = os.getcwd()
        os.chdir(tmp_path)

        try:
            result = run_cli(runner, ["rebuild"])
            assert result.exit_code != 0
            assert "not in a warehouse" in result.output.lower()
        finally:
            os.chdir(orig_dir)
