"""Global DWH configuration and warehouse registry."""

import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class WarehouseRegistration:
    """A registered warehouse in the global config."""

    name: str
    path: Path
    display_name: str
    created_at: str


@dataclass
class GlobalConfig:
    """
    Global DWH configuration.

    Stored at ~/.config/dwh/config.toml (XDG standard).
    Contains warehouse registry and default warehouse setting.
    """

    default_warehouse: str | None = None
    warehouses: dict[str, WarehouseRegistration] = field(default_factory=dict)

    @staticmethod
    def load() -> "GlobalConfig":
        """Load global config from disk."""
        config_path = get_config_path()

        if not config_path.exists():
            return GlobalConfig()

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        # Parse warehouses
        warehouses = {}
        for name, wh_data in data.get("warehouses", {}).items():
            warehouses[name] = WarehouseRegistration(
                name=name,
                path=Path(wh_data["path"]).expanduser(),
                display_name=wh_data.get("name", name),
                created_at=wh_data.get("created_at", ""),
            )

        return GlobalConfig(
            default_warehouse=data.get("default_warehouse"),
            warehouses=warehouses,
        )

    def save(self):
        """Save global config to disk."""
        config_path = get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Build TOML content
        lines = []

        if self.default_warehouse:
            lines.append(f'default_warehouse = "{self.default_warehouse}"')
            lines.append("")

        if self.warehouses:
            for name, wh in self.warehouses.items():
                lines.append(f"[warehouses.{name}]")
                lines.append(f'path = "{wh.path}"')
                lines.append(f'name = "{wh.display_name}"')
                lines.append(f'created_at = "{wh.created_at}"')
                lines.append("")

        config_path.write_text("\n".join(lines))

    def add_warehouse(self, name: str, path: Path, display_name: str | None = None):
        """Add a warehouse to the registry."""
        self.warehouses[name] = WarehouseRegistration(
            name=name,
            path=path.resolve(),
            display_name=display_name or name,
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def remove_warehouse(self, name: str):
        """Remove a warehouse from the registry."""
        if name in self.warehouses:
            del self.warehouses[name]

        # Clear default if it was the removed warehouse
        if self.default_warehouse == name:
            self.default_warehouse = None


def get_config_path() -> Path:
    """
    Get global config path using XDG standard.

    Returns ~/.config/dwh/config.toml or $XDG_CONFIG_HOME/dwh/config.toml
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")

    if xdg_config_home:
        return Path(xdg_config_home) / "dwh" / "config.toml"
    else:
        return Path.home() / ".config" / "dwh" / "config.toml"
