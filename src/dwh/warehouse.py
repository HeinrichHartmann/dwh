"""Warehouse path management and configuration."""

import sqlite3
from pathlib import Path

from dwh import db
from dwh.global_config import GlobalConfig


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


class NoWarehouseSpecifiedError(WarehouseError):
    """No warehouse selected."""

    def __init__(self):
        super().__init__(
            "No warehouse selected. Run 'dwh warehouse select <name>' to choose a warehouse. "
            "Use 'dwh warehouse list' to see available warehouses."
        )


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
        self.staging_dir = self.root / "_staging"

    def exists(self) -> bool:
        """Check if warehouse is initialized."""
        return self.dwh_dir.exists() and self.db_path.exists()

    def connect(self) -> sqlite3.Connection:
        """Connect to warehouse database."""
        if not self.exists():
            raise WarehouseNotFoundError(self.root)
        return db.connect(self.db_path)


def find_warehouse(
    start_path: Path | None = None, require_db: bool = True
) -> Warehouse:
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


def resolve_warehouse(
    start_dir: Path | None = None, require_db: bool = True
) -> Warehouse:
    """
    Resolve warehouse (simplified for v1).

    Priority:
    1. Current directory (if inside warehouse)
    2. Selected warehouse from global config
    3. Error

    Args:
        start_dir: Starting directory for current-dir search (defaults to cwd)
        require_db: If True, require database to exist. If False, only require .dwh/ directory.

    Returns:
        Warehouse instance

    Raises:
        NoWarehouseSpecifiedError: If no warehouse could be resolved
        WarehouseNotFoundError: If specified warehouse doesn't exist
    """
    if start_dir is None:
        start_dir = Path.cwd()

    # 1. Try current directory
    try:
        return find_warehouse(start_dir, require_db=require_db)
    except WarehouseNotFoundError:
        pass

    # 2. Try selected warehouse from config
    global_config = GlobalConfig.load()
    if global_config.default_warehouse:
        return resolve_warehouse_ref(
            global_config.default_warehouse, require_db=require_db
        )

    # 3. Error
    raise NoWarehouseSpecifiedError()


def resolve_warehouse_ref(ref: str, require_db: bool = True) -> Warehouse:
    """
    Resolve warehouse reference (name or path).

    - If ref is a path: use that path
    - If ref is a name: look up in registry

    Args:
        ref: Warehouse name (from registry) or path
        require_db: If True, require database to exist. If False, only require .dwh/ directory.

    Returns:
        Warehouse instance

    Raises:
        WarehouseNotFoundError: If warehouse doesn't exist
    """
    path = Path(ref).expanduser()

    # Try as path first
    if path.exists():
        warehouse = Warehouse(path)
        if require_db:
            if warehouse.exists():
                return warehouse
        else:
            if warehouse.dwh_dir.exists():
                return warehouse

    # Try as name in registry
    global_config = GlobalConfig.load()
    if ref in global_config.warehouses:
        wh_path = global_config.warehouses[ref].path
        warehouse = Warehouse(wh_path)
        if require_db:
            if warehouse.exists():
                return warehouse
        else:
            if warehouse.dwh_dir.exists():
                return warehouse

    # Not found
    raise WarehouseNotFoundError(Path(ref))
