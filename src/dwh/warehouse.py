"""Warehouse initialization and structure management."""

from pathlib import Path
from typing import Optional
import tomllib

from dwh.db import init_db


class Warehouse:
    """Represents a document warehouse."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.dwh_dir = self.root / ".dwh"
        self.config_path = self.dwh_dir / "config.toml"
        self.db_path = self.dwh_dir / "dwh.db"
        self.store_dir = self.dwh_dir / "store" / "blob"
        self.inbox_dir = self.root / "inbox"
        self.originals_dir = self.root / "originals"
        self.pile_dir = self.originals_dir / "pile"

    @classmethod
    def find(cls, start_path: Optional[Path] = None) -> Optional["Warehouse"]:
        """Find warehouse by looking for .dwh/ in current or parent directories."""
        if start_path is None:
            start_path = Path.cwd()

        current = start_path.resolve()
        while current != current.parent:
            dwh_dir = current / ".dwh"
            if dwh_dir.exists() and dwh_dir.is_dir():
                return cls(current)
            current = current.parent
        return None

    def exists(self) -> bool:
        """Check if warehouse is initialized."""
        return self.dwh_dir.exists() and self.config_path.exists()

    def init(self) -> None:
        """Initialize warehouse directory structure."""
        if self.exists():
            raise ValueError(f"Warehouse already initialized at {self.root}")

        # Create directory structure
        self.dwh_dir.mkdir(parents=True, exist_ok=True)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        self.pile_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        init_db(self.db_path)

        # Create config
        config = {
            "version": "0.1.0",
            "warehouse": {
                "root": str(self.root),
            }
        }

        with open(self.config_path, "w") as f:
            # Simple TOML write for now
            f.write(f"version = \"{config['version']}\"\n\n")
            f.write("[warehouse]\n")
            f.write(f"root = \"{config['warehouse']['root']}\"\n")

    def load_config(self) -> dict:
        """Load warehouse configuration."""
        if not self.config_path.exists():
            raise ValueError(f"No warehouse found at {self.root}")

        with open(self.config_path, "rb") as f:
            return tomllib.load(f)
