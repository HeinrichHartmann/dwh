# ADR-004: Warehouse Registry and Out-of-Tree Operations

**Status:** Proposed
**Date:** 2026-03-29

## Context

Currently, DWH requires you to be inside a warehouse directory to run commands (like Git). This creates friction for document management workflows:

**Current behavior:**
```bash
cd ~/Downloads
dwh drop import -m "Tax docs" *.pdf
# Error: Not in a warehouse. Run 'dwh init' first.

# Must do:
cd ~/Documents
dwh drop import -m "Tax docs" ~/Downloads/*.pdf
```

**Problems:**

1. **Workflow friction** - Users often want to import from their current location
2. **Multiple warehouses** - Users may have multiple warehouses (personal, work, archive) with no way to switch between them
3. **No default warehouse** - Can't set a preferred warehouse for imports
4. **Discovery** - No way to list all warehouses on the system

**Use cases:**

1. Import from Downloads without leaving that directory
2. Maintain separate work and personal warehouses
3. Import to specific warehouse by name: `dwh import --warehouse work ...`
4. Set a default warehouse for convenience
5. List and manage all warehouses

## Decision

Add a **warehouse registry** and support **out-of-tree operations**.

### Warehouse Registry

**Location:** `~/.config/dwh/config.toml` (XDG standard)

**Format:**
```toml
# Global DWH configuration
default_warehouse = "personal"  # Optional

[warehouses.personal]
path = "/Users/hhartmann/Documents"
name = "Personal Documents"
created_at = "2026-03-29T14:32:11Z"

[warehouses.work]
path = "/Users/hhartmann/Work/Archive"
name = "Work Archive"
created_at = "2026-03-29T15:00:00Z"
```

**Registration:**

Warehouses are automatically registered on `dwh init`:

```bash
dwh init ~/Documents --name "Personal Documents"
# Initializes warehouse AND registers it in ~/.config/dwh/config.toml
```

Manual registration for existing warehouses:

```bash
dwh warehouse register ~/Documents --name personal
```

### Warehouse Selection

**Priority order:**

1. **Inside warehouse directory** - Use that warehouse (current behavior)
   ```bash
   cd ~/Documents
   dwh drop import -m "Tax docs" ~/Downloads/*.pdf
   # Uses ~/Documents warehouse (detected via .dwh/)
   ```

2. **`--warehouse` flag** - Explicit warehouse selection
   ```bash
   dwh --warehouse ~/Documents drop import -m "Tax docs" *.pdf
   dwh --warehouse work drop import -m "Tax docs" *.pdf  # By name
   ```

3. **`DWH_WAREHOUSE` environment variable**
   ```bash
   export DWH_WAREHOUSE=~/Documents
   dwh drop import -m "Tax docs" *.pdf
   ```

4. **Default warehouse** - From config
   ```bash
   # ~/.config/dwh/config.toml has default_warehouse = "personal"
   dwh drop import -m "Tax docs" *.pdf
   # Uses personal warehouse
   ```

5. **Error** - No warehouse found
   ```bash
   dwh drop import -m "Tax docs" *.pdf
   # Error: No warehouse specified. Use --warehouse or set a default.
   # Run 'dwh warehouse list' to see available warehouses.
   ```

### CLI Changes

**Global flag (before subcommand):**

```bash
dwh --warehouse <name|path> <command> [args]
```

**New warehouse management commands:**

```bash
# List all registered warehouses
dwh warehouse list

# Output:
# NAME       PATH                      DEFAULT
# personal   ~/Documents               *
# work       ~/Work/Archive

# Set default warehouse
dwh warehouse default personal

# Register existing warehouse
dwh warehouse register ~/Archive --name archive

# Unregister warehouse (doesn't delete it)
dwh warehouse unregister archive

# Show current warehouse
dwh warehouse current
# ~/Documents (personal)
```

**Modified init command:**

```bash
dwh init [path] --name <name>
# Now registers warehouse in global config

# Examples:
dwh init ~/Documents --name personal
dwh init ~/Work/Archive --name work
```

### Implementation Details

**Config file structure:**

```python
# config.py (global config, separate from warehouse config)

@dataclass
class WarehouseRegistration:
    name: str
    path: Path
    display_name: str
    created_at: str

@dataclass
class GlobalConfig:
    default_warehouse: str | None
    warehouses: dict[str, WarehouseRegistration]

    @staticmethod
    def load() -> 'GlobalConfig':
        config_path = get_config_path()  # ~/.config/dwh/config.toml
        if not config_path.exists():
            return GlobalConfig(default_warehouse=None, warehouses={})

        with open(config_path, 'rb') as f:
            data = tomllib.load(f)

        # Parse warehouses
        warehouses = {}
        for name, wh_data in data.get('warehouses', {}).items():
            warehouses[name] = WarehouseRegistration(
                name=name,
                path=Path(wh_data['path']),
                display_name=wh_data.get('name', name),
                created_at=wh_data.get('created_at', '')
            )

        return GlobalConfig(
            default_warehouse=data.get('default_warehouse'),
            warehouses=warehouses
        )

    def save(self):
        config_path = get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {}
        if self.default_warehouse:
            data['default_warehouse'] = self.default_warehouse

        data['warehouses'] = {}
        for name, wh in self.warehouses.items():
            data['warehouses'][name] = {
                'path': str(wh.path),
                'name': wh.display_name,
                'created_at': wh.created_at
            }

        with open(config_path, 'w') as f:
            toml.dump(data, f)

def get_config_path() -> Path:
    """Get global config path using XDG standard."""
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME')
    if xdg_config_home:
        return Path(xdg_config_home) / 'dwh' / 'config.toml'
    else:
        return Path.home() / '.config' / 'dwh' / 'config.toml'
```

**Warehouse resolution:**

```python
# warehouse.py

def resolve_warehouse(
    warehouse_arg: str | None,
    start_dir: Path | None = None
) -> Warehouse:
    """
    Resolve warehouse using priority order:
    1. Current directory (if inside warehouse)
    2. warehouse_arg (name or path)
    3. DWH_WAREHOUSE env var
    4. Default from global config
    5. Error
    """
    # 1. Try current directory
    if start_dir is None:
        start_dir = Path.cwd()

    try:
        return find_warehouse(start_dir)
    except WarehouseNotFoundError:
        pass

    # 2. Try --warehouse flag
    if warehouse_arg:
        return resolve_warehouse_ref(warehouse_arg)

    # 3. Try environment variable
    env_warehouse = os.environ.get('DWH_WAREHOUSE')
    if env_warehouse:
        return resolve_warehouse_ref(env_warehouse)

    # 4. Try default from config
    global_config = GlobalConfig.load()
    if global_config.default_warehouse:
        return resolve_warehouse_ref(global_config.default_warehouse)

    # 5. Error
    raise NoWarehouseSpecifiedError(
        "No warehouse specified. Use --warehouse, set DWH_WAREHOUSE, "
        "or configure a default with 'dwh warehouse default <name>'"
    )

def resolve_warehouse_ref(ref: str) -> Warehouse:
    """
    Resolve warehouse reference (name or path).

    - If ref is a path: use that path
    - If ref is a name: look up in registry
    """
    path = Path(ref)

    # Try as path first
    if path.exists() and (path / '.dwh').exists():
        return Warehouse(path)

    # Try as name in registry
    global_config = GlobalConfig.load()
    if ref in global_config.warehouses:
        wh_path = global_config.warehouses[ref].path
        return Warehouse(wh_path)

    raise WarehouseNotFoundError(
        f"Warehouse '{ref}' not found. "
        f"Run 'dwh warehouse list' to see available warehouses."
    )
```

**CLI modification:**

```python
# cli.py

@click.group()
@click.option('--warehouse', '-w', help="Warehouse name or path")
@click.pass_context
def main(ctx, warehouse):
    """Document Warehouse - metadata-centric document archival."""
    # Store warehouse reference in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj['warehouse_ref'] = warehouse

@main.command()
@click.option("-m", "--message", required=True)
@click.argument("paths", nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.pass_context
def drop_import_cmd(ctx, message: str, paths: tuple[Path, ...]):
    """Import files into the warehouse."""
    try:
        # Resolve warehouse using context
        wh = resolve_warehouse(ctx.obj['warehouse_ref'])
        conn = wh.connect()

        # ... rest of import logic
    except NoWarehouseSpecifiedError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
```

### New Commands

```python
# cli.py - warehouse management commands

@main.group()
def warehouse_group():
    """Manage warehouses."""
    pass

warehouse_group.name = "warehouse"

@warehouse_group.command("list")
def warehouse_list():
    """List all registered warehouses."""
    global_config = GlobalConfig.load()

    if not global_config.warehouses:
        click.echo("No warehouses registered.")
        click.echo("Run 'dwh init <path>' to create a warehouse.")
        return

    # Print header
    click.echo(f"{'NAME':<15} {'PATH':<40} {'DEFAULT'}")
    click.echo("-" * 60)

    for name, wh in global_config.warehouses.items():
        is_default = name == global_config.default_warehouse
        default_mark = "*" if is_default else ""
        click.echo(f"{name:<15} {str(wh.path):<40} {default_mark}")

@warehouse_group.command("default")
@click.argument("name")
def warehouse_default(name: str):
    """Set default warehouse."""
    global_config = GlobalConfig.load()

    if name not in global_config.warehouses:
        click.echo(f"Error: Warehouse '{name}' not found.", err=True)
        click.echo("Run 'dwh warehouse list' to see available warehouses.")
        sys.exit(1)

    global_config.default_warehouse = name
    global_config.save()

    click.echo(f"Set default warehouse to: {name}")

@warehouse_group.command("register")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--name", required=True, help="Warehouse name")
def warehouse_register(path: Path, name: str):
    """Register an existing warehouse."""
    path = path.resolve()

    # Verify it's a warehouse
    if not (path / '.dwh').exists():
        click.echo(f"Error: {path} is not a warehouse.", err=True)
        click.echo("Run 'dwh init' to initialize a warehouse first.")
        sys.exit(1)

    # Add to registry
    global_config = GlobalConfig.load()

    if name in global_config.warehouses:
        click.echo(f"Error: Warehouse '{name}' already registered.", err=True)
        sys.exit(1)

    wh_config = Warehouse(path).config

    global_config.warehouses[name] = WarehouseRegistration(
        name=name,
        path=path,
        display_name=wh_config.name,
        created_at=datetime.now(timezone.utc).isoformat()
    )
    global_config.save()

    click.echo(f"Registered warehouse '{name}' at {path}")

@warehouse_group.command("unregister")
@click.argument("name")
def warehouse_unregister(name: str):
    """Unregister a warehouse (does not delete it)."""
    global_config = GlobalConfig.load()

    if name not in global_config.warehouses:
        click.echo(f"Error: Warehouse '{name}' not found.", err=True)
        sys.exit(1)

    del global_config.warehouses[name]

    # Clear default if it was the unregistered warehouse
    if global_config.default_warehouse == name:
        global_config.default_warehouse = None

    global_config.save()

    click.echo(f"Unregistered warehouse: {name}")

@warehouse_group.command("current")
@click.pass_context
def warehouse_current(ctx):
    """Show current warehouse."""
    try:
        wh = resolve_warehouse(ctx.obj['warehouse_ref'])

        # Try to find name in registry
        global_config = GlobalConfig.load()
        name = None
        for wh_name, wh_reg in global_config.warehouses.items():
            if wh_reg.path == wh.root:
                name = wh_name
                break

        if name:
            click.echo(f"{wh.root} ({name})")
        else:
            click.echo(f"{wh.root} (unregistered)")

    except NoWarehouseSpecifiedError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
```

**Modified init command:**

```python
@main.command()
@click.argument("path", type=click.Path(path_type=Path), default=".")
@click.option("--name", help="Warehouse name (for display and registry)")
@click.option("--register-as", help="Registry name (defaults to last path component)")
@click.option("--history-dir", default="_history")
@click.option("--triage-dir", default="_triage")
def init(path: Path, name: str | None, register_as: str | None,
         history_dir: str, triage_dir: str):
    """Initialize a new warehouse."""
    path = path.resolve()

    # ... existing init logic ...

    # Register in global config
    if register_as is None:
        register_as = path.name

    global_config = GlobalConfig.load()

    if register_as in global_config.warehouses:
        click.echo(f"Warning: Warehouse '{register_as}' already registered.")
    else:
        global_config.warehouses[register_as] = WarehouseRegistration(
            name=register_as,
            path=path,
            display_name=name or path.name,
            created_at=datetime.now(timezone.utc).isoformat()
        )
        global_config.save()

        click.echo(f"Registered as: {register_as}")

    # Prompt to set as default if it's the first warehouse
    if len(global_config.warehouses) == 1 and not global_config.default_warehouse:
        if click.confirm(f"Set '{register_as}' as default warehouse?", default=True):
            global_config.default_warehouse = register_as
            global_config.save()
            click.echo(f"Set default warehouse to: {register_as}")
```

## Examples

### Example 1: Multiple Warehouses

```bash
# Initialize personal warehouse
dwh init ~/Documents --name "Personal Documents" --register-as personal
# Set 'personal' as default warehouse? [Y/n] y

# Initialize work warehouse
dwh init ~/Work/Archive --name "Work Archive" --register-as work

# List warehouses
dwh warehouse list
# NAME       PATH                      DEFAULT
# personal   ~/Documents               *
# work       ~/Work/Archive

# Import to personal (default)
cd ~/Downloads
dwh drop import -m "Bank statements" statements/*.pdf
# Uses personal warehouse (default)

# Import to work warehouse
dwh --warehouse work drop import -m "Contracts" contracts/*.pdf

# Or use environment variable
export DWH_WAREHOUSE=work
dwh drop import -m "Contracts" contracts/*.pdf
```

### Example 2: Out-of-Tree Operations

```bash
# Old way (must cd to warehouse)
cd ~/Documents
dwh drop import -m "Tax docs" ~/Downloads/taxes/*.pdf

# New way (from anywhere)
cd ~/Downloads
dwh drop import -m "Tax docs" taxes/*.pdf
# Uses default warehouse

# Or explicit
dwh --warehouse personal drop import -m "Tax docs" taxes/*.pdf
```

### Example 3: Registry Management

```bash
# Register existing warehouse
dwh warehouse register ~/Archive --name archive

# Set default
dwh warehouse default archive

# Show current
dwh warehouse current
# ~/Archive (archive)

# Unregister (doesn't delete warehouse)
dwh warehouse unregister old_warehouse
```

## Alternatives Considered

### Alternative A: No Registry (Path Only)

Only support `--warehouse <path>`:

```bash
dwh --warehouse ~/Documents drop import -m "Tax docs" *.pdf
```

**Rejected because:**
- Paths are verbose
- No default warehouse support
- Can't name warehouses for easier reference

### Alternative B: Single Global Warehouse

One warehouse per user at fixed location (e.g., `~/Documents/.dwh`).

**Rejected because:**
- Users want separate work/personal warehouses
- Users may have warehouses on different drives
- Too restrictive

### Alternative C: Database-Based Registry

Store registry in SQLite instead of TOML.

**Rejected because:**
- TOML is simpler and human-editable
- Don't need query capabilities
- Avoid dependency on database for config

### Alternative D: Environment Variables Only

```bash
export DWH_PERSONAL=~/Documents
export DWH_WORK=~/Work/Archive
dwh --warehouse $DWH_PERSONAL drop import ...
```

**Rejected because:**
- Shell-specific
- No persistence across sessions
- No listing/management commands

## Consequences

### Positive

**1. Frictionless out-of-tree operations**

Import from anywhere:
```bash
cd ~/Downloads
dwh drop import -m "Tax docs" *.pdf
```

**2. Multiple warehouse support**

Maintain separate warehouses easily:
```bash
dwh --warehouse personal drop import ...
dwh --warehouse work drop import ...
```

**3. Named warehouse references**

`dwh --warehouse work` instead of `dwh --warehouse ~/Work/Archive`

**4. Discovery**

`dwh warehouse list` shows all available warehouses

**5. Default warehouse convenience**

Set once, use everywhere without flags

**6. Backward compatible**

Inside warehouse directory still works (priority #1)

### Negative

**1. State outside warehouse**

Global config at `~/.config/dwh/config.toml` adds external state

**Mitigation:** Config is rebuildable (can be regenerated from existing warehouses)

**2. Registry desync**

Registry might point to deleted or moved warehouses

**Mitigation:**
- Validate paths when resolving
- `dwh warehouse list` shows which are invalid
- Commands fail gracefully with helpful messages

**3. Complexity**

Adds global config management, resolution logic, new commands

**Mitigation:** Clear priority order and good error messages

**4. XDG compliance issues**

Not all systems follow XDG standard

**Mitigation:** Fallback to `~/.config` if `XDG_CONFIG_HOME` not set

### Migration Impact

**For existing users:**
- No migration needed (inside-warehouse operations still work)
- Can opt-in to registry by running `dwh warehouse register` or re-running `dwh init`

**For new users:**
- `dwh init` automatically registers warehouse
- Prompted to set default on first init

## Testing

```python
def test_warehouse_resolution_priority(tmp_path):
    """Warehouse resolution follows correct priority order."""
    # Setup: two warehouses
    wh1 = tmp_path / "wh1"
    wh2 = tmp_path / "wh2"

    run_cli(["init", str(wh1), "--register-as", "wh1"])
    run_cli(["init", str(wh2), "--register-as", "wh2"])
    run_cli(["warehouse", "default", "wh2"])

    # 1. Inside warehouse dir overrides default
    os.chdir(wh1)
    result = run_cli(["warehouse", "current"])
    assert str(wh1) in result.output

    # 2. --warehouse flag overrides current dir
    os.chdir(wh1)
    result = run_cli(["--warehouse", "wh2", "warehouse", "current"])
    assert str(wh2) in result.output

    # 3. Default used when outside warehouse
    os.chdir(tmp_path)
    result = run_cli(["warehouse", "current"])
    assert str(wh2) in result.output

def test_out_of_tree_import(tmp_path):
    """Can import from outside warehouse directory."""
    wh = tmp_path / "warehouse"
    source = tmp_path / "source"
    source.mkdir()

    (source / "file.txt").write_text("content")

    run_cli(["init", str(wh), "--register-as", "test"])
    run_cli(["warehouse", "default", "test"])

    # Import from outside warehouse
    os.chdir(source)
    result = run_cli(["drop", "import", "-m", "Test", "file.txt"])

    assert result.exit_code == 0
    assert "Imported 1 file" in result.output

def test_warehouse_list(tmp_path):
    """List shows all registered warehouses."""
    wh1 = tmp_path / "wh1"
    wh2 = tmp_path / "wh2"

    run_cli(["init", str(wh1), "--register-as", "personal"])
    run_cli(["init", str(wh2), "--register-as", "work"])
    run_cli(["warehouse", "default", "personal"])

    result = run_cli(["warehouse", "list"])

    assert "personal" in result.output
    assert "work" in result.output
    assert "*" in result.output  # Default marker
```

## References

- XDG Base Directory Specification: https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
- Similar tools:
  - Docker contexts (`docker context ls`)
  - Kubernetes contexts (`kubectl config use-context`)
  - Git remotes (implicit registry in `.git/config`)

## Decision Drivers

1. **Reduce friction** - Import from anywhere, not just inside warehouse
2. **Multiple warehouses** - Support work/personal separation
3. **Discoverability** - List all warehouses
4. **Convenience** - Default warehouse for common operations
5. **Backward compatibility** - Keep existing behavior working
