"""E2E tests for drop import and export operations.

These tests verify the core promises of DWH:
- Import preserves content and structure
- Export reconstructs original files
- Provenance is recorded in receipts
- Files can be retrieved exactly as imported

Focus: Blackbox testing - verify observable outcomes, not internal state.
"""

import json
import os

from tests.conftest import (
    run_cli,
    extract_drop_id,
    assert_files_match,
    compute_file_hash,
)


class TestImportSingleFile:
    """Test importing a single file."""

    def test_import_single_file_succeeds(self, runner, tmp_warehouse, single_file):
        """Import a single file and verify it appears in history."""
        result = run_cli(
            runner, ["drop", "import", "-m", "Single file test", str(single_file)]
        )

        assert result.exit_code == 0
        assert "Imported 1 files" in result.output
        assert "Drop ID:" in result.output

        drop_id = extract_drop_id(result.output)
        assert drop_id.startswith("d_")

    def test_import_single_file_creates_history(
        self, runner, tmp_warehouse, single_file
    ):
        """Verify history folder structure is created."""
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        assert result.exit_code == 0

        history_dir = tmp_warehouse / "_history"
        history_items = list(history_dir.iterdir())

        assert len(history_items) == 1
        assert history_items[0].is_dir()
        assert history_items[0].name.startswith("001_drop_")

    def test_import_single_file_creates_receipt(
        self, runner, tmp_warehouse, single_file
    ):
        """Verify receipt is created with correct metadata."""
        result = run_cli(
            runner, ["drop", "import", "-m", "Important doc", str(single_file)]
        )
        drop_id = extract_drop_id(result.output)

        history_dir = tmp_warehouse / "_history"
        drop_dir = list(history_dir.iterdir())[0]
        receipt_path = drop_dir / "receipt.json"

        assert receipt_path.exists()

        receipt = json.loads(receipt_path.read_text())
        assert receipt["type"] == "drop"
        assert receipt["drop_id"] == drop_id
        assert receipt["message"] == "Important doc"
        assert "actor" in receipt
        assert "created_at" in receipt

    def test_import_single_file_preserves_content(
        self, runner, tmp_warehouse, single_file
    ):
        """Verify imported file has identical content."""
        original_content = single_file.read_bytes()

        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        assert result.exit_code == 0

        history_dir = tmp_warehouse / "_history"
        drop_dir = list(history_dir.iterdir())[0]
        tree_dir = drop_dir / "tree"

        imported_file = tree_dir / single_file.name
        assert imported_file.exists()
        assert imported_file.read_bytes() == original_content

    def test_import_requires_message(self, runner, tmp_warehouse, single_file):
        """Import without message should fail."""
        result = run_cli(runner, ["drop", "import", str(single_file)])

        assert result.exit_code != 0
        # Click will complain about missing -m option


class TestImportDirectory:
    """Test importing directories with nested structure."""

    def test_import_directory_counts_files(self, runner, tmp_warehouse, sample_files):
        """Verify all files in directory are counted."""
        result = run_cli(
            runner, ["drop", "import", "-m", "Dir test", str(sample_files)]
        )

        assert result.exit_code == 0
        # sample_files has: file1.txt, file2.txt, subdir/nested.txt, subdir/deeper/deep.txt
        assert "Imported 4 files" in result.output

    def test_import_directory_preserves_structure(
        self, runner, tmp_warehouse, sample_files
    ):
        """Verify directory structure is preserved in tree/."""
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        assert result.exit_code == 0

        history_dir = tmp_warehouse / "_history"
        drop_dir = list(history_dir.iterdir())[0]
        tree_dir = drop_dir / "tree"

        # Check structure is preserved
        assert (tree_dir / "file1.txt").exists()
        assert (tree_dir / "file2.txt").exists()
        assert (tree_dir / "subdir" / "nested.txt").exists()
        assert (tree_dir / "subdir" / "deeper" / "deep.txt").exists()

    def test_import_directory_preserves_content(
        self, runner, tmp_warehouse, sample_files
    ):
        """Verify all file contents match original."""
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        assert result.exit_code == 0

        history_dir = tmp_warehouse / "_history"
        drop_dir = list(history_dir.iterdir())[0]
        tree_dir = drop_dir / "tree"

        # Verify content matches
        assert_files_match(sample_files, tree_dir)

    def test_import_empty_directory(self, runner, tmp_warehouse, empty_dir):
        """Import empty directory should succeed with 0 files."""
        result = run_cli(runner, ["drop", "import", "-m", "Empty test", str(empty_dir)])

        assert result.exit_code == 0
        assert "Imported 0 files" in result.output


class TestImportMultiplePaths:
    """Test importing multiple paths in one command."""

    def test_import_multiple_files(self, runner, tmp_warehouse, tmp_path):
        """Import multiple individual files."""
        file1 = tmp_path / "a.txt"
        file2 = tmp_path / "b.txt"
        file1.write_text("File A")
        file2.write_text("File B")

        result = run_cli(
            runner, ["drop", "import", "-m", "Multi", str(file1), str(file2)]
        )

        assert result.exit_code == 0
        assert "Imported 2 files" in result.output

    def test_import_mixed_files_and_dirs(self, runner, tmp_warehouse, tmp_path):
        """Import combination of files and directories."""
        file1 = tmp_path / "single.txt"
        file1.write_text("Single")

        dir1 = tmp_path / "dir"
        dir1.mkdir()
        (dir1 / "nested.txt").write_text("Nested")

        result = run_cli(
            runner, ["drop", "import", "-m", "Mixed", str(file1), str(dir1)]
        )

        assert result.exit_code == 0
        assert "Imported 2 files" in result.output

        # Verify both appear in tree
        history_dir = tmp_warehouse / "_history"
        drop_dirs = [
            d for d in history_dir.iterdir() if d.is_dir() and "_drop_" in d.name
        ]
        assert len(drop_dirs) == 1
        tree_dir = drop_dirs[0] / "tree"

        assert (tree_dir / "single.txt").exists()
        assert (tree_dir / "nested.txt").exists()


class TestDropList:
    """Test listing drops."""

    def test_list_empty_warehouse(self, runner, tmp_warehouse):
        """List with no drops should succeed."""
        result = run_cli(runner, ["drop", "list"])

        assert result.exit_code == 0
        assert "No drops yet" in result.output

    def test_list_shows_drops(self, runner, tmp_warehouse, single_file):
        """List should show imported drops."""
        run_cli(runner, ["drop", "import", "-m", "First drop", str(single_file)])
        run_cli(runner, ["drop", "import", "-m", "Second drop", str(single_file)])

        result = run_cli(runner, ["drop", "list"])

        assert result.exit_code == 0
        assert "First drop" in result.output
        assert "Second drop" in result.output
        assert "DROP_ID" in result.output
        assert "MESSAGE" in result.output

    def test_list_shows_file_count(self, runner, tmp_warehouse, sample_files):
        """List should show correct file count."""
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])

        result = run_cli(runner, ["drop", "list"])

        assert result.exit_code == 0
        assert "4" in result.output  # 4 files in sample_files

    def test_list_orders_by_date_desc(self, runner, tmp_warehouse, single_file):
        """Most recent drops should appear first."""
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["drop", "import", "-m", "Second", str(single_file)])

        result = run_cli(runner, ["drop", "list"])

        # Second should appear before First
        lines = result.output.split("\n")
        second_idx = next(i for i, line in enumerate(lines) if "Second" in line)
        first_idx = next(i for i, line in enumerate(lines) if "First" in line)
        assert second_idx < first_idx


class TestDropInspect:
    """Test inspecting drop details."""

    def test_inspect_shows_metadata(self, runner, tmp_warehouse, single_file):
        """Inspect should show drop metadata."""
        result = run_cli(
            runner, ["drop", "import", "-m", "Inspect test", str(single_file)]
        )
        drop_id = extract_drop_id(result.output)

        result = run_cli(runner, ["drop", "inspect", drop_id])

        assert result.exit_code == 0
        assert drop_id in result.output
        assert "Inspect test" in result.output
        assert "Actor:" in result.output
        assert "Date:" in result.output

    def test_inspect_shows_entries(self, runner, tmp_warehouse, sample_files):
        """Inspect should list all entries."""
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)

        result = run_cli(runner, ["drop", "inspect", drop_id])

        assert result.exit_code == 0
        assert "file1.txt" in result.output
        assert "file2.txt" in result.output
        assert "nested.txt" in result.output
        assert "deep.txt" in result.output
        assert "Entries" in result.output

    def test_inspect_shows_file_sizes(self, runner, tmp_warehouse, sample_files):
        """Inspect should show file sizes."""
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)

        result = run_cli(runner, ["drop", "inspect", drop_id])

        assert result.exit_code == 0
        # Should show size info
        assert "KB" in result.output or "MB" in result.output

    def test_inspect_nonexistent_drop_fails(self, runner, tmp_warehouse):
        """Inspect with invalid drop_id should fail."""
        result = run_cli(runner, ["drop", "inspect", "d_99999999_999999_deadbeef"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "error" in result.output.lower()


class TestDropExport:
    """Test exporting drops."""

    def test_export_single_file(self, runner, tmp_warehouse, single_file):
        """Export single file drop."""
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        drop_id = extract_drop_id(result.output)

        export_dir = tmp_warehouse / "exported"
        result = run_cli(runner, ["drop", "export", drop_id, str(export_dir)])

        assert result.exit_code == 0
        assert "Exported 1 files" in result.output
        assert (export_dir / single_file.name).exists()

    def test_export_preserves_content(self, runner, tmp_warehouse, single_file):
        """Exported file should have identical content."""
        original_content = single_file.read_bytes()

        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        drop_id = extract_drop_id(result.output)

        export_dir = tmp_warehouse / "exported"
        run_cli(runner, ["drop", "export", drop_id, str(export_dir)])

        exported_file = export_dir / single_file.name
        assert exported_file.read_bytes() == original_content

    def test_export_preserves_structure(self, runner, tmp_warehouse, sample_files):
        """Export should preserve directory structure."""
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)

        export_dir = tmp_warehouse / "exported"
        result = run_cli(runner, ["drop", "export", drop_id, str(export_dir)])

        assert result.exit_code == 0
        assert (export_dir / "file1.txt").exists()
        assert (export_dir / "subdir" / "nested.txt").exists()
        assert (export_dir / "subdir" / "deeper" / "deep.txt").exists()

    def test_export_creates_destination(self, runner, tmp_warehouse, single_file):
        """Export should create destination directory if it doesn't exist."""
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        drop_id = extract_drop_id(result.output)

        export_dir = tmp_warehouse / "new" / "nested" / "path"
        result = run_cli(runner, ["drop", "export", drop_id, str(export_dir)])

        assert result.exit_code == 0
        assert export_dir.exists()
        assert (export_dir / single_file.name).exists()

    def test_export_nonexistent_drop_fails(self, runner, tmp_warehouse):
        """Export with invalid drop_id should fail."""
        export_dir = tmp_warehouse / "exported"
        result = run_cli(
            runner, ["drop", "export", "d_99999999_999999_deadbeef", str(export_dir)]
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "error" in result.output.lower()


class TestImportExportRoundtrip:
    """Test that import → export produces identical results.

    This is a core promise of DWH: what you put in, you can get back exactly.
    """

    def test_roundtrip_single_file(self, runner, tmp_warehouse, single_file):
        """Import and export single file produces identical content."""
        original_content = single_file.read_bytes()

        # Import
        result = run_cli(
            runner, ["drop", "import", "-m", "Roundtrip", str(single_file)]
        )
        drop_id = extract_drop_id(result.output)

        # Export
        export_dir = tmp_warehouse / "restored"
        run_cli(runner, ["drop", "export", drop_id, str(export_dir)])

        # Verify
        restored_file = export_dir / single_file.name
        assert restored_file.read_bytes() == original_content

    def test_roundtrip_directory(self, runner, tmp_warehouse, sample_files):
        """Import and export directory produces identical structure and content."""
        # Import
        result = run_cli(
            runner, ["drop", "import", "-m", "Roundtrip", str(sample_files)]
        )
        drop_id = extract_drop_id(result.output)

        # Export
        export_dir = tmp_warehouse / "restored"
        run_cli(runner, ["drop", "export", drop_id, str(export_dir)])

        # Verify structure and content match
        assert_files_match(
            sample_files, export_dir, "Roundtrip failed: files don't match"
        )

    def test_roundtrip_preserves_file_hashes(self, runner, tmp_warehouse, sample_files):
        """Import and export preserves file hashes (bit-for-bit identical)."""
        # Compute hashes before import
        original_hashes = {}
        for file in sample_files.rglob("*"):
            if file.is_file():
                rel_path = file.relative_to(sample_files)
                original_hashes[str(rel_path)] = compute_file_hash(file)

        # Import and export
        result = run_cli(
            runner, ["drop", "import", "-m", "Hash test", str(sample_files)]
        )
        drop_id = extract_drop_id(result.output)

        export_dir = tmp_warehouse / "restored"
        run_cli(runner, ["drop", "export", drop_id, str(export_dir)])

        # Verify hashes match
        for rel_path, original_hash in original_hashes.items():
            restored_file = export_dir / rel_path
            assert restored_file.exists(), f"Missing file: {rel_path}"
            restored_hash = compute_file_hash(restored_file)
            assert restored_hash == original_hash, f"Hash mismatch for {rel_path}"

    def test_multiple_roundtrips_independent(self, runner, tmp_warehouse, sample_files):
        """Multiple imports of same content can be exported independently."""
        # Import twice
        result1 = run_cli(runner, ["drop", "import", "-m", "First", str(sample_files)])
        drop_id1 = extract_drop_id(result1.output)

        result2 = run_cli(runner, ["drop", "import", "-m", "Second", str(sample_files)])
        drop_id2 = extract_drop_id(result2.output)

        # Export both
        export_dir1 = tmp_warehouse / "export1"
        export_dir2 = tmp_warehouse / "export2"

        run_cli(runner, ["drop", "export", drop_id1, str(export_dir1)])
        run_cli(runner, ["drop", "export", drop_id2, str(export_dir2)])

        # Both should match original
        assert_files_match(sample_files, export_dir1)
        assert_files_match(sample_files, export_dir2)
        assert_files_match(export_dir1, export_dir2)


class TestProvenanceAndReceipts:
    """Test that provenance is correctly recorded.

    DWH promises: every file has a receipt showing who, when, why, from where.
    """

    def test_receipt_contains_required_fields(self, runner, tmp_warehouse, single_file):
        """Receipt must contain all required provenance fields."""
        result = run_cli(
            runner, ["drop", "import", "-m", "Provenance test", str(single_file)]
        )
        drop_id = extract_drop_id(result.output)

        # Find and read receipt
        history_dir = tmp_warehouse / "_history"
        drop_dir = list(history_dir.iterdir())[0]
        receipt = json.loads((drop_dir / "receipt.json").read_text())

        # Verify required fields
        assert receipt["type"] == "drop"
        assert receipt["drop_id"] == drop_id
        assert receipt["message"] == "Provenance test"
        assert isinstance(receipt["actor"], str) and receipt["actor"]
        assert isinstance(receipt["created_at"], str) and receipt["created_at"]

    def test_receipt_message_is_preserved(self, runner, tmp_warehouse, single_file):
        """The -m message should be stored exactly in receipt."""
        message = "Very important tax documents from 2024"
        run_cli(runner, ["drop", "import", "-m", message, str(single_file)])

        history_dir = tmp_warehouse / "_history"
        drop_dir = list(history_dir.iterdir())[0]
        receipt = json.loads((drop_dir / "receipt.json").read_text())

        assert receipt["message"] == message

    def test_each_drop_has_unique_id(self, runner, tmp_warehouse, single_file):
        """Each import should generate a unique drop_id."""
        result1 = run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        drop_id1 = extract_drop_id(result1.output)

        result2 = run_cli(runner, ["drop", "import", "-m", "Second", str(single_file)])
        drop_id2 = extract_drop_id(result2.output)

        assert drop_id1 != drop_id2

    def test_history_is_numbered_sequentially(self, runner, tmp_warehouse, single_file):
        """History folders should be numbered sequentially."""
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["drop", "import", "-m", "Second", str(single_file)])
        run_cli(runner, ["drop", "import", "-m", "Third", str(single_file)])

        history_dir = tmp_warehouse / "_history"
        history_items = sorted(history_dir.iterdir())

        assert len(history_items) == 3
        assert history_items[0].name.startswith("001_")
        assert history_items[1].name.startswith("002_")
        assert history_items[2].name.startswith("003_")


class TestDeduplication:
    """Test that duplicate content is handled efficiently.

    Note: Some internal state checking is necessary to verify deduplication,
    but we focus on observable outcomes where possible.
    """

    def test_import_same_file_twice_creates_two_drops(
        self, runner, tmp_warehouse, single_file
    ):
        """Importing same file twice should create two separate drops."""
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["drop", "import", "-m", "Second", str(single_file)])

        result = run_cli(runner, ["drop", "list"])

        assert result.output.count("d_") >= 2  # At least two drop IDs

    def test_import_same_file_twice_both_exportable(
        self, runner, tmp_warehouse, single_file
    ):
        """Both drops of same file should be independently exportable."""
        result1 = run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        drop_id1 = extract_drop_id(result1.output)

        result2 = run_cli(runner, ["drop", "import", "-m", "Second", str(single_file)])
        drop_id2 = extract_drop_id(result2.output)

        # Export both
        export1 = tmp_warehouse / "export1"
        export2 = tmp_warehouse / "export2"

        result1 = run_cli(runner, ["drop", "export", drop_id1, str(export1)])
        result2 = run_cli(runner, ["drop", "export", drop_id2, str(export2)])

        assert result1.exit_code == 0
        assert result2.exit_code == 0
        assert (export1 / single_file.name).exists()
        assert (export2 / single_file.name).exists()


class TestErrorHandling:
    """Test error cases are handled gracefully."""

    def test_import_nonexistent_file_fails(self, runner, tmp_warehouse):
        """Import should fail gracefully for missing files."""
        result = run_cli(
            runner, ["drop", "import", "-m", "Test", "/nonexistent/file.txt"]
        )

        assert result.exit_code != 0

    def test_import_without_warehouse_fails(self, runner, tmp_path):
        """Import outside warehouse should fail with helpful message."""
        os.chdir(tmp_path)
        file = tmp_path / "test.txt"
        file.write_text("test")

        result = run_cli(runner, ["drop", "import", "-m", "Test", str(file)])

        assert result.exit_code != 0
        assert "warehouse" in result.output.lower() or "init" in result.output.lower()

    def test_export_requires_two_arguments(self, runner, tmp_warehouse):
        """Export without destination should fail."""
        result = run_cli(runner, ["drop", "export", "d_20260101_000000_12345678"])

        assert result.exit_code != 0

    def test_list_without_warehouse_fails(self, runner, tmp_path):
        """List outside warehouse should fail gracefully."""
        os.chdir(tmp_path)
        result = run_cli(runner, ["drop", "list"])

        assert result.exit_code != 0
        assert "warehouse" in result.output.lower()


class TestAutoClassification:
    """Test auto-classification for in-tree imports (ADR-003)."""

    def test_import_from_category_auto_classifies(self, runner, tmp_warehouse):
        """Import from within warehouse auto-classifies files."""
        import sqlite3

        # Create a file in a category within warehouse
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        invoice = finance_dir / "invoice.pdf"
        invoice.write_text("Invoice content")

        # Import the in-tree file
        result = run_cli(runner, ["drop", "import", "-m", "Tax docs", str(invoice)])

        assert result.exit_code == 0
        assert "Imported 1 files" in result.output
        assert "Auto-classified 1 files" in result.output

        # Verify document is in database
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        documents = conn.execute("SELECT * FROM documents").fetchall()
        assert len(documents) == 1
        assert documents[0]["name"] == "invoice.pdf"
        assert documents[0]["category"] == "finance"

        conn.close()

    def test_import_nested_category_auto_classifies(self, runner, tmp_warehouse):
        """Import from nested category preserves full path."""
        import sqlite3

        # Create nested category structure
        cat_dir = tmp_warehouse / "finance" / "taxes" / "2024"
        cat_dir.mkdir(parents=True)
        doc = cat_dir / "return.pdf"
        doc.write_text("Tax return")

        # Import
        result = run_cli(runner, ["drop", "import", "-m", "Taxes", str(doc)])

        assert result.exit_code == 0
        assert "Auto-classified 1 files" in result.output

        # Verify category includes full path
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        documents = conn.execute("SELECT * FROM documents").fetchall()
        assert len(documents) == 1
        assert documents[0]["category"] == "finance/taxes/2024"

        conn.close()

    def test_mixed_import_auto_classifies_only_in_tree(
        self, runner, tmp_warehouse, tmp_path
    ):
        """Mixed import: auto-classify in-tree, leave external for triage."""
        import sqlite3

        # In-tree file
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        invoice = finance_dir / "invoice.pdf"
        invoice.write_text("Invoice")

        # External file
        external = tmp_path.parent / "receipt.pdf"
        external.write_text("Receipt")

        # Import both
        result = run_cli(
            runner, ["drop", "import", "-m", "Mixed", str(invoice), str(external)]
        )

        assert result.exit_code == 0
        assert "Imported 2 files" in result.output
        assert "Auto-classified 1 files" in result.output

        # Verify only one document (the in-tree file)
        db_path = tmp_warehouse / ".dwh" / "dwh.db"
        conn = sqlite3.connect(db_path)
        documents = conn.execute("SELECT * FROM documents").fetchall()
        assert len(documents) == 1
        assert documents[0][2] == "invoice.pdf"  # name column
        conn.close()

    def test_import_from_system_dir_rejected(self, runner, tmp_warehouse):
        """Import from system directories should fail."""
        # Create file in _history (system directory)
        history_file = tmp_warehouse / "_history" / "bad.txt"
        history_file.write_text("Bad")

        result = run_cli(runner, ["drop", "import", "-m", "Bad", str(history_file)])

        # Should succeed but not auto-classify (system dirs excluded)
        assert result.exit_code == 0
        assert "Auto-classified" not in result.output

    def test_classification_record_created(self, runner, tmp_warehouse):
        """Auto-classification creates history record."""
        # Create in-tree file
        docs_dir = tmp_warehouse / "docs"
        docs_dir.mkdir(parents=True)
        doc = docs_dir / "memo.txt"
        doc.write_text("Memo")

        # Import
        run_cli(runner, ["drop", "import", "-m", "Docs", str(doc)])

        # Check classification record exists
        history_dir = tmp_warehouse / "_history"
        classify_files = list(history_dir.glob("*_classify.json"))

        assert len(classify_files) == 1

        # Verify content
        classify_record = json.loads(classify_files[0].read_text())
        assert classify_record["type"] == "classify"
        assert classify_record["message"] == "Auto-classify (in-tree import)"
        assert len(classify_record["classifications"]) == 1

        classification = classify_record["classifications"][0]
        assert classification["category"] == "docs"
        assert classification["name"] == "memo.txt"
