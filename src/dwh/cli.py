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
@click.option("--name", help="Warehouse name (defaults to directory name)")
def init(path: Path, name: str | None):
    """Initialize a new warehouse."""
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

    except Exception as e:
        click.echo(f"Error initializing warehouse: {e}", err=True)
        sys.exit(1)


@main.command()
def rebuild():
    """Rebuild database from history."""
    try:
        wh = warehouse.find_warehouse(require_db=False)

        click.echo("Rebuilding database from history...")

        result = drop.rebuild_database(wh.history_dir, wh.db_path)

        click.echo(f"✓ Replayed {result['drops']} drops")
        click.echo(f"✓ Replayed {result['classifications']} classifications")
        click.echo("Database rebuild complete")

    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse.", err=True)
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
        wh = warehouse.find_warehouse()
        conn = wh.connect()

        result = drop.drop_import(list(paths), message, wh.root, wh.history_dir, conn)

        click.echo(f"Imported {len(result.entries)} files")
        click.echo(f"Drop ID: {result.id}")

        if result.auto_classified_count > 0:
            click.echo(
                f"✓ Auto-classified {result.auto_classified_count} files (already organized)"
            )

        conn.close()

    except warehouse.WarehouseNotFoundError:
        click.echo("Error: Not in a warehouse. Run 'dwh init' first.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error importing: {e}", err=True)
        sys.exit(1)


@drop_cmd.command("list")
def drop_list_cmd():
    """List all drops."""
    try:
        wh = warehouse.find_warehouse()
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
        wh = warehouse.find_warehouse()
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
        wh = warehouse.find_warehouse()

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
def triage_checkout(drop_id: str | None):
    """Checkout a drop for triage."""
    try:
        wh = warehouse.find_warehouse()
        conn = wh.connect()

        d = triage.triage_checkout(
            drop_id, wh.root, wh.history_dir, wh.triage_dir, conn
        )

        click.echo(f"Checked out drop {d.id} to triage/")
        click.echo(f"{len(d.entries)} files ready for triage")

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
        wh = warehouse.find_warehouse()
        conn = wh.connect()

        result = triage.triage_sync(wh.root, wh.triage_dir, wh.history_dir, conn)

        if result["classified"] > 0:
            click.echo(f"✓ Classified {result['classified']} files")

        if result["skipped"] > 0:
            click.echo(f"⚠ Skipped {result['skipped']} files still in triage/")

        if result["ambiguous"]:
            click.echo("⚠ Ambiguous files (skipped):")
            for path in result["ambiguous"]:
                click.echo(f"  - {path}")

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
