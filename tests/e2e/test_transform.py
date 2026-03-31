"""E2E tests for transformation workflow.

Tests verify:
- Transform start creates _input/ and _output/ from drop
- Transform status shows current state
- Transform import creates new drop with provenance
- Transform abort cleans up directories
"""

from tests.conftest import run_cli, extract_drop_id


class TestTransformStart:
    """Test transformation start operation."""

    def test_transform_start_from_drop(self, runner, tmp_warehouse, sample_files):
        """Start transformation from a drop."""
        # Import a drop first
        result = run_cli(runner, ["drop", "import", "-m", "Test files", str(sample_files)])
        drop_id = extract_drop_id(result.output)

        # Start transformation from drop
        result = run_cli(runner, ["transform", "start", f"drop:{drop_id}"])

        assert result.exit_code == 0
        assert "Transformation:" in result.output
        assert f"Input spec: drop:{drop_id}" in result.output
        assert "Populated _input/" in result.output
        assert "4 files" in result.output

        # Check _input/ and _output/ directories exist
        input_dir = tmp_warehouse / "_input"
        output_dir = tmp_warehouse / "_output"
        assert input_dir.exists()
        assert output_dir.exists()

        # Check files copied to _input/
        assert (input_dir / "file1.txt").exists()
        assert (input_dir / "file2.txt").exists()

    def test_transform_start_fails_if_already_active(self, runner, tmp_warehouse, sample_files):
        """Cannot start transformation if one is already active."""
        # Import and start transformation
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)
        run_cli(runner, ["transform", "start", f"drop:{drop_id}"])

        # Try to start another transformation
        result = run_cli(runner, ["transform", "start", f"drop:{drop_id}"])

        assert result.exit_code == 1
        assert "already active" in result.output

    def test_transform_start_fails_for_invalid_drop(self, runner, tmp_warehouse):
        """Transform start fails for non-existent drop."""
        result = run_cli(runner, ["transform", "start", "drop:d_20260101_000000_invalid"])

        assert result.exit_code == 1
        assert "Drop not found" in result.output


class TestTransformStatus:
    """Test transformation status display."""

    def test_transform_status_no_active(self, runner, tmp_warehouse):
        """Status shows no active transformation."""
        result = run_cli(runner, ["transform", "status"])

        assert result.exit_code == 0
        assert "No transformation active" in result.output

    def test_transform_status_shows_state(self, runner, tmp_warehouse, sample_files):
        """Status shows active transformation state."""
        # Import and start transformation
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)
        run_cli(runner, ["transform", "start", f"drop:{drop_id}"])

        # Check status
        result = run_cli(runner, ["transform", "status"])

        assert result.exit_code == 0
        assert "Transformation in progress:" in result.output
        assert f"Input spec: drop:{drop_id}" in result.output
        assert "_input/" in result.output
        assert "_output/" in result.output


class TestTransformImport:
    """Test transformation import operation."""

    def test_transform_import_creates_drop(self, runner, tmp_warehouse, sample_files):
        """Import transformation outputs as new drop."""
        # Import and start transformation
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)
        run_cli(runner, ["transform", "start", f"drop:{drop_id}"])

        # Create output file
        output_dir = tmp_warehouse / "_output"
        output_file = output_dir / "transformed.txt"
        output_file.write_text("Transformed content")

        # Import transformation
        result = run_cli(runner, ["transform", "import", "-m", "Transformation test"])

        assert result.exit_code == 0
        assert "Created drop:" in result.output
        assert "Transformation complete" in result.output
        assert "triage checkout" in result.output

        # Check _input/ and _output/ cleaned up
        assert not (tmp_warehouse / "_input").exists()
        assert not (tmp_warehouse / "_output").exists()

    def test_transform_import_fails_if_no_transformation(self, runner, tmp_warehouse):
        """Import fails if no transformation active."""
        result = run_cli(runner, ["transform", "import", "-m", "Test"])

        assert result.exit_code == 1
        assert "No transformation active" in result.output

    def test_transform_import_fails_if_output_empty(self, runner, tmp_warehouse, sample_files):
        """Import fails if _output/ is empty."""
        # Import and start transformation
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)
        run_cli(runner, ["transform", "start", f"drop:{drop_id}"])

        # Try to import without creating output
        result = run_cli(runner, ["transform", "import", "-m", "Empty"])

        assert result.exit_code == 1
        assert "empty" in result.output


class TestTransformAbort:
    """Test transformation abort operation."""

    def test_transform_abort_cleans_up(self, runner, tmp_warehouse, sample_files):
        """Abort removes _input/ and _output/."""
        # Import and start transformation
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)
        run_cli(runner, ["transform", "start", f"drop:{drop_id}"])

        # Abort transformation
        result = run_cli(runner, ["transform", "abort"])

        assert result.exit_code == 0
        assert "Discarding transformation" in result.output
        assert "Transformation aborted" in result.output

        # Check directories removed
        assert not (tmp_warehouse / "_input").exists()
        assert not (tmp_warehouse / "_output").exists()

    def test_transform_abort_fails_if_no_transformation(self, runner, tmp_warehouse):
        """Abort fails if no transformation active."""
        result = run_cli(runner, ["transform", "abort"])

        assert result.exit_code == 1
        assert "No transformation active" in result.output


class TestTransformWorkflow:
    """Test complete transformation workflow."""

    def test_full_transformation_workflow(self, runner, tmp_warehouse, sample_files):
        """Complete workflow: start -> transform -> import -> triage."""
        # 1. Import initial files
        result = run_cli(runner, ["drop", "import", "-m", "Original files", str(sample_files)])
        original_drop_id = extract_drop_id(result.output)

        # 2. Start transformation
        run_cli(runner, ["transform", "start", f"drop:{original_drop_id}"])

        # 3. Create transformed output
        output_dir = tmp_warehouse / "_output"
        (output_dir / "merged.txt").write_text("All files merged")

        # 4. Import transformation
        result = run_cli(runner, ["transform", "import", "-m", "Merged files"])

        # Extract drop ID from "Created drop: <id>" format
        import re
        match = re.search(r"Created drop: (d_\d{8}_\d{6}_[a-f0-9]{8})", result.output)
        assert match, f"No drop ID found in: {result.output}"
        transform_drop_id = match.group(1)

        assert transform_drop_id != original_drop_id

        # 5. Triage the transformed drop
        result = run_cli(runner, ["triage", "checkout", transform_drop_id])

        assert result.exit_code == 0
        assert (tmp_warehouse / "_triage" / "merged.txt").exists()
