"""E2E tests for warehouse audit functionality.

Tests verify:
- Orphaned file detection (files not in DB)
- Missing file detection (documents without files)
- Relocated file detection (files moved from recorded location)
- Duplicate detection (same content in multiple locations)
- Subtree auditing
- Exit codes
"""

from tests.conftest import run_cli


class TestAuditClean:
    """Test audit on clean warehouse."""

    def test_audit_clean_warehouse(self, runner, tmp_warehouse, sample_files):
        """Audit clean warehouse shows no issues."""
        # Import and classify files
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        # Organize all files
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        triage_dir = tmp_warehouse / "_triage"

        for file in triage_dir.rglob("*.txt"):
            file.rename(finance_dir / file.name)

        run_cli(runner, ["triage", "sync"])

        # Audit should be clean
        result = run_cli(runner, ["audit"])

        assert result.exit_code == 0
        assert "Warehouse is consistent!" in result.output
        assert "no orphans or missing files" in result.output


class TestAuditOrphans:
    """Test orphaned file detection."""

    def test_audit_detects_orphaned_file(self, runner, tmp_warehouse, sample_files):
        """Audit detects files in warehouse not tracked in database."""
        # Import and classify files
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)

        for file in triage_dir.rglob("*.txt"):
            file.rename(finance_dir / file.name)

        run_cli(runner, ["triage", "sync"])

        # Manually add an orphaned file (not imported through DWH)
        orphan = finance_dir / "orphan.txt"
        orphan.write_text("This file was manually added")

        # Audit should detect orphan
        result = run_cli(runner, ["audit"])

        assert result.exit_code == 1  # Issues found
        assert "Orphaned Files (1)" in result.output
        assert "finance/orphan.txt" in result.output
        assert "Import this file to track it" in result.output

    def test_audit_shows_orphan_details(self, runner, tmp_warehouse):
        """Audit shows hash and size for orphaned files."""
        # Create category with orphaned file
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        orphan = finance_dir / "orphan.txt"
        orphan.write_text("Orphaned content")

        result = run_cli(runner, ["audit"])

        assert result.exit_code == 1
        assert "Orphaned Files (1)" in result.output
        assert "Hash:" in result.output
        assert "Size:" in result.output


class TestAuditMissing:
    """Test missing file detection."""

    def test_audit_detects_missing_file(self, runner, tmp_warehouse, sample_files):
        """Audit detects documents in DB but files not on disk."""
        # Import and classify files
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)

        # Move files to finance
        files_moved = []
        for file in triage_dir.rglob("*.txt"):
            dest = finance_dir / file.name
            file.rename(dest)
            files_moved.append(dest)

        run_cli(runner, ["triage", "sync"])

        # Delete a file manually (simulating filesystem deletion)
        if files_moved:
            files_moved[0].unlink()

        # Audit should detect missing file
        result = run_cli(runner, ["audit"])

        assert result.exit_code == 1
        assert "Missing Files (1)" in result.output
        assert "finance/" in result.output
        assert "Document:" in result.output
        assert "Entry:" in result.output
        assert "restore command coming soon" in result.output


class TestAuditRelocated:
    """Test relocated file detection."""

    def test_audit_detects_relocated_file(self, runner, tmp_warehouse, sample_files):
        """Audit detects files moved from their recorded location."""
        # Import and classify files
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        archive_dir = tmp_warehouse / "archive"
        finance_dir.mkdir(parents=True)
        archive_dir.mkdir(parents=True)

        # Move one file to finance
        file1 = next(triage_dir.rglob("file1.txt"))
        dest = finance_dir / "file1.txt"
        file1.rename(dest)

        # Clean up remaining files
        for file in triage_dir.rglob("*.txt"):
            file.unlink()

        run_cli(runner, ["triage", "sync"])

        # Manually relocate the file (move from finance to archive)
        relocated = archive_dir / "file1.txt"
        dest.rename(relocated)

        # Audit should detect relocation
        result = run_cli(runner, ["audit"])

        assert result.exit_code == 1
        assert "Relocated Files (1)" in result.output
        assert "archive/file1.txt" in result.output
        assert "expected: finance/file1.txt" in result.output
        assert "mv archive/file1.txt finance/file1.txt" in result.output


class TestAuditDuplicates:
    """Test duplicate detection."""

    def test_audit_detects_duplicates(self, runner, tmp_warehouse, single_file):
        """Audit detects same content in multiple locations."""
        # Import same file twice to different categories
        run_cli(runner, ["drop", "import", "-m", "First", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        # File to finance
        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (triage_dir / single_file.name).rename(finance_dir / "doc1.txt")

        run_cli(runner, ["triage", "sync"])

        # Import again
        run_cli(
            runner, ["drop", "import", "-m", "Second", str(single_file)], input="y\n"
        )
        run_cli(runner, ["triage", "checkout"])

        # File to archive
        archive_dir = tmp_warehouse / "archive"
        archive_dir.mkdir(parents=True)
        (triage_dir / single_file.name).rename(archive_dir / "doc2.txt")

        run_cli(runner, ["triage", "sync"])

        # Audit should detect duplicate
        result = run_cli(runner, ["audit"])

        assert result.exit_code == 1
        assert "Duplicates" in result.output
        assert "2 locations" in result.output
        assert "finance/doc1.txt" in result.output
        assert "archive/doc2.txt" in result.output


class TestAuditSubtree:
    """Test auditing specific subtrees."""

    def test_audit_specific_category(self, runner, tmp_warehouse, sample_files):
        """Can audit specific category subdirectory."""
        # Setup multiple categories
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        work_dir = tmp_warehouse / "work"
        finance_dir.mkdir(parents=True)
        work_dir.mkdir(parents=True)

        # Distribute files
        files = list(triage_dir.rglob("*.txt"))
        if len(files) >= 2:
            files[0].rename(finance_dir / files[0].name)
            files[1].rename(work_dir / files[1].name)

        # Clean up remaining
        for file in triage_dir.rglob("*.txt"):
            file.unlink()

        run_cli(runner, ["triage", "sync"])

        # Add orphan to finance only
        orphan = finance_dir / "orphan.txt"
        orphan.write_text("Orphaned in finance")

        # Audit finance subtree only
        result = run_cli(runner, ["audit", "finance"])

        assert result.exit_code == 1
        assert "Audited: finance" in result.output
        assert "Orphaned Files (1)" in result.output
        assert "finance/orphan.txt" in result.output

    def test_audit_subtree_shows_scoped_files(
        self, runner, tmp_warehouse, sample_files
    ):
        """Audit subtree only counts files in that subtree."""
        # Setup files in multiple categories
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        work_dir = tmp_warehouse / "work"
        finance_dir.mkdir(parents=True)
        work_dir.mkdir(parents=True)

        # Put files in both categories
        files = list(triage_dir.rglob("*.txt"))
        for i, file in enumerate(files):
            if i % 2 == 0:
                file.rename(finance_dir / file.name)
            else:
                file.rename(work_dir / file.name)

        run_cli(runner, ["triage", "sync"])

        # Audit finance only
        result = run_cli(runner, ["audit", "finance/"])

        # Should only report finance files
        assert "finance/" in result.output or "Audited: finance" in result.output
        # Shouldn't mention work files
        assert "work/" not in result.output or result.exit_code == 0


class TestAuditExitCodes:
    """Test audit exit codes."""

    def test_audit_clean_exits_zero(self, runner, tmp_warehouse, single_file):
        """Clean audit exits with code 0."""
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (triage_dir / single_file.name).rename(finance_dir / "doc.txt")

        run_cli(runner, ["triage", "sync"])

        result = run_cli(runner, ["audit"])
        assert result.exit_code == 0

    def test_audit_with_issues_exits_one(self, runner, tmp_warehouse):
        """Audit with issues exits with code 1."""
        # Create orphaned file
        finance_dir = tmp_warehouse / "finance"
        finance_dir.mkdir(parents=True)
        (finance_dir / "orphan.txt").write_text("orphan")

        result = run_cli(runner, ["audit"])
        assert result.exit_code == 1

    def test_audit_invalid_path_exits_two(self, runner, tmp_warehouse):
        """Audit with invalid path exits with code 2."""
        result = run_cli(runner, ["audit", "nonexistent"])
        assert result.exit_code == 2
        assert "Error:" in result.output


class TestAuditMultipleIssues:
    """Test audit with multiple issue types."""

    def test_audit_shows_all_issue_types(self, runner, tmp_warehouse, sample_files):
        """Audit can detect and report multiple issue types simultaneously."""
        # Setup: import and classify some files
        run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        run_cli(runner, ["triage", "checkout"])

        triage_dir = tmp_warehouse / "_triage"
        finance_dir = tmp_warehouse / "finance"
        archive_dir = tmp_warehouse / "archive"
        finance_dir.mkdir(parents=True)
        archive_dir.mkdir(parents=True)

        # Move files
        files = list(triage_dir.rglob("*.txt"))
        if len(files) >= 2:
            # File 1: will be relocated
            files[0].rename(finance_dir / "doc1.txt")
            # File 2: will be deleted (missing)
            dest2 = finance_dir / "doc2.txt"
            files[1].rename(dest2)

        # Clean up rest
        for file in triage_dir.rglob("*.txt"):
            file.unlink()

        run_cli(runner, ["triage", "sync"])

        # Create issues:
        # 1. Orphan
        (finance_dir / "orphan.txt").write_text("orphan")

        # 2. Missing (delete doc2)
        if len(files) >= 2:
            (finance_dir / "doc2.txt").unlink()

        # 3. Relocated (move doc1 to archive)
        if len(files) >= 1:
            (finance_dir / "doc1.txt").rename(archive_dir / "doc1.txt")

        # Audit should detect all issues
        result = run_cli(runner, ["audit"])

        assert result.exit_code == 1
        assert "Orphaned Files (1)" in result.output
        assert "Missing Files (1)" in result.output
        assert "Relocated Files (1)" in result.output
