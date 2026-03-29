"""Document Warehouse - metadata-centric document archival system."""

from dwh.cli import main as cli_main

__version__ = "0.1.0"


def main() -> None:
    """Entry point for the dwh command."""
    cli_main()
