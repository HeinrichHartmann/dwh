"""Warehouse path management and configuration."""

import sqlite3
from pathlib import Path

from dwh import db


class WarehouseError(Exception):
    """Base exception for warehouse errors."""
    pass


class WarehouseExistsError(WarehouseError):
    """Warehouse already exists at path."""
    def __init__(self, path: Path):
        super().__init__(f"Warehouse already exists at {path}")
        self.path = path


class WarehouseNotFoundError(WarehouseError):
    """Warehouse not found at path."""
    def __init__(self, path: Path):
        super().__init__(f"Warehouse not found at {path}")
        self.path = path


class Warehouse:
    """Warehouse interface providing paths and database access.

    Per ADR-003, the warehouse root IS the archive.
    Categories live at root, system directories use underscore prefix.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.dwh_dir = self.root / ".dwh"
        self.db_path = self.dwh_dir / "dwh.db"
        self.config_path = self.dwh_dir / "config.toml"
        # ADR-003: System directories at root with underscore prefix
        self.history_dir = self.root / "_history"
        self.triage_dir = self.root / "_triage"

    def exists(self) -> bool:
        """Check if warehouse is initialized."""
        return self.dwh_dir.exists() and self.db_path.exists()

    def connect(self) -> sqlite3.Connection:
        """Connect to warehouse database."""
        if not self.exists():
            raise WarehouseNotFoundError(self.root)
        return db.connect(self.db_path)


def find_warehouse(start_path: Path | None = None, require_db: bool = True) -> Warehouse:
    """Find warehouse by walking up from start_path.

    Args:
        start_path: Starting directory (defaults to cwd)
        require_db: If True, require database to exist. If False, only require .dwh/ directory.
    """
    current = (start_path or Path.cwd()).resolve()

    while True:
        warehouse = Warehouse(current)
        if require_db:
            if warehouse.exists():
                return warehouse
        else:
            # For rebuild, only require .dwh directory
            if warehouse.dwh_dir.exists():
                return warehouse

        if current.parent == current:
            # Reached filesystem root
            raise WarehouseNotFoundError(start_path or Path.cwd())

        current = current.parent
