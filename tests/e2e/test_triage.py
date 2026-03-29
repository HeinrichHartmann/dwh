"""E2E tests for triage workflow.

Tests verify:
- Triage checkout copies files from drop to triage/
- User can move files to documents/
- Triage sync creates classifications and updates database
- Files are matched by hash
- Classification records are written to history
"""

import json

from tests.conftest import (
    run_cli,
    extract_drop_id,
)


class TestTriageCheckout:
    """Test triage checkout operation."""

    def test_triage_checkout_latest_drop(self, runner, tmp_warehouse, sample_files):
        """Checkout latest drop for triage."""
        # Import a drop
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)

        # Triage checkout
        result = run_cli(runner, ["triage"])

        assert result.exit_code == 0
        assert "Checked out drop" in result.output
        assert drop_id in result.output
        assert "4 files ready for triage" in result.output

    def test_triage_checkout_specific_drop(self, runner, tmp_warehouse, single_file):
        """Checkout specific drop by ID."""
        # Import a drop
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        drop_id = extract_drop_id(result.output)

        # Triage with specific drop_id
        result = run_cli(runner, ["triage", drop_id])

        assert result.exit_code == 0
        assert drop_id in result.output

    def test_triage_creates_triage_directory(self, runner, tmp_warehouse, sample_files):
        """Triage creates triage/ with files."""
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage"])

        triage_dir = tmp_warehouse / "triage"
        assert triage_dir.exists()
        assert (triage_dir / "file1.txt").exists()
        assert (triage_dir / "file2.txt").exists()
        assert (triage_dir / "subdir" / "nested.txt").exists()

    def test_triage_clears_existing_triage(self, runner, tmp_warehouse, single_file):
        """Triage clears existing triage/ directory."""
        # First triage
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["triage"])

        # Add a marker file
        marker = tmp_warehouse / "triage" / "marker.txt"
        marker.write_text("marker")

        # Import another drop and triage again
        run_cli(runner, ["drop", "import", "-m", "Second", str(single_file)])
        run_cli(runner, ["triage"])

        # Marker should be gone
        assert not marker.exists()

    def test_triage_without_drops_fails(self, runner, tmp_warehouse):
        """Triage without any drops should fail."""
        result = run_cli(runner, ["triage"])

        assert result.exit_code != 0
        assert "no drops" in result.output.lower() or "error" in result.output.lower()


class TestTriageSync:
    """Test triage sync operation."""

    def test_triage_sync_classifies_files(self, runner, tmp_warehouse, sample_files):
        """Sync creates classifications for moved files."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage"])

        # Move files to documents/
        documents_dir = tmp_warehouse / "documents"
        finance_dir = documents_dir / "finance"
        finance_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "triage"
        (triage_dir / "file1.txt").rename(finance_dir / "file1.txt")

        # Sync
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "Classified 1 files" in result.output

    def test_triage_sync_creates_classification_record(self, runner, tmp_warehouse, single_file):
        """Sync writes classification record to history."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage"])

        # Move file
        documents_dir = tmp_warehouse / "documents"
        finance_dir = documents_dir / "finance"
        finance_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "triage"
        (triage_dir / single_file.name).rename(finance_dir / single_file.name)

        # Sync
        run_cli(runner, ["triage", "sync"])

        # Check classification record exists
        history_dir = tmp_warehouse / ".dwh" / "history"
        classify_files = list(history_dir.glob("*_classify.json"))

        assert len(classify_files) == 1

        # Verify classification content
        classify_record = json.loads(classify_files[0].read_text())
        assert classify_record["type"] == "classify"
        assert classify_record["message"] == "Triage sync"
        assert len(classify_record["classifications"]) == 1

        classification = classify_record["classifications"][0]
        assert classification["category"] == "finance"
        assert classification["name"] == single_file.name

    def test_triage_sync_updates_database(self, runner, tmp_warehouse, single_file):
        """Sync creates document records in database."""
        import sqlite3

        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage"])

        # Move file
        documents_dir = tmp_warehouse / "documents"
        documents_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "triage"
        (triage_dir / single_file.name).rename(documents_dir / single_file.name)

        # Sync
        run_cli(runner, ["triage", "sync"])

        # Check database
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        documents = conn.execute("SELECT * FROM documents").fetchall()
        assert len(documents) == 1

        doc = documents[0]
        assert doc["name"] == single_file.name
        assert doc["category"] == ""

        conn.close()

    def test_triage_sync_clears_triage_directory(self, runner, tmp_warehouse, single_file):
        """Sync clears triage/ directory after completion."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage"])

        # Move file
        documents_dir = tmp_warehouse / "documents"
        documents_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "triage"
        (triage_dir / single_file.name).rename(documents_dir / single_file.name)

        # Sync
        run_cli(runner, ["triage", "sync"])

        # Triage should be cleared
        assert not triage_dir.exists()

    def test_triage_sync_without_checkout_fails(self, runner, tmp_warehouse):
        """Sync without checkout should fail."""
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code != 0
        assert "no triage in progress" in result.output.lower()

    def test_triage_sync_with_nested_categories(self, runner, tmp_warehouse, sample_files):
        """Sync handles nested category directories."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage"])

        # Move files to nested categories
        documents_dir = tmp_warehouse / "documents"
        finance_dir = documents_dir / "finance" / "taxes" / "2024"
        finance_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "triage"
        (triage_dir / "file1.txt").rename(finance_dir / "invoice.txt")

        # Sync
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0

        # Check classification
        history_dir = tmp_warehouse / ".dwh" / "history"
        classify_file = list(history_dir.glob("*_classify.json"))[0]
        classify_record = json.loads(classify_file.read_text())

        classification = classify_record["classifications"][0]
        assert classification["category"] == "finance/taxes/2024"
        assert classification["name"] == "invoice.txt"

    def test_triage_sync_skips_unclassified_files(self, runner, tmp_warehouse, sample_files):
        """Sync reports files left in triage/ as skipped."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage"])

        # Move only some files
        documents_dir = tmp_warehouse / "documents"
        documents_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "triage"
        (triage_dir / "file1.txt").rename(documents_dir / "file1.txt")
        # Leave file2.txt and others in triage/

        # Sync
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "Classified 1 files" in result.output
        assert "Skipped 3 files" in result.output


class TestTriageWorkflow:
    """Test complete triage workflows."""

    def test_complete_triage_workflow(self, runner, tmp_warehouse, sample_files):
        """Complete workflow: import → triage → organize → sync."""
        # 1. Import
        result = run_cli(runner, ["drop", "import", "-m", "Documents", str(sample_files)])
        assert result.exit_code == 0
        drop_id = extract_drop_id(result.output)

        # 2. Triage
        result = run_cli(runner, ["triage", drop_id])
        assert result.exit_code == 0

        triage_dir = tmp_warehouse / "triage"
        assert triage_dir.exists()

        # 3. User organizes files
        documents_dir = tmp_warehouse / "documents"
        (documents_dir / "work").mkdir(parents=True)
        (documents_dir / "personal").mkdir(parents=True)

        (triage_dir / "file1.txt").rename(documents_dir / "work" / "report.txt")
        (triage_dir / "file2.txt").rename(documents_dir / "personal" / "notes.txt")

        # 4. Sync
        result = run_cli(runner, ["triage", "sync"])
        assert result.exit_code == 0
        assert "Classified 2 files" in result.output

        # 5. Verify results
        assert not triage_dir.exists()

        # Check classification record
        history_dir = tmp_warehouse / ".dwh" / "history"
        classify_files = list(history_dir.glob("*_classify.json"))
        assert len(classify_files) == 1

        classify_record = json.loads(classify_files[0].read_text())
        assert len(classify_record["classifications"]) == 2

        # Check database
        import sqlite3
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        documents = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
        assert len(documents) == 2

        assert documents[0]["name"] == "report.txt"
        assert documents[0]["category"] == "work"

        assert documents[1]["name"] == "notes.txt"
        assert documents[1]["category"] == "personal"

        conn.close()

    def test_multiple_triage_cycles(self, runner, tmp_warehouse, single_file, tmp_path):
        """Multiple import-triage-sync cycles work independently."""
        # First cycle
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["triage"])

        documents_dir = tmp_warehouse / "documents"
        documents_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "triage"
        (triage_dir / single_file.name).rename(documents_dir / "first.txt")

        run_cli(runner, ["triage", "sync"])

        # Second cycle with different file
        second_file = tmp_path / "second.txt"
        second_file.write_text("second content")

        run_cli(runner, ["drop", "import", "-m", "Second", str(second_file)])
        run_cli(runner, ["triage"])

        triage_dir = tmp_warehouse / "triage"
        (triage_dir / "second.txt").rename(documents_dir / "second.txt")

        run_cli(runner, ["triage", "sync"])

        # Verify two classification records
        history_dir = tmp_warehouse / ".dwh" / "history"
        classify_files = sorted(history_dir.glob("*_classify.json"))
        assert len(classify_files) == 2

        # Verify two documents
        import sqlite3
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        documents = conn.execute("SELECT * FROM documents").fetchall()
        assert len(documents) == 2
        conn.close()
