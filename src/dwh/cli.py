"""CLI for DWH."""

import sys
from pathlib import Path

import click

from dwh import db, drop, triage, warehouse


@click.group()
def main():
    """Document Warehouse - metadata-centric document archival."""
    pass


@main.command()
@click.argument("path", type=click.Path(path_type=Path), default=".")
@click.option("--name", help="Warehouse display name (defaults to directory name)")
@click.option("--register-as", help="Registry name (defaults to directory name)")
def init(path: Path, name: str | None, register_as: str | None):
    """Initialize a new warehouse."""
    from dwh.global_config import GlobalConfig

    path = path.resolve()
    wh = warehouse.Warehouse(path)

    if wh.exists():
        click.echo(f"Error: Warehouse already exists at {path}", err=True)
        sys.exit(1)

    try:
        # Create directory structure (ADR-003)
        wh.dwh_dir.mkdir(parents=True, exist_ok=True)
        wh.history_dir.mkdir(parents=True, exist_ok=True)
        wh.triage_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        db.init_db(wh.db_path)

        # Write basic config
        wh.config_path.write_text(f'name = "{name or path.name}"\nversion = "1"\n')

        click.echo(f"Initialized warehouse at {path}")
        click.echo()
        click.echo("Created:")
        click.echo("  .dwh/       - Metadata (config, database cache)")
        click.echo("  _history/   - Event log (source of truth, backup required)")
        click.echo("  _triage/    - Working directory (ephemeral)")

        # Register in global config (ADR-004)
        registry_name = register_as or path.name
        global_config = GlobalConfig.load()

        if registry_name in global_config.warehouses:
            click.echo()
            click.echo(f"Note: Warehouse '{registry_name}' already registered.")
        else:
            global_config.add_warehouse(
                name=registry_name, path=path, display_name=name or path.name
            )
            global_config.save()

            click.echo()
            click.echo(f"Registered as: {registry_name}")

            # Auto-select if first warehouse
            if len(global_config.warehouses) == 1:
                global_config.default_warehouse = registry_name
                global_config.save()
                click.echo("Selected as active warehouse")

    except Exception as e:
        click.echo(f"Error initializing warehouse: {e}", err=True)
        sys.exit(1)


@main.command()
def rebuild():
    """Rebuild database from history."""
    try:
        wh = warehouse.resolve_warehouse(require_db=False)

        click.echo("Rebuilding database from history...")

        result = drop.rebuild_database(wh.history_dir, wh.db_path)

        click.echo(f"✓ Replayed {result['drops']} drops")
        click.echo(f"✓ Replayed {result['classifications']} classifications")
        click.echo("Database rebuild complete")

    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except warehouse.NoWarehouseSpecifiedError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error rebuilding database: {e}", err=True)
        sys.exit(1)


@main.group()
def drop_cmd():
    """Manage drops (import events)."""
    pass


# Rename the group to 'drop' so commands work as 'dwh drop ...'
drop_cmd.name = "drop"


@drop_cmd.command("import")
@click.option("-m", "--message", required=True, help="Import message (required)")
@click.argument(
    "paths", nargs=-1, type=click.Path(exists=True, path_type=Path), required=True
)
def drop_import_cmd(message: str, paths: tuple[Path, ...]):
    """Import files into the warehouse."""
    try:
        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        # Check for duplicate drop before importing (ADR-005)
        tree_fingerprint = drop.compute_tree_fingerprint_from_paths(list(paths))
        duplicate = drop.check_duplicate_drop(tree_fingerprint, conn)

        if duplicate:
            # Prompt user about duplicate
            click.echo()
            click.echo("⚠ This exact tree was imported before:")
            click.echo(f"  Drop: {duplicate['drop_id']}")
            click.echo(f"  Date: {duplicate['created_at']}")
            click.echo(f"  Message: {duplicate['message']}")
            click.echo()

            if not click.confirm(
                "Import again? (Creates record that nothing changed)", default=False
            ):
                click.echo("Import cancelled.")
                conn.close()
                return

        result = drop.drop_import(list(paths), message, wh.root, wh.history_dir, conn)

        click.echo(f"Imported {len(result.entries)} files")
        click.echo(f"Drop ID: {result.id}")

        if result.auto_classified_count > 0:
            click.echo(
                f"✓ Auto-classified {result.auto_classified_count} files (already organized)"
            )

        conn.close()

    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Warehouse not found.", err=True)
        sys.exit(1)
    except warehouse.NoWarehouseSpecifiedError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error importing: {e}", err=True)
        sys.exit(1)


@drop_cmd.command("list")
def drop_list_cmd():
    """List all drops."""
    try:
        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        drops = drop.drop_list(conn)

        if not drops:
            click.echo("No drops yet.")
            return

        # Print header
        click.echo(f"{'DROP_ID':<32} {'DATE':<12} {'FILES':<6} MESSAGE")
        click.echo("-" * 80)

        # Print drops
        for d in drops:
            date = d.created_at[:10]  # YYYY-MM-DD
            click.echo(f"{d.id:<32} {date:<12} {d.entry_count:<6} {d.message}")

        conn.close()

    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error listing drops: {e}", err=True)
        sys.exit(1)


@drop_cmd.command("inspect")
@click.argument("drop_id")
def drop_inspect_cmd(drop_id: str):
    """Show detailed information about a drop."""
    try:
        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        d = drop.drop_inspect(drop_id, conn)

        click.echo(f"Drop: {d.id}")
        click.echo(f"Date: {d.created_at}")
        click.echo(f"Actor: {d.actor}")
        click.echo(f"Message: {d.message}")
        click.echo()

        total_size = sum(e.size for e in d.entries)
        size_mb = total_size / (1024 * 1024)
        click.echo(f"Entries ({len(d.entries)} files, {size_mb:.1f} MB):")

        for e in d.entries:
            size_kb = e.size / 1024
            click.echo(
                f"  {e.id}  {e.filename:<30} {e.relative_path:<40} {size_kb:>8.1f} KB"
            )

        conn.close()

    except drop.DropNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error inspecting drop: {e}", err=True)
        sys.exit(1)


@drop_cmd.command("export")
@click.argument("drop_id")
@click.argument("destination", type=click.Path(path_type=Path))
def drop_export_cmd(drop_id: str, destination: Path):
    """Export a drop to a destination directory."""
    try:
        wh = warehouse.resolve_warehouse()

        count = drop.drop_export(drop_id, destination, wh.history_dir)

        click.echo(f"Exported {count} files to {destination}")

    except drop.DropNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error exporting drop: {e}", err=True)
        sys.exit(1)


@main.group()
def triage_group():
    """Triage workflow commands."""
    pass


triage_group.name = "triage"


@triage_group.command("checkout")
@click.argument("drop_id", required=False)
@click.option("--force", is_flag=True, help="Restart current drop from scratch")
def triage_checkout_cmd(drop_id: str | None, force: bool):
    """Checkout a drop for triage.

    Without arguments, resumes in-progress triage or checks out next from queue.
    With DROP_ID, checks out a specific drop (with safety check if triage in progress).
    With --force, restarts current drop from scratch.
    """
    try:
        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        # Check current triage state
        current_drop_id = triage.get_triage_state(conn)

        # Handle --force flag
        if force:
            if not current_drop_id:
                click.echo("Error: No triage in progress to restart.", err=True)
                sys.exit(1)

            # Get current status
            status = triage.get_drop_triage_status(current_drop_id, conn)
            click.echo(f"⚠ Discarding triage progress for {current_drop_id}")
            click.echo(
                f"  {status['classified_entries']}/{status['total_entries']} entries already classified"
            )
            click.echo()

            # Clear triage state and re-checkout same drop
            conn.execute("DELETE FROM triage_state")
            conn.commit()
            drop_id = current_drop_id

        # Handle explicit drop_id
        if drop_id and not force:
            # Safety check: warn if switching drops
            if current_drop_id and current_drop_id != drop_id:
                status = triage.get_drop_triage_status(current_drop_id, conn)
                remaining = status["total_entries"] - status["classified_entries"]

                click.echo(f"⚠ Triage in progress: {current_drop_id}")
                click.echo(f"  {remaining} entries remain in _triage/")
                click.echo()
                click.echo(f"Switch to {drop_id}? This will discard uncommitted work.")

                if not click.confirm("Continue?", default=False):
                    click.echo("Cancelled.")
                    conn.close()
                    return

                # Clear current triage state
                conn.execute("DELETE FROM triage_state")
                conn.commit()

        # Handle no drop_id (queue mode)
        if not drop_id:
            # Check if resuming in-progress triage
            if current_drop_id:
                status = triage.get_drop_triage_status(current_drop_id, conn)
                remaining = status["total_entries"] - status["classified_entries"]

                click.echo(f"Resuming: {current_drop_id}")
                click.echo(
                    f"{remaining} entries remain in _triage/ ({status['classified_entries']} already classified)"
                )

                conn.close()
                return

            # Get next from queue
            next_drop_id = triage.get_next_untriaged_drop(conn)
            if not next_drop_id:
                click.echo("✓ All drops triaged! Queue clear.")
                conn.close()
                return

            drop_id = next_drop_id

        # Perform checkout
        d = triage.triage_checkout(
            drop_id, wh.root, wh.history_dir, wh.triage_dir, conn
        )

        # Show checkout info
        if current_drop_id != drop_id or force:
            click.echo(f"Checking out: {d.id}")
            click.echo(f"Message: {d.message}")
            click.echo()

        click.echo(f"Checked out {len(d.entries)} files to _triage/")

        conn.close()

    except triage.TriageError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@triage_group.command("sync")
def triage_sync_cmd():
    """Finalize triage and create classifications."""
    try:
        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        result = triage.triage_sync(wh.root, wh.triage_dir, wh.history_dir, conn)

        if result["filed"] > 0:
            click.echo(f"✓ Filed: {result['filed']} entries")

        if result["excluded"] > 0:
            click.echo(f"✓ Excluded: {result['excluded']} entries")

        if result["skipped"] > 0:
            click.echo(f"→ {result['skipped']} entries remain in _triage/")

        if result["ambiguous"]:
            click.echo("⚠ Ambiguous files (skipped):")
            for path in result["ambiguous"]:
                click.echo(f"  - {path}")

        # Check if drop is complete
        if result["skipped"] == 0:
            click.echo()
            drop_id = triage.get_triage_state(conn)
            if not drop_id:  # Triage was cleared
                click.echo("Triage complete!")

        conn.close()

    except triage.NoTriageInProgressError:
        click.echo("Error: No triage in progress.", err=True)
        sys.exit(1)
    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@triage_group.command("suggest")
def triage_suggest_cmd():
    """Auto-classify known files from triage to staging."""
    try:
        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        result = triage.triage_suggest(wh.triage_dir, wh.staging_dir, conn)

        auto_classified = result["auto_classified"]
        needs_manual = result["needs_manual"]

        if auto_classified:
            click.echo(
                f"✓ {len(auto_classified)} files auto-classified (known content)"
            )

            # Group by category for display
            by_category = {}
            for filename, category in auto_classified:
                if category not in by_category:
                    by_category[category] = []
                by_category[category].append(filename)

            for category, files in sorted(by_category.items()):
                click.echo(f"  → {len(files)} to {category}/")

            click.echo()
            click.echo(f"→ {len(auto_classified)} files staged in _staging/ for review")

        if needs_manual:
            click.echo(
                f"→ {len(needs_manual)} files remain in _triage/ (need manual classification)"
            )

        if auto_classified:
            click.echo()
            click.echo("Review staged files and adjust before running:")
            click.echo("  dwh triage merge")

        conn.close()

    except triage.NoTriageInProgressError:
        click.echo("Error: No triage in progress.", err=True)
        sys.exit(1)
    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@triage_group.command("merge")
def triage_merge_cmd():
    """Merge staged files to warehouse."""
    try:
        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        result = triage.triage_merge(wh.staging_dir, wh.root, wh.history_dir, conn)

        merged = result["merged"]

        if merged:
            click.echo(f"✓ Merged {len(merged)} files")

            # Group by category for display
            by_category = {}
            for filename, category in merged:
                if category not in by_category:
                    by_category[category] = []
                by_category[category].append(filename)

            for category, files in sorted(by_category.items()):
                click.echo(f"  {category}/: {len(files)} files")

            click.echo()
            click.echo("Classification records written to history.")
            click.echo("_staging/ cleared.")
        else:
            click.echo("No files in staging to merge.")

        conn.close()

    except triage.NoTriageInProgressError:
        click.echo("Error: No triage in progress.", err=True)
        sys.exit(1)
    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@triage_group.command("status")
def triage_status_cmd():
    """Show triage queue status."""
    try:
        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        # Get current triage state
        current_drop_id = triage.get_triage_state(conn)

        # Get all drops in LIFO order (newest first)
        drops = conn.execute(
            """SELECT id, message, created_at FROM drops ORDER BY created_at DESC"""
        ).fetchall()

        if not drops:
            click.echo("No drops in warehouse.")
            return

        # Display queue
        click.echo(f"Triage Queue ({len(drops)} drops):")
        click.echo()

        # Count by status for summary
        complete_count = 0
        complete_entries = 0
        in_progress_count = 0
        in_progress_classified = 0
        in_progress_total = 0
        pending_count = 0
        pending_entries = 0

        for drop_row in drops:
            drop_id = drop_row["id"]
            message = drop_row["message"]
            status_info = triage.get_drop_triage_status(drop_id, conn)

            total = status_info["total_entries"]
            classified = status_info["classified_entries"]
            status = status_info["status"]

            # Status indicator
            if status == "complete":
                indicator = "✓"
                complete_count += 1
                complete_entries += total
            elif status == "in_progress":
                if drop_id == current_drop_id:
                    indicator = "→"
                else:
                    indicator = "→"
                in_progress_count += 1
                in_progress_classified += classified
                in_progress_total += total
            else:  # pending
                indicator = "⏳"
                pending_count += 1
                pending_entries += total

            # Format entry count
            if status == "complete":
                entry_str = f"({total}/{total} complete)"
            elif status == "in_progress":
                entry_str = f"({classified}/{total} classified)"
            else:  # pending
                entry_str = f"({total} pending)"

            # In progress marker
            progress_marker = " ← IN PROGRESS" if drop_id == current_drop_id else ""

            # Truncate drop_id for display
            drop_id_short = drop_id[:23] if len(drop_id) > 23 else drop_id

            # Truncate message for display
            message_short = message[:30] if len(message) > 30 else message

            click.echo(
                f"{indicator} {drop_id_short:<23} {message_short:<30} {entry_str}{progress_marker}"
            )

        # Summary
        click.echo()
        click.echo("Summary:")
        if complete_count > 0:
            click.echo(
                f"- Complete: {complete_count} drops ({complete_entries} entries)"
            )
        if in_progress_count > 0:
            click.echo(
                f"- In progress: {in_progress_count} drop(s) ({in_progress_classified}/{in_progress_total} entries)"
            )
        if pending_count > 0:
            click.echo(f"- Pending: {pending_count} drops ({pending_entries} entries)")

        conn.close()

    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.group()
def warehouse_group():
    """Manage warehouses."""
    pass


warehouse_group.name = "warehouse"


@warehouse_group.command("list")
def warehouse_list():
    """List all registered warehouses."""
    from dwh.global_config import GlobalConfig

    global_config = GlobalConfig.load()

    if not global_config.warehouses:
        click.echo("No warehouses registered.")
        click.echo()
        click.echo("Initialize a warehouse with:")
        click.echo("  dwh init <path>")
        return

    # Print header
    click.echo(f"{'NAME':<15} {'PATH':<50} {'SELECTED'}")
    click.echo("-" * 70)

    # Print warehouses
    for name, wh in global_config.warehouses.items():
        is_selected = name == global_config.default_warehouse
        selected_mark = "*" if is_selected else ""
        click.echo(f"{name:<15} {str(wh.path):<50} {selected_mark}")


@warehouse_group.command("select")
@click.argument("name")
def warehouse_select(name: str):
    """Select the active warehouse."""
    from dwh.global_config import GlobalConfig

    global_config = GlobalConfig.load()

    if name not in global_config.warehouses:
        click.echo(f"Error: Warehouse '{name}' not found.", err=True)
        click.echo()
        click.echo("Available warehouses:")
        for wh_name in global_config.warehouses.keys():
            click.echo(f"  {wh_name}")
        sys.exit(1)

    global_config.default_warehouse = name
    global_config.save()

    wh_path = global_config.warehouses[name].path
    click.echo(f"Selected warehouse: {name}")
    click.echo(f"  Path: {wh_path}")


@warehouse_group.command("add")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--name", help="Registry name (defaults to directory name)")
def warehouse_add(path: Path, name: str | None):
    """Add an existing warehouse to the registry."""
    from dwh.global_config import GlobalConfig

    path = path.resolve()
    wh = warehouse.Warehouse(path)

    # Verify it's a valid warehouse
    if not wh.dwh_dir.exists():
        click.echo("Error: Not a warehouse directory (missing .dwh/)", err=True)
        click.echo(f"Path: {path}")
        click.echo()
        click.echo("Initialize a warehouse with:")
        click.echo("  dwh init <path>")
        sys.exit(1)

    # Determine registry name
    registry_name = name or path.name

    # Load global config
    global_config = GlobalConfig.load()

    # Check if already registered
    if registry_name in global_config.warehouses:
        existing_path = global_config.warehouses[registry_name].path
        if existing_path == path:
            click.echo(
                f"Warehouse '{registry_name}' is already registered at this path."
            )
            return
        else:
            click.echo(
                f"Error: Name '{registry_name}' already registered at {existing_path}",
                err=True,
            )
            click.echo()
            click.echo("Use a different name with --name option:")
            click.echo(f"  dwh warehouse register {path} --name <unique-name>")
            sys.exit(1)

    # Read warehouse config for display name
    try:
        import tomllib

        if wh.config_path.exists():
            with open(wh.config_path, "rb") as f:
                config = tomllib.load(f)
                display_name = config.get("name", registry_name)
        else:
            display_name = registry_name
    except Exception:
        display_name = registry_name

    # Register warehouse
    global_config.add_warehouse(
        name=registry_name, path=path, display_name=display_name
    )
    global_config.save()

    click.echo(f"Registered warehouse: {registry_name}")
    click.echo(f"  Path: {path}")
    click.echo(f"  Display name: {display_name}")

    # Auto-select if first warehouse
    if len(global_config.warehouses) == 1:
        global_config.default_warehouse = registry_name
        global_config.save()
        click.echo()
        click.echo("Selected as active warehouse (first registered)")


@warehouse_group.command("remove")
@click.argument("name")
def warehouse_remove(name: str):
    """Remove a warehouse from the registry."""
    from dwh.global_config import GlobalConfig

    global_config = GlobalConfig.load()

    if name not in global_config.warehouses:
        click.echo(f"Error: Warehouse '{name}' not found.", err=True)
        click.echo()
        click.echo("Available warehouses:")
        for wh_name in global_config.warehouses.keys():
            click.echo(f"  {wh_name}")
        sys.exit(1)

    wh_path = global_config.warehouses[name].path
    was_selected = global_config.default_warehouse == name

    global_config.remove_warehouse(name)
    global_config.save()

    click.echo(f"Removed warehouse: {name}")
    click.echo(f"  Path: {wh_path}")

    if was_selected:
        click.echo()
        click.echo("Note: This was the selected warehouse.")
        if global_config.warehouses:
            click.echo("Use 'dwh warehouse select <name>' to choose another.")
        else:
            click.echo("No warehouses remain in registry.")


def _collect_commands(
    cmd: click.Command, prefix: str = "", override_name: str | None = None
) -> list[tuple[str, str, list, list]]:
    """Collect all commands as (full_name, help, arguments, options) tuples."""
    results = []
    # Use override_name if provided (for renamed commands), otherwise use cmd.name
    cmd_name = override_name if override_name else cmd.name
    name = f"{prefix} {cmd_name}".strip() if prefix else cmd_name

    if isinstance(cmd, click.Group):
        for subcmd_name in sorted(cmd.list_commands(None)):
            subcmd = cmd.get_command(None, subcmd_name)
            if subcmd:
                # Pass subcmd_name as override to preserve registered names
                results.extend(_collect_commands(subcmd, name, subcmd_name))
    else:
        # Get first line of help only
        help_text = (cmd.help or "").split("\n")[0]
        arguments = []
        options = []
        for param in cmd.params:
            if isinstance(param, click.Argument):
                arg_name = param.name.upper()
                # Show default if present
                if param.default is not None:
                    arguments.append(f"[{arg_name}]")
                elif param.required:
                    arguments.append(arg_name)
                else:
                    arguments.append(f"[{arg_name}]")
            elif isinstance(param, click.Option) and param.help:
                opts = ", ".join(param.opts)
                options.append((opts, param.help))
        results.append((name, help_text, arguments, options))

    return results


@main.command()
@click.argument("path", required=False, default=".")
def audit(path: str):
    """Audit warehouse filesystem consistency.

    Checks for orphaned files, missing files, relocated files, and duplicates.
    """
    try:
        from dwh import audit as audit_module

        wh = warehouse.resolve_warehouse()
        conn = wh.connect()

        audit_path = Path(path)
        result = audit_module.audit_warehouse(wh.root, audit_path, conn)

        # Display header
        click.echo()
        click.echo("Warehouse Audit Report")
        click.echo("=" * 60)
        if audit_path == Path("."):
            click.echo(
                f"Audited: {result.total_files} files, {result.total_documents} documents"
            )
        else:
            click.echo(f"Audited: {path}")
            click.echo(
                f"Files: {result.total_files}, Documents: {result.total_documents}"
            )
        click.echo()

        # Calculate correct count
        correct_count = result.total_files - len(result.orphans) - len(result.relocated)

        # Check if there are any issues
        total_issues = (
            len(result.orphans)
            + len(result.missing)
            + len(result.relocated)
            + len(result.duplicates)
        )

        if total_issues == 0:
            click.echo("✓ Warehouse is consistent!")
            click.echo("  All files correctly tracked, no orphans or missing files.")
            conn.close()
            sys.exit(0)

        click.echo(f"✓ {correct_count} files correctly tracked")
        click.echo()
        click.echo(f"Issues Found: {total_issues}")
        click.echo("━" * 60)

        # Display orphaned files
        if result.orphans:
            click.echo()
            click.echo(f"Orphaned Files ({len(result.orphans)}):")
            click.echo("  Files in warehouse but not tracked in database")
            click.echo()
            for orphan in result.orphans:
                size_kb = orphan.size / 1024
                click.echo(f"  {orphan.path}")
                click.echo(f"    Hash: {orphan.hash[:16]}...")
                click.echo(f"    Size: {size_kb:.1f} KB")
                click.echo("    → Import this file to track it")

        # Display missing files
        if result.missing:
            click.echo()
            click.echo(f"Missing Files ({len(result.missing)}):")
            click.echo("  Documents in database but files not found on disk")
            click.echo()
            for missing in result.missing:
                click.echo(f"  {missing.path}")
                click.echo(
                    f"    Document: {missing.document_id}, Entry: {missing.entry_id}"
                )
                click.echo(f"    Hash: {missing.hash[:16]}...")
                click.echo(
                    "    → File needs to be restored (restore command coming soon)"
                )

        # Display relocated files
        if result.relocated:
            click.echo()
            click.echo(f"Relocated Files ({len(result.relocated)}):")
            click.echo("  Files moved from recorded location")
            click.echo()
            for reloc in result.relocated:
                click.echo(f"  {reloc.actual_path} (expected: {reloc.expected_path})")
                click.echo(f"    Document: {reloc.document_id}")
                click.echo(f"    Hash: {reloc.hash[:16]}...")
                click.echo("    → File moved outside DWH, database not updated")
                click.echo(
                    f"    → Restore: mv {reloc.actual_path} {reloc.expected_path}"
                )

        # Display duplicates
        if result.duplicates:
            click.echo()
            click.echo(
                f"Duplicates ({len(result.duplicates)} blob(s) in multiple locations):"
            )
            click.echo("  Same content in multiple categories")
            click.echo()
            for dup in result.duplicates:
                click.echo(
                    f"  Hash: {dup.hash[:16]}... ({len(dup.locations)} locations)"
                )
                for i, (loc, doc_id) in enumerate(dup.locations, 1):
                    if doc_id:
                        click.echo(f"    {i}. {loc} (Document: {doc_id})")
                    else:
                        click.echo(f"    {i}. {loc} (orphaned)")
                click.echo(
                    "    → Both classifications may be valid (same document in multiple categories)"
                )

        conn.close()

        # Exit with error code if issues found
        sys.exit(1)

    except audit_module.AuditError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
        sys.exit(2)
    except Exception as e:
        click.echo(f"Error during audit: {e}", err=True)
        sys.exit(2)


@main.command()
@click.pass_context
def man(ctx):
    """Print the complete manual (auto-generated from commands)."""
    root = ctx.find_root().command

    lines = ["DWH(1)", "", "NAME", "    dwh - Document Warehouse", ""]

    # Group commands by top-level
    groups: dict[str, list] = {}
    for cmd_name in sorted(root.list_commands(ctx)):
        if cmd_name == "man":
            continue
        cmd = root.get_command(ctx, cmd_name)
        if cmd:
            # Pass cmd_name to preserve registered names (e.g., "drop" not "drop_cmd")
            commands = _collect_commands(cmd, override_name=cmd_name)
            if commands:
                groups[cmd_name] = commands

    lines.append("COMMANDS")

    for group_name, commands in groups.items():
        lines.append(f"\n  {group_name}:")
        for full_name, help_text, arguments, options in commands:
            # Show command with positional arguments
            args_str = " ".join(arguments)
            if args_str:
                lines.append(f"    dwh {full_name} {args_str}")
            else:
                lines.append(f"    dwh {full_name}")
            if help_text:
                lines.append(f"        {help_text}")
            for opt, opt_help in options:
                lines.append(f"        {opt}: {opt_help}")

    lines.extend(
        [
            "",
            "WAREHOUSE STRUCTURE",
            "    .dwh/                Hidden metadata directory",
            "        config.toml      Warehouse configuration",
            "        dwh.db           SQLite database (cache, can be rebuilt)",
            "",
            "    _history/            Event log (source of truth, backup required)",
            "        NNN_drop_*/      Drop directories (content-addressed storage)",
            "            receipt.json Drop metadata (actor, timestamp, message)",
            "            tree/        Original file structure (content-addressed)",
            "        NNN_classify.json Classification records",
            "",
            "    _triage/             Working directory (ephemeral)",
            "        Files staged for classification",
            "",
            "    <category>/          User-defined categories at warehouse root",
            "        Documents organized by category",
            "",
            "CONCEPTS",
            "    Drop        An import event with full provenance tracking",
            "    Entry       A file within a drop (content-addressed by SHA-256)",
            "    Blob        Deduplicated file content storage",
            "    Triage      Workflow for classifying imported files",
            "    Category    User-defined organizational structure",
            "    Rebuild     Reconstruct database from history (disaster recovery)",
            "",
            "SEE ALSO",
            "    dwh <command> --help",
        ]
    )

    click.echo("\n".join(lines))


if __name__ == "__main__":
    main()
