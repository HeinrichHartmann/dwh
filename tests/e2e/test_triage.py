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
        result = run_cli(runner, ["triage", "checkout"])

        assert result.exit_code == 0
        assert "Checking out:" in result.output or "Checked out" in result.output
        assert drop_id in result.output
        assert "4 files" in result.output

    def test_triage_checkout_specific_drop(self, runner, tmp_warehouse, single_file):
        """Checkout specific drop by ID."""
        # Import a drop
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        drop_id = extract_drop_id(result.output)

        # Triage with specific drop_id
        result = run_cli(runner, ["triage", "checkout", drop_id])

        assert result.exit_code == 0
        assert drop_id in result.output

    def test_triage_creates_triage_directory(self, runner, tmp_warehouse, sample_files):
        """Triage creates triage/ with files."""
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        assert triage_dir.exists()
        assert (triage_dir / "file1.txt").exists()
        assert (triage_dir / "file2.txt").exists()
        assert (triage_dir / "subdir" / "nested.txt").exists()

    def test_triage_clears_existing_triage(self, runner, tmp_warehouse, single_file):
        """Triage clears existing triage/ directory."""
        # First triage
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # Add a marker file
        marker = tmp_warehouse / "_triage" / "marker.txt"
        marker.write_text("marker")

        # Import another drop and triage again (explicit checkout to bypass resume)
        result = run_cli(
            runner, ["drop", "import", "-m", "Second", str(single_file)], input="y\n"
        )
        drop_id = extract_drop_id(result.output)
        run_cli(runner, ["triage", "checkout", drop_id], input="y\n")  # Confirm switch

        # Marker should be gone
        assert not marker.exists()

    def test_triage_without_drops_fails(self, runner, tmp_warehouse):
        """Triage without any drops shows queue clear message."""
        result = run_cli(runner, ["triage", "checkout"])

        assert result.exit_code == 0
        assert "All drops triaged!" in result.output or "Queue clear" in result.output


class TestTriageSync:
    """Test triage sync operation."""

    def test_triage_sync_classifies_files(self, runner, tmp_warehouse, sample_files):
        """Sync creates classifications for moved files."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # Move files to documents/
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / "file1.txt").rename(finance_dir / "file1.txt")

        # Sync
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "Filed: 1 entries" in result.output

    def test_triage_sync_creates_classification_record(
        self, runner, tmp_warehouse, single_file
    ):
        """Sync writes classification record to history."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # Move file
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / single_file.name).rename(finance_dir / single_file.name)

        # Sync
        run_cli(runner, ["triage", "sync"])

        # Check classification record exists
        history_dir = tmp_warehouse / "_history"
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
        run_cli(runner, ["triage", "checkout"])

        # Move file to category (ADR-003: files must be in category dirs)
        docs_cat = tmp_warehouse / "docs"
        docs_cat.mkdir(parents=True)

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / single_file.name).rename(docs_cat / single_file.name)

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
        assert doc["category"] == "docs"

        conn.close()

    def test_triage_sync_clears_triage_directory(
        self, runner, tmp_warehouse, single_file
    ):
        """Sync clears triage/ directory after completion."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # Move file

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / single_file.name).rename(tmp_warehouse / single_file.name)

        # Sync
        run_cli(runner, ["triage", "sync"])

        # Triage should be cleared
        assert not triage_dir.exists()

    def test_triage_sync_without_checkout_fails(self, runner, tmp_warehouse):
        """Sync without checkout should fail."""
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code != 0
        assert "no triage in progress" in result.output.lower()

    def test_triage_sync_with_nested_categories(
        self, runner, tmp_warehouse, sample_files
    ):
        """Sync handles nested category directories."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # Move files to nested categories
        finance_dir = tmp_warehouse / "finance" / "taxes" / "2024"
        finance_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / "file1.txt").rename(finance_dir / "invoice.txt")

        # Sync
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0

        # Check classification
        history_dir = tmp_warehouse / "_history"
        classify_file = list(history_dir.glob("*_classify.json"))[0]
        classify_record = json.loads(classify_file.read_text())

        classification = classify_record["classifications"][0]
        assert classification["category"] == "finance/taxes/2024"
        assert classification["name"] == "invoice.txt"

    def test_triage_sync_skips_unclassified_files(
        self, runner, tmp_warehouse, sample_files
    ):
        """Sync reports files left in triage/ as skipped."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # Move only some files
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / "file1.txt").rename(finance_dir / "file1.txt")
        # Leave file2.txt and others in triage/

        # Sync
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "Filed: 1 entries" in result.output
        assert "3 entries remain in _triage/" in result.output


class TestTriageWorkflow:
    """Test complete triage workflows."""

    def test_complete_triage_workflow(self, runner, tmp_warehouse, sample_files):
        """Complete workflow: import → triage → organize → sync."""
        # 1. Import
        result = run_cli(
            runner, ["drop", "import", "-m", "Documents", str(sample_files)]
        )
        assert result.exit_code == 0
        drop_id = extract_drop_id(result.output)

        # 2. Triage
        result = run_cli(runner, ["triage", "checkout", drop_id])
        assert result.exit_code == 0

        triage_dir = tmp_warehouse / "_triage"
        assert triage_dir.exists()

        # 3. User organizes files (move all 4 files)
        (tmp_warehouse / "work").mkdir(parents=True)
        (tmp_warehouse / "personal").mkdir(parents=True)

        (triage_dir / "file1.txt").rename(tmp_warehouse / "work" / "report.txt")
        (triage_dir / "file2.txt").rename(tmp_warehouse / "personal" / "notes.txt")
        (triage_dir / "subdir" / "nested.txt").rename(
            tmp_warehouse / "work" / "nested.txt"
        )
        (triage_dir / "subdir" / "deeper" / "deep.txt").rename(
            tmp_warehouse / "personal" / "deep.txt"
        )

        # 4. Sync
        result = run_cli(runner, ["triage", "sync"])
        assert result.exit_code == 0
        assert "Filed: 4 entries" in result.output

        # 5. Verify results
        assert not triage_dir.exists()

        # Check classification record
        history_dir = tmp_warehouse / "_history"
        classify_files = list(history_dir.glob("*_classify.json"))
        assert len(classify_files) == 1

        classify_record = json.loads(classify_files[0].read_text())
        assert len(classify_record["classifications"]) == 4

        # Check database
        import sqlite3

        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        documents = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
        assert len(documents) == 4

        # Check all documents exist (order-independent)
        doc_map = {doc["name"]: doc["category"] for doc in documents}
        assert doc_map["report.txt"] == "work"
        assert doc_map["notes.txt"] == "personal"
        assert doc_map["nested.txt"] == "work"
        assert doc_map["deep.txt"] == "personal"

        conn.close()

    def test_multiple_triage_cycles(self, runner, tmp_warehouse, single_file, tmp_path):
        """Multiple import-triage-sync cycles work independently."""
        # First cycle
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        docs_dir = tmp_warehouse / "docs"
        docs_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / single_file.name).rename(docs_dir / "first.txt")

        run_cli(runner, ["triage", "sync"])

        # Second cycle with different file (create outside warehouse)
        second_file = tmp_path.parent / "second.txt"
        second_file.write_text("second content")

        run_cli(runner, ["drop", "import", "-m", "Second", str(second_file)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / second_file.name).rename(docs_dir / "second.txt")

        run_cli(runner, ["triage", "sync"])

        # Verify two classification records
        history_dir = tmp_warehouse / "_history"
        classify_files = sorted(history_dir.glob("*_classify.json"))
        assert len(classify_files) == 2

        # Verify two documents
        import sqlite3

        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        documents = conn.execute("SELECT * FROM documents").fetchall()
        assert len(documents) == 2
        conn.close()


class TestTriageSkippedFiles:
    """Test triage sync behavior when files are skipped (Issue #1)."""

    def test_sync_preserves_triage_when_files_skipped(
        self, runner, tmp_warehouse, single_file
    ):
        """Triage sync preserves _triage/ directory when files are skipped."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # Don't move the file (simulate user mistake)
        triage_dir = tmp_warehouse / "_triage"
        assert triage_dir.exists()
        assert (triage_dir / single_file.name).exists()

        # Sync (should skip the file)
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "1 entries remain in _triage/" in result.output

        # Triage directory should still exist (not deleted)
        assert triage_dir.exists()
        assert (triage_dir / single_file.name).exists()

        # Triage state should be preserved
        import sqlite3

        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        state = conn.execute("SELECT * FROM triage_state").fetchone()
        assert state is not None
        conn.close()

    def test_sync_clears_triage_when_all_classified(
        self, runner, tmp_warehouse, single_file
    ):
        """Triage sync clears _triage/ when all files are classified."""
        # Import and triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # Move file to category
        docs_dir = tmp_warehouse / "docs"
        docs_dir.mkdir(parents=True)

        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / single_file.name).rename(docs_dir / single_file.name)

        # Sync
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "Filed: 1 entries" in result.output

        # Triage directory should be deleted
        assert not triage_dir.exists()

        # Triage state should be cleared
        import sqlite3

        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        state = conn.execute("SELECT * FROM triage_state").fetchone()
        assert state is None
        conn.close()


class TestTriageSuggestMerge:
    """Test auto-triage suggest/merge workflow (ADR-005)."""

    def test_suggest_with_no_known_files(self, runner, tmp_warehouse, single_file):
        """Suggest with no known files leaves all in triage."""
        # Import and triage (first time - no classifications exist)
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # Run suggest
        result = run_cli(runner, ["triage", "suggest"])

        assert result.exit_code == 0
        assert "1 files remain in _triage/" in result.output

        # File should still be in triage
        triage_dir = tmp_warehouse / "_triage"
        assert (triage_dir / single_file.name).exists()

        # Staging should be empty
        staging_dir = tmp_warehouse / "_staging"
        assert staging_dir.exists()
        staging_files = list(staging_dir.rglob("*"))
        assert len([f for f in staging_files if f.is_file()]) == 0

    def test_suggest_with_known_files(self, runner, tmp_warehouse, single_file):
        """Suggest moves known files to staging."""
        # First: classify a file manually
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # Manually classify
        docs_dir = tmp_warehouse / "docs"
        docs_dir.mkdir(parents=True)
        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / single_file.name).rename(docs_dir / single_file.name)
        run_cli(runner, ["triage", "sync"])

        # Second: import same file again
        run_cli(
            runner, ["drop", "import", "-m", "Second", str(single_file)], input="y\n"
        )
        run_cli(runner, ["triage", "checkout"])

        # Run suggest - should auto-classify
        result = run_cli(runner, ["triage", "suggest"])

        assert result.exit_code == 0
        assert "1 files auto-classified" in result.output
        assert "docs/" in result.output
        assert "1 files staged in _staging/" in result.output

        # File should be in staging
        staging_dir = tmp_warehouse / "_staging"
        assert (staging_dir / "docs" / single_file.name).exists()

        # File should not be in triage
        triage_dir = tmp_warehouse / "_triage"
        assert not (triage_dir / single_file.name).exists()

    def test_merge_creates_classifications(self, runner, tmp_warehouse, single_file):
        """Merge moves files from staging to warehouse and creates classifications."""
        # Setup: First classify manually
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["triage", "checkout"])
        docs_dir = tmp_warehouse / "docs"
        docs_dir.mkdir(parents=True)
        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / single_file.name).rename(docs_dir / single_file.name)
        run_cli(runner, ["triage", "sync"])

        # Import again and suggest
        run_cli(
            runner, ["drop", "import", "-m", "Second", str(single_file)], input="y\n"
        )
        run_cli(runner, ["triage", "checkout"])
        run_cli(runner, ["triage", "suggest"])

        # Now merge
        result = run_cli(runner, ["triage", "merge"])

        assert result.exit_code == 0
        assert "Merged 1 files" in result.output
        assert "docs/: 1 files" in result.output
        assert "Classification records written" in result.output

        # File should be in warehouse
        assert (docs_dir / single_file.name).exists()

        # Staging should be cleared
        staging_dir = tmp_warehouse / "_staging"
        assert not staging_dir.exists()

        # Check database has classification
        import sqlite3

        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        documents = conn.execute("SELECT * FROM documents").fetchall()
        # Should have 2: original manual classification + merged one
        assert len(documents) == 2
        conn.close()

    def test_suggest_merge_complete_workflow(self, runner, tmp_warehouse, sample_files):
        """Complete suggest/merge workflow with mixed known/unknown files."""
        import shutil

        # First: classify some files
        run_cli(runner, ["drop", "import", "-m", "First", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # Classify 2 files manually, delete the others to complete triage
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        triage_dir = tmp_warehouse / "_triage"

        (triage_dir / "file1.txt").rename(finance_dir / "file1.txt")
        (triage_dir / "file2.txt").rename(finance_dir / "file2.txt")
        # Delete the other files to complete the first triage
        (triage_dir / "subdir" / "nested.txt").unlink()
        (triage_dir / "subdir" / "deeper" / "deep.txt").unlink()
        run_cli(runner, ["triage", "sync"])

        # Second: import all files again (including newly classified ones)
        # Copy sample_files to a new location to avoid duplicate warning
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            copied_samples = shutil.copytree(sample_files, tmpdir + "/samples")
            result = run_cli(
                runner,
                ["drop", "import", "-m", "Second", str(copied_samples)],
                input="y\n",  # Confirm duplicate import
            )

        run_cli(runner, ["triage", "checkout"])

        # Suggest - should auto-classify all 4 files (2 to finance, 2 to tombstone)
        result = run_cli(runner, ["triage", "suggest"])

        assert result.exit_code == 0
        assert "4 files auto-classified" in result.output
        assert "2 to finance/" in result.output
        assert "2 to /" in result.output  # Tombstoned files (empty category)

        # Merge staged files
        result = run_cli(runner, ["triage", "merge"])

        assert result.exit_code == 0
        assert "Merged 4 files" in result.output

        # Check files are in warehouse - only the finance files should be visible
        assert (finance_dir / "file1.txt").exists()
        assert (finance_dir / "file2.txt").exists()

        # Triage should be clear (all files auto-classified)
        triage_dir = tmp_warehouse / "_triage"
        if triage_dir.exists():
            remaining = list(triage_dir.rglob("*.txt"))
            assert len(remaining) == 0

    def test_suggest_without_triage_fails(self, runner, tmp_warehouse):
        """Suggest without active triage should fail."""
        result = run_cli(runner, ["triage", "suggest"])

        assert result.exit_code != 0
        assert "no triage in progress" in result.output.lower()

    def test_merge_without_staging_succeeds(self, runner, tmp_warehouse, single_file):
        """Merge with empty staging should succeed with no-op."""
        # Setup triage but don't suggest
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # Merge with no staging
        result = run_cli(runner, ["triage", "merge"])

        assert result.exit_code == 0
        assert "No files in staging" in result.output


class TestTriageTombstones:
    """Test tombstone classification for excluded entries (ADR-006)."""

    def test_deleted_file_creates_tombstone(self, runner, tmp_warehouse, sample_files):
        """Deleting file from triage creates tombstone document."""
        # Import and checkout
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)
        run_cli(runner, ["triage", "checkout"])

        # Delete a file from triage (exclude it)
        triage_dir = tmp_warehouse / "_triage"
        (triage_dir / "file1.txt").unlink()

        # Sync should create tombstone
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "Excluded: 1 entries" in result.output
        assert "3 entries remain in _triage/" in result.output

        # Check database has tombstone document (category = '')
        from dwh import db

        conn = db.connect(tmp_warehouse / ".dwh" / "dwh.db")
        doc = conn.execute(
            """SELECT d.* FROM documents d
               JOIN entries e ON d.entry_id = e.id
               WHERE e.drop_id = ? AND d.name = 'file1.txt'""",
            (drop_id,),
        ).fetchone()

        assert doc is not None
        assert doc["category"] == ""  # Tombstone marker
        assert doc["name"] == "file1.txt"

        conn.close()

    def test_filed_and_excluded_in_same_sync(self, runner, tmp_warehouse, sample_files):
        """Can file some entries and exclude others in same sync."""
        # Import and checkout
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # File one, exclude one
        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)

        (triage_dir / "file1.txt").rename(finance_dir / "invoice.txt")
        (triage_dir / "file2.txt").unlink()  # Exclude

        # Sync
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "Filed: 1 entries" in result.output
        assert "Excluded: 1 entries" in result.output
        assert "2 entries remain" in result.output

    def test_excluded_file_counts_as_classified(
        self, runner, tmp_warehouse, sample_files
    ):
        """Excluded entries count toward drop completion."""
        # Import and checkout
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # Delete all files (exclude all)
        triage_dir = tmp_warehouse / "_triage"
        for file in triage_dir.rglob("*.txt"):
            file.unlink()

        # Sync should complete drop
        result = run_cli(runner, ["triage", "sync"])

        assert result.exit_code == 0
        assert "Excluded: 4 entries" in result.output
        assert "Triage complete!" in result.output

        # Triage directory should be cleared
        assert not triage_dir.exists()


class TestTriageQueue:
    """Test triage queue logic (ADR-006)."""

    def test_checkout_without_args_resumes(self, runner, tmp_warehouse, sample_files):
        """Checkout without args resumes in-progress triage."""
        # Import and checkout
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # File one entry
        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (triage_dir / "file1.txt").rename(finance_dir / "invoice.txt")

        run_cli(runner, ["triage", "sync"])

        # Checkout again without args - should resume
        result = run_cli(runner, ["triage", "checkout"])

        assert result.exit_code == 0
        assert "Resuming:" in result.output
        assert "3 entries remain" in result.output
        assert "1 already classified" in result.output

    def test_checkout_without_args_picks_next_from_queue(
        self, runner, tmp_warehouse, sample_files
    ):
        """Checkout without args picks next incomplete drop from LIFO queue."""
        import tempfile
        import shutil

        # Import two drops
        run_cli(runner, ["drop", "import", "-m", "First drop", str(sample_files)])

        with tempfile.TemporaryDirectory() as tmpdir:
            copied_samples = shutil.copytree(sample_files, tmpdir + "/samples")
            run_cli(
                runner,
                ["drop", "import", "-m", "Second drop", str(copied_samples)],
                input="y\n",
            )

        # Checkout should pick newest (Second drop)
        result = run_cli(runner, ["triage", "checkout"])

        assert result.exit_code == 0
        assert "Checking out:" in result.output
        assert "Second drop" in result.output

    def test_checkout_all_complete_shows_clear_message(
        self, runner, tmp_warehouse, single_file
    ):
        """Checkout when all drops complete shows queue clear message."""
        # Import and complete triage
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (triage_dir / single_file.name).rename(finance_dir / "doc.txt")

        run_cli(runner, ["triage", "sync"])

        # Try checkout again - queue should be clear
        result = run_cli(runner, ["triage", "checkout"])

        assert result.exit_code == 0
        assert "All drops triaged!" in result.output
        assert "Queue clear" in result.output

    def test_checkout_with_explicit_id_shows_safety_warning(
        self, runner, tmp_warehouse, sample_files
    ):
        """Checkout with explicit drop_id shows safety warning if triage in progress."""
        import tempfile
        import shutil

        # Import two drops
        result = run_cli(runner, ["drop", "import", "-m", "First", str(sample_files)])
        first_drop_id = extract_drop_id(result.output)

        with tempfile.TemporaryDirectory() as tmpdir:
            copied_samples = shutil.copytree(sample_files, tmpdir + "/samples")
            run_cli(
                runner,
                ["drop", "import", "-m", "Second", str(copied_samples)],
                input="y\n",
            )

        # Checkout second (newest)
        run_cli(runner, ["triage", "checkout"])

        # Try to checkout first explicitly - should show warning
        result = run_cli(
            runner, ["triage", "checkout", first_drop_id], input="n\n"
        )  # Decline

        assert result.exit_code == 0
        assert "Triage in progress:" in result.output
        assert "entries remain in _triage/" in result.output
        assert "Cancelled" in result.output

    def test_checkout_force_restarts_from_scratch(
        self, runner, tmp_warehouse, sample_files
    ):
        """Checkout --force restarts current drop from scratch."""
        # Import and checkout
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # File one entry
        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (triage_dir / "file1.txt").rename(finance_dir / "invoice.txt")

        run_cli(runner, ["triage", "sync"])

        # Force restart
        result = run_cli(runner, ["triage", "checkout", "--force"])

        assert result.exit_code == 0
        assert "Discarding triage progress" in result.output
        assert "1/4 entries already classified" in result.output
        assert "Checked out 4 files" in result.output

        # All files should be back in triage
        assert (triage_dir / "file1.txt").exists()


class TestTriageStatus:
    """Test triage status command (ADR-006)."""

    def test_status_shows_single_pending_drop(
        self, runner, tmp_warehouse, sample_files
    ):
        """Status shows pending drop."""
        run_cli(runner, ["drop", "import", "-m", "Test drop", str(sample_files)])

        result = run_cli(runner, ["triage", "status"])

        assert result.exit_code == 0
        assert "Triage Queue (1 drops)" in result.output
        assert "⏳" in result.output  # Pending indicator
        assert "Test drop" in result.output
        assert "(4 pending)" in result.output
        assert "Pending: 1 drops (4 entries)" in result.output

    def test_status_shows_in_progress_drop(self, runner, tmp_warehouse, sample_files):
        """Status shows in-progress drop with marker."""
        run_cli(runner, ["drop", "import", "-m", "Test drop", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # File one entry
        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (triage_dir / "file1.txt").rename(finance_dir / "invoice.txt")

        run_cli(runner, ["triage", "sync"])

        # Check status
        result = run_cli(runner, ["triage", "status"])

        assert result.exit_code == 0
        assert "→" in result.output  # In progress indicator
        assert "(1/4 classified)" in result.output
        assert "← IN PROGRESS" in result.output
        assert "In progress: 1 drop(s) (1/4 entries)" in result.output

    def test_status_shows_complete_drop(self, runner, tmp_warehouse, single_file):
        """Status shows complete drop."""
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # File the entry
        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (triage_dir / single_file.name).rename(finance_dir / "doc.txt")

        run_cli(runner, ["triage", "sync"])

        # Check status
        result = run_cli(runner, ["triage", "status"])

        assert result.exit_code == 0
        assert "✓" in result.output  # Complete indicator
        assert "(1/1 complete)" in result.output
        assert "Complete: 1 drops (1 entries)" in result.output

    def test_status_shows_mixed_queue(self, runner, tmp_warehouse, sample_files):
        """Status shows queue with multiple drops in different states."""
        import tempfile
        import shutil

        # Import three drops
        run_cli(runner, ["drop", "import", "-m", "Drop 1", str(sample_files)])

        with tempfile.TemporaryDirectory() as tmpdir:
            # Second drop
            copied = shutil.copytree(sample_files, tmpdir + "/drop2")
            run_cli(
                runner, ["drop", "import", "-m", "Drop 2", str(copied)], input="y\n"
            )

            # Third drop
            copied2 = shutil.copytree(sample_files, tmpdir + "/drop3")
            run_cli(
                runner, ["drop", "import", "-m", "Drop 3", str(copied2)], input="y\n"
            )

        # Complete drop 1
        run_cli(runner, ["triage", "checkout"])
        triage_dir = tmp_warehouse / "_triage"
        for file in triage_dir.rglob("*.txt"):
            file.unlink()
        run_cli(runner, ["triage", "sync"])

        # Start drop 2 (in progress)
        run_cli(runner, ["triage", "checkout"])
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (triage_dir / "file1.txt").rename(finance_dir / "invoice.txt")
        run_cli(runner, ["triage", "sync"])

        # Drop 3 remains pending

        # Check status
        result = run_cli(runner, ["triage", "status"])

        assert result.exit_code == 0
        assert "Triage Queue (3 drops)" in result.output

        # Should see all three states
        output_lines = result.output.split("\n")
        indicators = [
            line[:1] for line in output_lines if line and line[0] in ["✓", "→", "⏳"]
        ]
        assert "✓" in indicators  # Complete
        assert "→" in indicators  # In progress
        assert "⏳" in indicators  # Pending

        # Summary should show all states
        assert "Complete: 1 drops" in result.output
        assert "In progress: 1 drop(s)" in result.output
        assert "Pending: 1 drops" in result.output

    def test_status_with_no_drops(self, runner, tmp_warehouse):
        """Status with no drops shows empty message."""
        result = run_cli(runner, ["triage", "status"])

        assert result.exit_code == 0
        assert "No drops in warehouse" in result.output
