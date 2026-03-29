"""Command-line interface for dwh."""

from pathlib import Path

import click

from dwh.warehouse import Warehouse
from dwh.db import get_connection
from dwh.storage import import_files


@click.group()
@click.version_option()
def main():
    """Document Warehouse - metadata-centric document archival system."""
    pass


@main.group()
def store():
    """Manage blob storage operations."""
    pass


@main.group()
def originals():
    """Manage originals tree operations."""
    pass


@main.command()
@click.argument("path", default=".", type=click.Path())
def init(path):
    """Initialize a document warehouse at PATH."""
    warehouse_path = Path(path).resolve()
    click.echo(f"Initializing document warehouse at {warehouse_path}...")

    warehouse = Warehouse(warehouse_path)

    if warehouse.exists():
        click.echo(f"✗ Warehouse already exists at {warehouse_path}", err=True)
        raise click.Abort()

    try:
        warehouse.init()
        click.echo(f"✓ Created .dwh/")
        click.echo(f"✓ Created inbox/")
        click.echo(f"✓ Created originals/pile/")
        click.echo(f"✓ Initialized database")
        click.echo()
        click.echo(f"Warehouse initialized at {warehouse_path}")
    except Exception as e:
        click.echo(f"✗ Failed to initialize warehouse: {e}", err=True)
        raise click.Abort()


@store.command(name="import")
@click.option("-m", "--message", required=True, help="Import message describing the source/purpose")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
def import_cmd(message, paths):
    """Import files with transaction tracking.

    PATHS can be files or directories. Directories are scanned recursively.

    Example:
        dwh store import -m "Bank statements 2023" inbox/statements/
        dwh store import -m "Tax documents" file1.pdf file2.pdf folder/
    """
    warehouse = Warehouse.find()
    if not warehouse:
        click.echo("✗ No warehouse found. Run 'dwh init' first.", err=True)
        raise click.Abort()

    # Convert string paths to Path objects
    path_objects = [Path(p).resolve() for p in paths]

    click.echo(f"Importing from {len(path_objects)} path(s)...")

    try:
        conn = get_connection(warehouse.db_path)
        import_id, stored_count = import_files(
            path_objects,
            message,
            warehouse.root,
            warehouse.store_dir,
            conn
        )
        conn.close()

        click.echo(f"✓ Imported {stored_count} file(s)")
        click.echo(f"  Transaction ID: {import_id}")
        click.echo(f"  Message: {message}")

    except ValueError as e:
        click.echo(f"✗ {e}", err=True)
        raise click.Abort()
    except Exception as e:
        click.echo(f"✗ Import failed: {e}", err=True)
        raise click.Abort()


@originals.command()
@click.option("-y", "--yes", is_flag=True, help="Auto-confirm without prompting")
@click.option("--force", is_flag=True, help="Store + capture unknown files")
def capture(yes, force):
    """Capture placements and infer metadata from originals/ tree."""
    click.echo("Scanning originals/ tree...")
    # TODO: Implement capture


@originals.command()
@click.option("--dry-run", is_flag=True, help="Show what would change")
def sync(dry_run):
    """Regenerate originals/ from metadata."""
    if dry_run:
        click.echo("Dry run: would sync originals/")
    else:
        click.echo("Syncing originals/...")
    # TODO: Implement sync


@originals.command()
def status():
    """Show drift between metadata and filesystem."""
    click.echo("Checking originals/ status...")
    # TODO: Implement status


@main.command()
@click.option("--state", help="Filter by state")
@click.option("--domain", help="Filter by domain")
def list(state, domain):
    """List documents."""
    click.echo("Listing documents...")
    # TODO: Implement list


@main.command()
@click.argument("document_id")
def show(document_id):
    """Show document details and metadata."""
    click.echo(f"Showing document {document_id}...")
    # TODO: Implement show


@main.command()
@click.argument("document_id")
def open(document_id):
    """Open document in default application."""
    click.echo(f"Opening document {document_id}...")
    # TODO: Implement open


if __name__ == "__main__":
    main()
