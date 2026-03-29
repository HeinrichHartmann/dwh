"""E2E tests for warehouse registry and out-of-tree operations.

Tests verify:
- Warehouse registration via dwh init
- dwh warehouse list shows registered warehouses
- dwh warehouse select changes active warehouse
- Commands work out-of-tree when warehouse is selected
- Auto-selection of first warehouse
"""

import os

from tests.conftest import run_cli, extract_drop_id


class TestWarehouseList:
    """Test warehouse list command."""

    def test_list_no_warehouses(self, runner, tmp_path):
        """List with no warehouses shows helpful message."""
        os.chdir(tmp_path)
        result = run_cli(runner, ["warehouse", "list"])

        assert result.exit_code == 0
        assert "No warehouses registered" in result.output
        assert "dwh init" in result.output

    def test_list_shows_registered_warehouse(self, runner, tmp_path):
        """List shows warehouses after init."""
        wh_path = tmp_path / "test_wh"
        wh_path.mkdir()
        os.chdir(wh_path)

        # Initialize warehouse
        run_cli(runner, ["init", "."])

        # List should show it
        result = run_cli(runner, ["warehouse", "list"])

        assert result.exit_code == 0
        assert "test_wh" in result.output
        assert str(wh_path) in result.output

    def test_list_shows_multiple_warehouses(self, runner, tmp_path):
        """List shows all registered warehouses."""
        # Create and init two warehouses
        wh1 = tmp_path / "warehouse1"
        wh1.mkdir()
        os.chdir(wh1)
        run_cli(runner, ["init", "."])

        wh2 = tmp_path / "warehouse2"
        wh2.mkdir()
        os.chdir(wh2)
        run_cli(runner, ["init", "."])

        # List should show both
        result = run_cli(runner, ["warehouse", "list"])

        assert result.exit_code == 0
        assert "warehouse1" in result.output
        assert "warehouse2" in result.output

    def test_list_shows_selected_indicator(self, runner, tmp_path):
        """List shows * next to selected warehouse."""
        wh_path = tmp_path / "test_wh"
        wh_path.mkdir()
        os.chdir(wh_path)

        # Initialize warehouse (should auto-select as first)
        run_cli(runner, ["init", "."])

        # List should show selection marker
        result = run_cli(runner, ["warehouse", "list"])

        assert result.exit_code == 0
        # Find the line with test_wh and verify it has *
        lines = result.output.split("\n")
        wh_line = next(line for line in lines if "test_wh" in line)
        assert "*" in wh_line


class TestWarehouseAdd:
    """Test warehouse add command."""

    def test_add_existing_warehouse(self, runner, tmp_path):
        """Add an existing warehouse to registry."""
        # Create warehouse manually (not through init to simulate forgotten warehouse)
        wh_path = tmp_path / "existing_wh"
        wh_path.mkdir()
        dwh_dir = wh_path / ".dwh"
        dwh_dir.mkdir()
        (dwh_dir / "config.toml").write_text('name = "Existing Warehouse"\n')

        # Add to registry
        result = run_cli(runner, ["warehouse", "add", str(wh_path)])

        assert result.exit_code == 0
        assert "Registered warehouse: existing_wh" in result.output
        assert str(wh_path) in result.output

        # Verify it appears in list
        result = run_cli(runner, ["warehouse", "list"])
        assert "existing_wh" in result.output

    def test_add_with_custom_name(self, runner, tmp_path):
        """Add warehouse with custom registry name."""
        wh_path = tmp_path / "my_warehouse"
        wh_path.mkdir()
        (wh_path / ".dwh").mkdir()

        result = run_cli(
            runner, ["warehouse", "add", str(wh_path), "--name", "custom_name"]
        )

        assert result.exit_code == 0
        assert "Registered warehouse: custom_name" in result.output

        # Verify custom name in list
        result = run_cli(runner, ["warehouse", "list"])
        assert "custom_name" in result.output

    def test_add_non_warehouse_fails(self, runner, tmp_path):
        """Add non-warehouse directory should fail."""
        non_wh = tmp_path / "not_a_warehouse"
        non_wh.mkdir()

        result = run_cli(runner, ["warehouse", "add", str(non_wh)])

        assert result.exit_code != 0
        assert "not a warehouse" in result.output.lower()
        assert "missing .dwh" in result.output.lower()

    def test_add_already_registered_idempotent(self, runner, tmp_path):
        """Adding already registered warehouse is idempotent."""
        # Create and add warehouse
        wh_path = tmp_path / "test_wh"
        wh_path.mkdir()
        (wh_path / ".dwh").mkdir()

        run_cli(runner, ["warehouse", "add", str(wh_path)])

        # Add again
        result = run_cli(runner, ["warehouse", "add", str(wh_path)])

        assert result.exit_code == 0
        assert "already registered" in result.output.lower()

    def test_add_name_conflict_fails(self, runner, tmp_path):
        """Adding warehouse with conflicting name fails."""
        # Create two warehouses
        wh1 = tmp_path / "wh1"
        wh1.mkdir()
        (wh1 / ".dwh").mkdir()

        wh2 = tmp_path / "wh2"
        wh2.mkdir()
        (wh2 / ".dwh").mkdir()

        # Add first
        run_cli(runner, ["warehouse", "add", str(wh1), "--name", "mywarehouse"])

        # Try to add second with same name
        result = run_cli(
            runner, ["warehouse", "add", str(wh2), "--name", "mywarehouse"]
        )

        assert result.exit_code != 0
        assert "already registered" in result.output.lower()
        assert str(wh1) in result.output

    def test_add_auto_selects_first(self, runner, tmp_path):
        """First added warehouse is auto-selected."""
        wh_path = tmp_path / "first"
        wh_path.mkdir()
        (wh_path / ".dwh").mkdir()

        result = run_cli(runner, ["warehouse", "add", str(wh_path)])

        assert result.exit_code == 0
        assert "Selected as active warehouse" in result.output

        # Verify selection
        result = run_cli(runner, ["warehouse", "list"])
        lines = result.output.split("\n")
        wh_line = next(line for line in lines if "first" in line)
        assert "*" in wh_line

    def test_add_reads_display_name_from_config(self, runner, tmp_path):
        """Add reads display name from warehouse config."""
        wh_path = tmp_path / "my_wh"
        wh_path.mkdir()
        dwh_dir = wh_path / ".dwh"
        dwh_dir.mkdir()
        (dwh_dir / "config.toml").write_text('name = "My Project Documents"\n')

        result = run_cli(runner, ["warehouse", "add", str(wh_path)])

        assert result.exit_code == 0
        assert "Display name: My Project Documents" in result.output


class TestWarehouseSelect:
    """Test warehouse select command."""

    def test_select_warehouse(self, runner, tmp_path):
        """Select command changes active warehouse."""
        # Create two warehouses
        wh1 = tmp_path / "wh1"
        wh1.mkdir()
        os.chdir(wh1)
        run_cli(runner, ["init", "."])

        wh2 = tmp_path / "wh2"
        wh2.mkdir()
        os.chdir(wh2)
        run_cli(runner, ["init", "."])

        # Select wh1
        result = run_cli(runner, ["warehouse", "select", "wh1"])

        assert result.exit_code == 0
        assert "Selected warehouse: wh1" in result.output

        # Verify selection
        result = run_cli(runner, ["warehouse", "list"])
        lines = result.output.split("\n")
        wh1_line = next(line for line in lines if "wh1" in line and "wh2" not in line)
        assert "*" in wh1_line

    def test_select_nonexistent_warehouse_fails(self, runner, tmp_path):
        """Select with invalid name shows error."""
        os.chdir(tmp_path)
        result = run_cli(runner, ["warehouse", "select", "nonexistent"])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()
        assert "Available warehouses" in result.output

    def test_select_shows_available_warehouses_on_error(self, runner, tmp_path):
        """Select error shows list of available warehouses."""
        # Create a warehouse
        wh_path = tmp_path / "actual_wh"
        wh_path.mkdir()
        os.chdir(wh_path)
        run_cli(runner, ["init", "."])

        # Try to select wrong name
        result = run_cli(runner, ["warehouse", "select", "wrong_name"])

        assert result.exit_code != 0
        assert "actual_wh" in result.output


class TestWarehouseInit:
    """Test warehouse initialization and registration."""

    def test_init_registers_warehouse(self, runner, tmp_path):
        """Init automatically registers warehouse."""
        wh_path = tmp_path / "my_warehouse"
        wh_path.mkdir()
        os.chdir(wh_path)

        result = run_cli(runner, ["init", "."])

        assert result.exit_code == 0
        assert "Registered as: my_warehouse" in result.output

        # Verify it appears in list
        result = run_cli(runner, ["warehouse", "list"])
        assert "my_warehouse" in result.output

    def test_init_with_custom_name(self, runner, tmp_path):
        """Init with --name sets display name."""
        wh_path = tmp_path / "wh"
        wh_path.mkdir()
        os.chdir(wh_path)

        result = run_cli(runner, ["init", ".", "--name", "My Documents"])

        assert result.exit_code == 0
        assert "Registered as: wh" in result.output

    def test_init_with_register_as(self, runner, tmp_path):
        """Init with --register-as sets registry name."""
        wh_path = tmp_path / "some_path"
        wh_path.mkdir()
        os.chdir(wh_path)

        result = run_cli(runner, ["init", ".", "--register-as", "custom_name"])

        assert result.exit_code == 0
        assert "Registered as: custom_name" in result.output

        # Verify custom name in registry
        result = run_cli(runner, ["warehouse", "list"])
        assert "custom_name" in result.output

    def test_init_auto_selects_first_warehouse(self, runner, tmp_path):
        """First initialized warehouse is auto-selected."""
        wh_path = tmp_path / "first_wh"
        wh_path.mkdir()
        os.chdir(wh_path)

        result = run_cli(runner, ["init", "."])

        assert result.exit_code == 0
        assert "Selected as active warehouse" in result.output

        # Verify selection
        result = run_cli(runner, ["warehouse", "list"])
        lines = result.output.split("\n")
        wh_line = next(line for line in lines if "first_wh" in line)
        assert "*" in wh_line

    def test_init_second_warehouse_not_auto_selected(self, runner, tmp_path):
        """Second warehouse is not auto-selected."""
        # First warehouse
        wh1 = tmp_path / "wh1"
        wh1.mkdir()
        os.chdir(wh1)
        run_cli(runner, ["init", "."])

        # Second warehouse
        wh2 = tmp_path / "wh2"
        wh2.mkdir()
        os.chdir(wh2)
        result = run_cli(runner, ["init", "."])

        # Should not see auto-select message
        assert "Selected as active warehouse" not in result.output

        # First should still be selected
        result = run_cli(runner, ["warehouse", "list"])
        lines = result.output.split("\n")
        wh1_line = next(line for line in lines if "wh1" in line and "wh2" not in line)
        assert "*" in wh1_line

    def test_init_same_path_twice_fails(self, runner, tmp_path):
        """Init same path twice fails (warehouse already exists)."""
        wh_path = tmp_path / "duplicate"
        wh_path.mkdir()
        os.chdir(wh_path)

        run_cli(runner, ["init", "."])

        # Init again should fail
        result = run_cli(runner, ["init", "."])

        assert result.exit_code != 0
        assert "already exists" in result.output.lower()


class TestOutOfTreeOperations:
    """Test that commands work outside warehouse when one is selected."""

    def test_import_from_outside_warehouse(self, runner, tmp_path, single_file):
        """Import works from any directory when warehouse is selected."""
        # Create and init warehouse
        wh_path = tmp_path / "warehouse"
        wh_path.mkdir()
        os.chdir(wh_path)
        run_cli(runner, ["init", "."])

        # Move to different directory
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        # Import should work
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])

        assert result.exit_code == 0
        assert "Imported 1 files" in result.output

        # Verify file is in warehouse history
        assert (wh_path / "_history").exists()
        history_items = list((wh_path / "_history").iterdir())
        assert len(history_items) == 1

    def test_list_from_outside_warehouse(self, runner, tmp_path, single_file):
        """List works from any directory when warehouse is selected."""
        # Create warehouse and import
        wh_path = tmp_path / "warehouse"
        wh_path.mkdir()
        os.chdir(wh_path)
        run_cli(runner, ["init", "."])
        run_cli(runner, ["drop", "import", "-m", "Test drop", str(single_file)])

        # Move to different directory
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        # List should work
        result = run_cli(runner, ["drop", "list"])

        assert result.exit_code == 0
        assert "Test drop" in result.output

    def test_commands_prefer_current_directory(self, runner, tmp_path, single_file):
        """Commands use current directory warehouse if inside one."""
        # Create two warehouses
        wh1 = tmp_path / "wh1"
        wh1.mkdir()
        os.chdir(wh1)
        run_cli(runner, ["init", "."])

        wh2 = tmp_path / "wh2"
        wh2.mkdir()
        os.chdir(wh2)
        run_cli(runner, ["init", "."])

        # Select wh1
        run_cli(runner, ["warehouse", "select", "wh1"])

        # But we're in wh2, so import should go to wh2
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])
        assert result.exit_code == 0

        # Verify it went to wh2 (current dir), not wh1 (selected)
        wh2_history = list((wh2 / "_history").iterdir())
        wh1_history = list((wh1 / "_history").iterdir())

        assert len(wh2_history) == 1
        assert len(wh1_history) == 0

    def test_rebuild_from_outside_warehouse(self, runner, tmp_path, single_file):
        """Rebuild works from outside when warehouse is selected."""
        # Create warehouse and import
        wh_path = tmp_path / "warehouse"
        wh_path.mkdir()
        os.chdir(wh_path)
        run_cli(runner, ["init", "."])
        run_cli(runner, ["drop", "import", "-m", "Test", str(single_file)])

        # Delete database
        db_path = wh_path / ".dwh" / "dwh.db"
        db_path.unlink()

        # Move to different directory
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        # Rebuild should work
        result = run_cli(runner, ["rebuild"])

        assert result.exit_code == 0
        assert "1 drops" in result.output
        assert db_path.exists()

    def test_triage_from_outside_warehouse(self, runner, tmp_path, sample_files):
        """Triage workflow works from outside when warehouse is selected."""
        # Create warehouse and import
        wh_path = tmp_path / "warehouse"
        wh_path.mkdir()
        os.chdir(wh_path)
        run_cli(runner, ["init", "."])
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(sample_files)])
        drop_id = extract_drop_id(result.output)

        # Move to different directory
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        # Triage checkout should work
        result = run_cli(runner, ["triage", "checkout", drop_id])

        assert result.exit_code == 0
        assert "Checked out" in result.output

        # Verify triage directory in warehouse
        assert (wh_path / "_triage").exists()


class TestWarehouseResolution:
    """Test warehouse resolution priority."""

    def test_no_warehouse_no_selection_fails(self, runner, tmp_path):
        """Commands fail when not in warehouse and none selected."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        result = run_cli(runner, ["drop", "list"])

        assert result.exit_code != 0
        assert "no warehouse selected" in result.output.lower()
        assert "dwh warehouse select" in result.output

    def test_error_message_suggests_list(self, runner, tmp_path):
        """Error message suggests dwh warehouse list."""
        # Create a valid file so we get past click validation
        test_file = tmp_path / "file.txt"
        test_file.write_text("test")

        os.chdir(tmp_path)
        result = run_cli(runner, ["drop", "import", "-m", "Test", str(test_file)])

        assert result.exit_code != 0
        assert "dwh warehouse list" in result.output
