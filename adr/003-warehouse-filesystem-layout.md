# ADR-003: Warehouse Filesystem Layout

**Status:** Accepted
**Date:** 2026-03-29

## Context

DWH needs to coexist with user files in a directory. The layout must balance several concerns:

1. **Adoption friction** - Users should be able to run `dwh init ~/Documents` without restructuring their existing files
2. **Backup clarity** - Critical data (history) must be obviously backed up, cache can be skipped
3. **Namespace management** - System directories should not collide with user categories
4. **Visual signaling** - Users should understand which directories are "theirs" vs "system-managed"
5. **Path ergonomics** - Common operations should use short, natural paths

The question: How should we organize metadata, history, working directories, and user content within the warehouse root?

## Decision

**The warehouse root IS the archive.** User categories live at the root alongside system directories.

### Filesystem Layout

```
~/Documents/                    # Warehouse root (user chooses location)
├── .dwh/                       # Hidden metadata (cache, rebuildable)
│   ├── config.toml
│   └── dwh.db
├── _history/                   # Semi-visible canonical data (must backup)
│   ├── 001_drop_d_.../
│   │   ├── receipt.json
│   │   └── tree/
│   │       └── invoice.pdf
│   └── 002_classify.json
├── _triage/                    # Semi-visible working directory (ephemeral)
│   └── ... (files being classified)
├── finance/                    # User categories (at root level)
│   ├── taxes/
│   │   └── 2024/
│   │       └── invoice.pdf
│   └── invoices/
├── medical/
└── personal/
```

### Directory Semantics

| Directory | Prefix | Visibility | Purpose | Backup? |
|-----------|--------|------------|---------|---------|
| `.dwh/` | Dot | Hidden | Metadata cache (config, SQLite) | Optional (rebuildable) |
| `_history/` | Underscore | Semi-visible | Canonical event log | **Required** |
| `_triage/` | Underscore | Semi-visible | Working directory | No (ephemeral) |
| `finance/`, etc. | None | Visible | User categories | Required (but rebuildable) |

### Naming Convention

**Dot prefix (`.dwh/`):**
- True metadata and cache
- Can be safely regenerated from history
- Hidden from casual browsing
- Standard Unix/POSIX convention

**Underscore prefix (`_history/`, `_triage/`):**
- System-managed, not user content
- Semi-visible (shown in `ls -a`, not `ls`)
- Signals "don't manually edit"
- Included in backups by default

**No prefix (user categories):**
- User-owned namespace
- Fully visible
- Natural filesystem organization

### Configuration

System directory names are configurable via `.dwh/config.toml`:

```toml
name = "My Documents"
version = 1

# Directory locations (relative to warehouse root)
history_dir = "_history"  # Default
triage_dir = "_triage"    # Default
```

**Rationale for configurability:**

1. **Name collision resolution** - If user has existing `_history/` directory
2. **Platform constraints** - Some cloud storage systems mangle underscore-prefixed directories
3. **Personal preference** - Some users prefer dotfiles (`.history`) over underscore prefix
4. **Migration scenarios** - Import from other systems with different naming

**Configuration constraints:**

- Directory names must be relative (not absolute paths)
- Must be single-component paths (no `/` or nested directories)
- Cannot be reserved names (`.dwh`, `.`, `..`)
- `.dwh/` itself is **not configurable** (serves as warehouse anchor)

**CLI initialization:**

```bash
# Default (recommended)
dwh init ~/Documents

# Custom directory names
dwh init ~/Documents --history-dir .history --triage-dir .triage

# Explicit prefix (verbose but clear)
dwh init ~/Documents --history-dir dwh_history --triage-dir dwh_triage
```

**Validation:**

The `dwh init` command warns if directory names don't start with `_` or `.`:

```bash
dwh init . --history-dir history
# ⚠ Warning: history/ doesn't start with _ or .
#   It won't be visually distinct from user categories.
```

**Recommendation:** Use defaults (`_history`, `_triage`) unless you have specific constraints.

## Alternatives Considered

### Alternative A: Nested Under `documents/`

```
warehouse/
├── .dwh/
│   └── history/
├── triage/
└── documents/           # User content nested here
    └── finance/
```

**Rejected because:**
- Forces dedicated warehouse directory (can't use existing ~/Documents)
- Extra nesting: `documents/finance/taxes/` vs `finance/taxes/`
- Separates DWH from user's existing structure
- "Warehouse contains archive" instead of "warehouse IS archive"
- Triage workflow: `mv triage/file.pdf documents/finance/` is awkward

### Alternative B: All Hidden Dotfiles

```
warehouse/
├── .dwh/
├── .history/
├── .triage/
└── finance/
```

**Rejected because:**
- Backup tools often skip dotfiles by default
- Critical history data would be hidden
- Name collision risk (`.history` used by bash, `.triage` might be used elsewhere)
- Multiple dotfiles at root clutters `ls -a`

### Alternative C: Explicit `dwh_` Prefix

```
warehouse/
├── .dwh/
├── dwh_history/
├── dwh_triage/
└── finance/
```

**Rejected because:**
- Too visible (clutters `ls` output)
- Sorts alphabetically with user categories
- Verbose prefix doesn't add clarity
- Looks like user namespace, not system

### Alternative D: Single `.dwh/` Container

```
warehouse/
├── .dwh/
│   ├── config.toml
│   ├── dwh.db
│   ├── history/
│   └── triage/
└── finance/
```

**Rejected because:**
- Conflates cache (.dwh/dwh.db) with canonical data (.dwh/history/)
- History is less visible (users may not realize it needs backup)
- Longer paths: `.dwh/history/001_drop_.../tree/file.pdf`
- Inconsistent: Why is triage/ hidden but finance/ visible?

## Consequences

### Positive

**1. Zero-friction adoption**

Users can initialize DWH in existing directories:
```bash
cd ~/Documents
dwh init .
# Done. Existing files stay in place.
# DWH adds: .dwh/, _history/, _triage/
```

**2. Coexistence with unmanaged files**

DWH becomes a metadata layer. Unmanaged files can coexist:
```
~/Documents/
├── _history/         # DWH-managed
├── finance/          # DWH-managed (tracked)
│   └── invoice.pdf   # Tracked (imported via dwh drop import)
├── draft.docx        # Untracked (ignored by DWH)
└── temp/             # Untracked (ignored by DWH)
```

**Tracked vs Untracked:**

DWH only manages files explicitly imported via `dwh drop import`:
- **Tracked:** Files with provenance records in history
- **Untracked:** Other files in the warehouse (no DWH metadata)

This is intentional and analogous to Git:
- Git repos can have untracked files
- Only `git add`-ed files are versioned
- Untracked files coexist peacefully

**Invariant update:**
- ~~Old: "The archive is rebuildable from database"~~
- **New: "Tracked portions of the archive are rebuildable from database"**

Untracked files have no provenance and won't be restored by `dwh rebuild` or `dwh restore`.

**Optional status command:**
```bash
dwh status
# Tracked: 45 documents across 12 categories
# Untracked: 8 files (run dwh status --list-untracked to see them)
```

**3. Clear backup semantics**

Visual inspection shows what matters:
- `.dwh/` - Cache, can skip
- `_history/` - **Must backup** (source of truth)
- `_triage/` - Ephemeral, can skip
- Categories - Must backup (but rebuildable from _history/)

Standard backup:
```bash
restic backup ~/Documents/ --exclude .dwh --exclude _triage
```

**4. Short, natural paths**

```bash
# Triage workflow
dwh triage checkout
mv _triage/invoice.pdf finance/taxes/2024/
dwh triage sync

# Direct classification (no "documents/" prefix)
ls finance/taxes/
```

**5. Clear namespace separation**

- `.dwh`, `_history`, `_triage` - Reserved system names
- Everything else - User namespace
- Collision unlikely (users won't create `_history/` category)

**6. Shell experience**

```bash
# Clean output by default
$ ls
finance/  medical/  personal/

# System dirs visible when needed
$ ls -a
.dwh/  _history/  _triage/  finance/  medical/  personal/

# Tab completion groups system dirs
$ ls _<tab>
_history/  _triage/
```

**7. Auto-classify on import from within warehouse**

When importing files that are already organized within the warehouse, DWH auto-classifies them based on their current location:

```bash
# User has already organized files
~/Documents/
└── finance/
    └── taxes/
        └── 2024/
            └── invoice.pdf

# Import from within warehouse
cd ~/Documents
dwh drop import -m "Tax documents" finance/taxes/2024/invoice.pdf

# Output:
Imported 1 file
Drop ID: d_20260329_143211_abc123
Auto-classified 1 file (already organized):
  finance/taxes/2024/invoice.pdf → finance/taxes/2024
```

**Behavior:**

1. **Detection:** Check if import path is within warehouse root (excluding system dirs)
2. **Storage:** Copy to history at `_history/NNN_drop_.../tree/finance/taxes/2024/invoice.pdf`
3. **Auto-classify:** Create document with:
   - category: `"finance/taxes/2024"`
   - name: `"invoice.pdf"`
4. **Skip triage:** File already in final location, no triage needed
5. **History records:** Both drop + classification records created

**Mixed imports:**

If import includes both internal and external paths:

```bash
dwh drop import -m "Mixed" finance/invoice.pdf ~/Downloads/receipt.pdf
# Auto-classifies: finance/invoice.pdf → finance
# Requires triage: ~/Downloads/receipt.pdf
```

**Safety:**

System directories are rejected:

```bash
dwh drop import -m "Bad" _history/001_drop_.../tree/file.pdf
# Error: Cannot import from system directory: _history
```

**Use cases:**

1. **Track existing organized files** - User has already organized documents, wants to add provenance
2. **Bulk import** - Import entire category tree: `dwh drop import -m "Archive" finance/`
3. **Retroactive tracking** - Add DWH to existing document collection

**Implementation note:**

Files are copied (not moved) to history, resulting in duplication. This is acceptable because:
- User explicitly organized the file at that location
- History needs canonical copy for durability
- v1 doesn't have content-addressed blob dedup anyway
- Future v2 can deduplicate via blob storage

### Negative

**1. Reserved namespace**

Users cannot create categories named:
- `.dwh`
- `_history`
- `_triage`

**Mitigation:** These names are unlikely to collide with natural categories. If collision occurs, user can rename their category (e.g., `_history_archive/`).

**2. Unconventional underscore prefix**

Underscore prefix is not a standard Unix convention (dot prefix is).

**Mitigation:**
- Python uses `__pycache__/` (double underscore)
- Underscore is widely understood as "system/temporary"
- Benefit (backup visibility) outweighs convention cost

**3. Multiple system directories at root**

Three system entries (`.dwh/`, `_history/`, `_triage/`) at root level.

**Mitigation:**
- Dot and underscore prefixes sort first
- `ls` without `-a` shows clean output
- Clear semantic separation justifies multiple entries

**4. History visibility trade-off**

`_history/` is semi-visible, users might wonder what it is.

**Mitigation:**
- Good thing - users should know history exists
- README.md explains directory structure
- `dwh init` can print explanation

**5. Untracked files are not rebuildable**

Files that exist in the warehouse but were never imported have no provenance:

```
~/Documents/
├── finance/
│   ├── invoice.pdf    # Tracked (imported)
│   └── draft.pdf      # Untracked (just copied there)
```

If the database is corrupted and rebuilt:
- `invoice.pdf` restored from history ✅
- `draft.pdf` not restored (no history record) ❌

**Mitigation:**
- This is explicit design choice (like Git untracked files)
- Users control what gets tracked via `dwh drop import`
- Optional `dwh status` command can list untracked files
- User responsibility to backup the entire warehouse directory

**Recommendation:**
- Backup entire warehouse (not just history)
- Or explicitly import all files you want tracked

### Migration Impact

This layout is incompatible with any previous design that nested under `documents/`.

**For pre-v1:** This is the right time to make this breaking change.

**For post-v1:** Would need migration tool to:
1. Move `documents/*` to root
2. Move `.dwh/history/` to `_history/`
3. Rename `triage/` to `_triage/`

## Implementation Notes

### Code Changes

**warehouse.py:**
```python
@dataclass
class Warehouse:
    root: Path
    _config: Config | None = None

    @property
    def config(self) -> Config:
        """Lazy-load configuration."""
        if self._config is None:
            self._config = Config.load(self.config_path)
        return self._config

    @property
    def dwh_dir(self) -> Path:
        return self.root / ".dwh"

    @property
    def history_dir(self) -> Path:
        return self.root / self.config.history_dir

    @property
    def triage_dir(self) -> Path:
        return self.root / self.config.triage_dir

    @property
    def config_path(self) -> Path:
        return self.dwh_dir / "config.toml"

    @property
    def db_path(self) -> Path:
        return self.dwh_dir / "dwh.db"
```

### Triage Workflow

**Simplified paths:**
```python
# Old
documents_dir = warehouse.root / "documents"
category_path = documents_dir / category  # documents/finance

# New
category_path = warehouse.root / category  # finance
```

**Category extraction:**
```python
# From: finance/taxes/2024/invoice.pdf
relative_to_root = doc_path.relative_to(warehouse.root)
category = str(relative_to_root.parent)  # "finance/taxes/2024"
name = relative_to_root.name              # "invoice.pdf"
```

### Auto-Classify on Import

**Detection logic:**
```python
def drop_import(paths: list[Path], message: str, warehouse_root: Path, ...):
    resolved_paths = [p.resolve() for p in paths]

    within_warehouse = []
    external = []

    for p in resolved_paths:
        try:
            rel = p.relative_to(warehouse_root)

            # Reject system directories
            if str(rel).startswith(('.dwh', '_history', '_triage')):
                raise ImportError(f"Cannot import from system directory: {p}")

            within_warehouse.append((p, rel))
        except ValueError:
            external.append(p)

    # Phase 1: Create drop in history (all files)
    drop = create_drop_in_history(...)

    # Phase 2: Auto-classify within-warehouse files
    if within_warehouse:
        auto_classify_entries(drop, within_warehouse, ...)

    return drop
```

**Auto-classification:**
```python
def auto_classify_entries(drop: Drop, within_warehouse: list, history_dir: Path, conn):
    """Auto-classify files imported from within warehouse."""
    classifications = []

    for file_path, relative_path in within_warehouse:
        # Extract category from current location
        category = str(relative_path.parent) if relative_path.parent != Path('.') else ''
        name = relative_path.name

        # Find corresponding entry
        entry = next(e for e in drop.entries if e.relative_path == str(relative_path))

        # Insert document
        cursor = conn.execute(
            "INSERT INTO documents (entry_id, name, category) VALUES (?, ?, ?)",
            (entry.id, name, category)
        )

        classifications.append({
            'entry_id': entry.id,
            'document_id': cursor.lastrowid,
            'category': category,
            'name': name
        })

    # Write classification record to history
    if classifications:
        seq_num = get_next_history_number(history_dir)
        classify_file = history_dir / f"{seq_num:03d}_classify.json"

        record = {
            'type': 'classify',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'actor': getpass.getuser(),
            'message': f'Auto-classify from import: {drop.message}',
            'classifications': classifications
        }

        with open(classify_file, 'w') as f:
            json.dump(record, f, indent=2)

        conn.commit()
```

### Init Command

**CLI signature:**
```python
@main.command()
@click.argument("path", type=click.Path(path_type=Path), default=".")
@click.option("--name", help="Warehouse name")
@click.option("--history-dir", default="_history", help="History directory name")
@click.option("--triage-dir", default="_triage", help="Triage directory name")
def init(path: Path, name: str | None, history_dir: str, triage_dir: str):
    # Validate directory names
    config = Config(name=name or path.name, version=1,
                    history_dir=history_dir, triage_dir=triage_dir)
    config.validate(path)

    # Create directories and write config
    ...
```

**Output message:**
```
Initialized warehouse at /Users/name/Documents

Created:
  .dwh/       - Metadata (config, database cache)
  _history/   - Event log (source of truth, backup required)
  _triage/    - Working directory (ephemeral)

You can now import files:
  dwh drop import -m "Description" path/to/files/
```

**With custom directories:**
```bash
dwh init . --history-dir .archive --triage-dir .staging

# Output:
Initialized warehouse at /Users/name/Documents

Created:
  .dwh/       - Metadata (config, database cache)
  .archive/   - Event log (source of truth, backup required)
  .staging/   - Working directory (ephemeral)
```

### Documentation Updates

**README.md:**
- Update Quick Start examples (no `documents/` prefix)
- Update filesystem layout diagram
- Explain directory purposes

**DESIGN.md:**
- Update filesystem layout section
- Update category extraction logic
- Update backup strategy section

**CLI help text:**
- `dwh init`: Explain created directories
- `dwh triage`: Show paths without `documents/` prefix

## Examples

### Example 1: New Warehouse

```bash
cd ~/Documents
dwh init .

# Structure:
~/Documents/
├── .dwh/
├── _history/
└── _triage/
```

### Example 2: Import and Classify

```bash
dwh drop import -m "Tax documents 2024" ~/Downloads/taxes/

# Import creates:
_history/
└── 001_drop_d_20260329_143211_abc123/
    ├── receipt.json
    └── tree/
        ├── invoice.pdf
        └── receipt.pdf

# Triage:
dwh triage checkout
mv _triage/invoice.pdf finance/taxes/2024/
dwh triage sync

# Result:
finance/
└── taxes/
    └── 2024/
        └── invoice.pdf
```

### Example 3: Existing Directory

```bash
# User has:
~/Documents/
├── work/
├── personal/
└── old_taxes.pdf

# Initialize DWH:
cd ~/Documents
dwh init .

# Result:
~/Documents/
├── .dwh/              # Added by DWH
├── _history/          # Added by DWH
├── _triage/           # Added by DWH
├── work/              # Existing, unmanaged
├── personal/          # Existing, unmanaged
└── old_taxes.pdf      # Existing, unmanaged

# Import old file:
dwh drop import -m "Old taxes" old_taxes.pdf
dwh triage checkout
mv _triage/old_taxes.pdf finance/taxes/old/
dwh triage sync

# Now old_taxes.pdf is DWH-managed with provenance
```

### Example 4: Auto-Classify from Within Warehouse

```bash
# User has already organized files
~/Documents/
└── finance/
    └── taxes/
        └── 2024/
            ├── invoice.pdf
            └── receipt.pdf

# Initialize DWH
cd ~/Documents
dwh init .

# Import already-organized files
dwh drop import -m "2024 tax documents" finance/taxes/2024/

# Output:
Imported 2 files
Drop ID: d_20260329_143211_abc123
Auto-classified 2 files (already organized):
  finance/taxes/2024/invoice.pdf → finance/taxes/2024
  finance/taxes/2024/receipt.pdf → finance/taxes/2024

# Result:
# - Files stay at finance/taxes/2024/ (not moved)
# - History has copy at _history/001_drop_.../tree/finance/taxes/2024/
# - Both drop and classification records created
# - No triage needed (already in final location)

# Verify
dwh drop list
# d_20260329_143211_abc123  2026-03-29  2  2024 tax documents

# Files are now tracked with provenance
```

## Validation

**Success criteria:**

1. ✅ User can run `dwh init ~/Documents` without moving files
2. ✅ Backup tools include `_history/` by default (configurable to dotfile if needed)
3. ✅ `ls` output is clean (only user categories)
4. ✅ Triage workflow uses short paths (`finance/taxes/` not `documents/finance/taxes/`)
5. ✅ System directories sort before user categories
6. ✅ Clear separation: cache vs canonical vs ephemeral vs user
7. ✅ Directory names configurable via `--history-dir` and `--triage-dir` flags
8. ✅ Import from within warehouse auto-classifies based on current location
9. ✅ Tracked vs untracked files coexist (Git-like model)

**Validation tests:**

```python
def test_init_in_existing_directory(tmp_path):
    """DWH can initialize in directory with existing files."""
    # Create existing structure
    (tmp_path / "work").mkdir()
    (tmp_path / "personal").mkdir()
    (tmp_path / "file.txt").write_text("existing")

    # Initialize
    run_cli(["init", str(tmp_path)])

    # Verify DWH added its directories
    assert (tmp_path / ".dwh").exists()
    assert (tmp_path / "_history").exists()
    assert (tmp_path / "_triage").exists()

    # Verify existing files untouched
    assert (tmp_path / "work").exists()
    assert (tmp_path / "personal").exists()
    assert (tmp_path / "file.txt").read_text() == "existing"

def test_category_at_root(tmp_warehouse, single_file):
    """Categories created at warehouse root, not under documents/."""
    run_cli(["drop", "import", "-m", "Test", str(single_file)])
    run_cli(["triage", "checkout"])

    # User creates category at root
    finance_dir = tmp_warehouse / "finance"
    finance_dir.mkdir()

    triage_dir = tmp_warehouse / "_triage"
    (triage_dir / single_file.name).rename(finance_dir / single_file.name)

    run_cli(["triage", "sync"])

    # Verify category is at root
    assert (tmp_warehouse / "finance" / single_file.name).exists()
    assert not (tmp_warehouse / "documents").exists()

def test_custom_history_dir(tmp_path):
    """Init with custom history directory name."""
    run_cli(["init", str(tmp_path), "--history-dir", ".archive"])

    # Check config
    config_path = tmp_path / ".dwh" / "config.toml"
    config_text = config_path.read_text()
    assert 'history_dir = ".archive"' in config_text

    # Check directory created
    assert (tmp_path / ".archive").exists()
    assert not (tmp_path / "_history").exists()

    # Check warehouse uses it
    wh = Warehouse(tmp_path)
    assert wh.history_dir == tmp_path / ".archive"

def test_auto_classify_from_within_warehouse(tmp_warehouse):
    """Import from within warehouse auto-classifies to current location."""
    # User organizes file first
    finance_dir = tmp_warehouse / "finance" / "taxes"
    finance_dir.mkdir(parents=True)

    invoice = finance_dir / "invoice.pdf"
    invoice.write_bytes(b"content")

    # Import from within warehouse
    result = run_cli(["drop", "import", "-m", "Tax docs", str(invoice)])

    assert "Auto-classified 1 file" in result.output

    # Check classification record exists
    history_dir = tmp_warehouse / "_history"
    classify_files = list(history_dir.glob("*_classify.json"))
    assert len(classify_files) == 1

    record = json.loads(classify_files[0].read_text())
    assert record['classifications'][0]['category'] == 'finance/taxes'
    assert record['classifications'][0]['name'] == 'invoice.pdf'

    # Check document in database
    db_path = tmp_warehouse / ".dwh" / "dwh.db"
    conn = sqlite3.connect(db_path)
    docs = conn.execute("SELECT * FROM documents").fetchall()
    assert len(docs) == 1
    assert docs[0]['category'] == 'finance/taxes'

def test_import_rejects_system_directories(tmp_warehouse):
    """Import rejects files from system directories."""
    history_dir = tmp_warehouse / "_history"
    history_dir.mkdir()

    test_file = history_dir / "test.pdf"
    test_file.write_bytes(b"content")

    result = run_cli(["drop", "import", "-m", "Bad", str(test_file)])

    assert result.exit_code != 0
    assert "system directory" in result.output.lower()
```

## References

- Python: `__pycache__/` (double underscore for system)
- Git: `.git/` (dotfile for all metadata)
- Unix: `/var/`, `/tmp/` (visible system directories)
- Backup tools: restic, borgbackup (typically skip dotfiles, include underscore)

## Decision Drivers

1. **Enable `dwh init ~/Documents`** - Critical for adoption
2. **Backup-friendly** - History must be obviously backed up
3. **Short paths** - Reduce cognitive load
4. **Clear ownership** - User vs system directories
5. **Git-like simplicity** - One initialization command, coexists with existing structure
6. **Flexibility** - Configurability for different environments and preferences
