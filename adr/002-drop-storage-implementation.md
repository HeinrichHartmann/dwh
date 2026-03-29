# ADR-002: Drop Storage Implementation

**Status:** Proposed
**Date:** 2026-03-29

## Context

Drop storage is the foundation of DWH. Before filing, querying, or any advanced features, we need reliable import/export with provenance.

This ADR covers the first implementation milestone: basic drop operations.

## Scope

### Commands

| Command | Purpose |
|---------|---------|
| `dwh init` | Initialize warehouse |
| `dwh drop import -m "msg" <paths>` | Import files, create drop |
| `dwh drop list` | List all drops |
| `dwh drop inspect <drop_id>` | Show drop details |
| `dwh drop export <drop_id> <dest>` | Reconstruct drop to destination |

### Out of Scope (for this ADR)

- Filing (`dwh file`, `dwh capture`)
- Document projection (`documents/`)
- Verification (`dwh verify`)
- Restore (`dwh restore`)

## Implementation

### Module Structure

```
src/dwh/
├── __init__.py
├── cli.py              # Click CLI entry point
├── warehouse.py        # Warehouse class (paths, config)
├── db.py               # Schema, migrations, queries
├── history.py          # History operations (append, replay)
├── drop.py             # Drop/Entry operations
└── triage.py           # Triage workflow
```

### Database Schema (v1)

```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE blobs (
    hash TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mime_type TEXT,
    stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE drops (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE entries (
    id TEXT PRIMARY KEY,
    drop_id TEXT NOT NULL REFERENCES drops(id),
    blob_hash TEXT NOT NULL REFERENCES blobs(hash),
    filename TEXT NOT NULL,
    relative_path TEXT,
    source_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_entries_drop_id ON entries(drop_id);
CREATE INDEX idx_entries_blob_hash ON entries(blob_hash);
```

### Command Implementations

#### `dwh init [path]`

```python
def init(path: Path, name: str | None = None):
    """Initialize warehouse at path."""
    warehouse = Warehouse(path)

    if warehouse.exists():
        raise WarehouseExistsError(path)

    # Create structure
    warehouse.dwh_dir.mkdir(parents=True)
    warehouse.blobs_dir.mkdir(parents=True)
    warehouse.drops_dir.mkdir(parents=True)
    warehouse.documents_dir.mkdir(parents=True)

    # Initialize database
    init_db(warehouse.db_path)

    # Write config
    write_config(warehouse.config_path, {
        "name": name or path.name,
        "version": "1",
    })

    return warehouse
```

#### `dwh drop import -m "msg" <paths>`

```python
def drop_import(paths: list[Path], message: str, warehouse: Warehouse) -> Drop:
    """Import files and create a drop in history."""
    # Generate drop ID and get next history number
    drop_id = generate_drop_id()  # d_YYYYMMDD_HHMMSS_hash8
    actor = getpass.getuser()
    seq_num = get_next_history_number(warehouse.history_dir)

    # Create history folder
    drop_dir = warehouse.history_dir / f"{seq_num:03d}_drop_{drop_id}"
    tree_dir = drop_dir / "tree"
    tree_dir.mkdir(parents=True)

    # Copy files to tree/, preserving structure
    for path in expand_paths(paths):
        relative_path = compute_relative_path(path, paths)
        dest = tree_dir / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    # Write receipt (metadata only)
    receipt = generate_receipt(drop_id, message, actor)
    write_receipt(drop_dir, receipt)

    # Derive entries from tree/ and update database
    entries = derive_entries(tree_dir, drop_id)
    apply_drop_to_db(warehouse.connect(), receipt, entries)

    return Drop(drop_id, message, actor, entries)

def apply_drop_to_db(conn, receipt: dict, entries: list[Entry]):
    """Apply drop to database."""
    conn.execute(
        "INSERT INTO drops (id, message, actor) VALUES (?, ?, ?)",
        (receipt["drop_id"], receipt["message"], receipt["actor"])
    )

    for entry in entries:
        conn.execute(
            """INSERT INTO entries (id, drop_id, blob_hash, filename, relative_path, source_path)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry.id, receipt["drop_id"], entry.blob_hash, entry.filename,
             entry.relative_path, "")  # source_path not stored in history
        )

    conn.commit()
```

#### `dwh drop list`

```python
def drop_list(warehouse: Warehouse) -> list[DropSummary]:
    """List all drops."""
    conn = warehouse.connect()

    rows = conn.execute("""
        SELECT d.id, d.message, d.actor, d.created_at, COUNT(e.id) as entry_count
        FROM drops d
        LEFT JOIN entries e ON e.drop_id = d.id
        GROUP BY d.id
        ORDER BY d.created_at DESC
    """).fetchall()

    return [DropSummary(**row) for row in rows]
```

Output format:
```
DROP_ID                          DATE        FILES  MESSAGE
d_20260329_143211_a1b2c3d4       2026-03-29  12     Tax documents 2024
d_20260328_091500_deadbeef       2026-03-28  3      Bank statements Q1
```

#### `dwh drop inspect <drop_id>`

```python
def drop_inspect(drop_id: str, warehouse: Warehouse) -> Drop:
    """Get full drop details."""
    conn = warehouse.connect()

    drop_row = conn.execute(
        "SELECT * FROM drops WHERE id = ?", (drop_id,)
    ).fetchone()

    if not drop_row:
        raise DropNotFoundError(drop_id)

    entries = conn.execute(
        "SELECT * FROM entries WHERE drop_id = ?", (drop_id,)
    ).fetchall()

    return Drop(
        id=drop_row["id"],
        message=drop_row["message"],
        actor=drop_row["actor"],
        created_at=drop_row["created_at"],
        entries=[Entry(**e) for e in entries]
    )
```

Output format:
```
Drop: d_20260329_143211_a1b2c3d4
Date: 2026-03-29T14:32:11
Actor: hhartmann
Message: Tax documents 2024

Entries (12 files, 4.2 MB):
  e_8f3c2a1b9d4e  invoice.pdf           march/invoice.pdf      123.4 KB
  e_7a2b3c4d5e6f  receipt.pdf           march/receipt.pdf       45.2 KB
  ...
```

#### `dwh drop export <drop_id> <dest>`

```python
def drop_export(drop_id: str, dest: Path, warehouse: Warehouse):
    """Export drop to destination directory."""
    # Find drop folder in history
    drop_dir = find_drop_in_history(warehouse.history_dir, drop_id)
    if not drop_dir:
        raise DropNotFoundError(drop_id)

    tree_dir = drop_dir / "tree"
    dest.mkdir(parents=True, exist_ok=True)

    # Copy tree/ contents to destination
    count = 0
    for file in tree_dir.rglob("*"):
        if file.is_file():
            relative_path = file.relative_to(tree_dir)
            entry_dest = dest / relative_path
            entry_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, entry_dest)
            count += 1

    return count

def find_drop_in_history(history_dir: Path, drop_id: str) -> Path | None:
    """Find drop folder in history by drop_id."""
    for item in history_dir.iterdir():
        if item.is_dir() and drop_id in item.name:
            return item
    return None
```

### File Storage

Files are stored directly in `history/NNN_drop_.../tree/`. This is the canonical location.

```python
def compute_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()
```

**Note:** Content-addressed blob storage (`.dwh/blobs/`) is optional deduplication for v2. For v1, files live directly in history tree folders.

### Receipt Format

Like a git commit: metadata only. Entries are derived from `tree/` contents.

```python
def generate_receipt(drop_id: str, message: str, actor: str) -> dict:
    """Generate receipt JSON (metadata only)."""
    return {
        "type": "drop",
        "drop_id": drop_id,
        "message": message,
        "actor": actor,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }

def write_receipt(drop_dir: Path, receipt: dict):
    """Write receipt.json to drop directory."""
    receipt_path = drop_dir / "receipt.json"
    with open(receipt_path, "w") as f:
        json.dump(receipt, f, indent=2)

def derive_entries(tree_dir: Path, drop_id: str) -> list[Entry]:
    """Derive entries by scanning tree/ contents."""
    entries = []
    for file in tree_dir.rglob("*"):
        if file.is_file():
            relative_path = file.relative_to(tree_dir)
            entries.append(Entry(
                id=generate_entry_id(drop_id, relative_path),
                filename=file.name,
                relative_path=str(relative_path),
                blob_hash=compute_hash(file),
                size=file.stat().st_size,
            ))
    return entries
```

## Testing Approach

### E2E Tests (Primary)

```python
# tests/e2e/test_drop_storage.py

class TestDropImportExport:
    """Test import/export roundtrip."""

    def test_import_single_file(self, tmp_warehouse, sample_pdf):
        result = run_cli(["drop", "import", "-m", "test", str(sample_pdf)])
        assert result.exit_code == 0
        assert "d_" in result.output  # Drop ID printed

        # Verify drop exists in list
        result = run_cli(["drop", "list"])
        assert "test" in result.output

    def test_import_directory(self, tmp_warehouse, sample_dir):
        result = run_cli(["drop", "import", "-m", "batch", str(sample_dir)])
        assert result.exit_code == 0

        # Verify all files imported
        drop_id = extract_drop_id(result.output)
        result = run_cli(["drop", "inspect", drop_id])
        assert "file1.pdf" in result.output
        assert "subdir/file2.txt" in result.output

    def test_import_export_roundtrip(self, tmp_warehouse, sample_dir):
        # Import
        result = run_cli(["drop", "import", "-m", "roundtrip", str(sample_dir)])
        drop_id = extract_drop_id(result.output)

        # Export
        export_dir = tmp_warehouse / "exported"
        run_cli(["drop", "export", drop_id, str(export_dir)])

        # Verify identical
        assert dirs_identical(sample_dir, export_dir)

    def test_receipt_written(self, tmp_warehouse, sample_pdf):
        result = run_cli(["drop", "import", "-m", "receipt test", str(sample_pdf)])
        drop_id = extract_drop_id(result.output)

        # Find receipt
        receipt_path = find_receipt(tmp_warehouse, drop_id)
        assert receipt_path.exists()

        receipt = json.loads(receipt_path.read_text())
        assert receipt["drop_id"] == drop_id
        assert receipt["message"] == "receipt test"
        assert len(receipt["entries"]) == 1

    def test_deduplication(self, tmp_warehouse, sample_pdf):
        # Import same file twice
        run_cli(["drop", "import", "-m", "first", str(sample_pdf)])
        run_cli(["drop", "import", "-m", "second", str(sample_pdf)])

        # Should have 2 drops, 2 entries, but 1 blob
        result = run_cli(["drop", "list"])
        assert result.output.count("d_") == 2

        blob_count = count_blobs(tmp_warehouse)
        assert blob_count == 1


class TestDropList:
    """Test drop listing."""

    def test_list_empty(self, tmp_warehouse):
        result = run_cli(["drop", "list"])
        assert result.exit_code == 0
        # Empty or header only

    def test_list_multiple(self, tmp_warehouse, sample_pdf):
        run_cli(["drop", "import", "-m", "first", str(sample_pdf)])
        run_cli(["drop", "import", "-m", "second", str(sample_pdf)])

        result = run_cli(["drop", "list"])
        assert "first" in result.output
        assert "second" in result.output


class TestDropInspect:
    """Test drop inspection."""

    def test_inspect_exists(self, tmp_warehouse, sample_pdf):
        result = run_cli(["drop", "import", "-m", "inspect test", str(sample_pdf)])
        drop_id = extract_drop_id(result.output)

        result = run_cli(["drop", "inspect", drop_id])
        assert result.exit_code == 0
        assert drop_id in result.output
        assert "inspect test" in result.output
        assert sample_pdf.name in result.output

    def test_inspect_not_found(self, tmp_warehouse):
        result = run_cli(["drop", "inspect", "d_nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestDropExport:
    """Test drop export."""

    def test_export_preserves_structure(self, tmp_warehouse, nested_sample_dir):
        result = run_cli(["drop", "import", "-m", "nested", str(nested_sample_dir)])
        drop_id = extract_drop_id(result.output)

        export_dir = tmp_warehouse / "exported"
        run_cli(["drop", "export", drop_id, str(export_dir)])

        # Verify nested structure preserved
        assert (export_dir / "subdir" / "nested.txt").exists()

    def test_export_not_found(self, tmp_warehouse):
        result = run_cli(["drop", "export", "d_nonexistent", "out/"])
        assert result.exit_code != 0
```

### Unit Tests

```python
# tests/unit/test_blob.py

def test_compute_hash():
    # Known hash for "hello"
    assert compute_hash_bytes(b"hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

def test_store_blob_creates_correct_path(tmp_path):
    file = tmp_path / "test.txt"
    file.write_text("content")

    blobs_dir = tmp_path / "blobs"
    blobs_dir.mkdir()

    blob_hash = store_blob(file, blobs_dir)

    # Verify path structure
    expected = blobs_dir / blob_hash[:2] / blob_hash[2:4] / blob_hash
    assert expected.exists()

def test_store_blob_deduplicates(tmp_path):
    file = tmp_path / "test.txt"
    file.write_text("content")

    blobs_dir = tmp_path / "blobs"
    blobs_dir.mkdir()

    hash1 = store_blob(file, blobs_dir)
    hash2 = store_blob(file, blobs_dir)

    assert hash1 == hash2
    assert count_files(blobs_dir) == 1  # Only one blob stored
```

```python
# tests/unit/test_drop.py

def test_generate_drop_id():
    drop_id = generate_drop_id()

    assert drop_id.startswith("d_")
    assert len(drop_id) == 26  # d_YYYYMMDD_HHMMSS_hash8

    # Parseable
    parts = drop_id.split("_")
    assert len(parts) == 4

def test_expand_paths_single_file(tmp_path):
    file = tmp_path / "test.txt"
    file.write_text("content")

    result = list(expand_paths([file]))
    assert result == [file]

def test_expand_paths_directory(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("b")

    result = list(expand_paths([tmp_path]))
    assert len(result) == 2
    assert any(p.name == "a.txt" for p in result)
    assert any(p.name == "b.txt" for p in result)
```

### CLI Structure Tests

```python
# tests/cli/test_drop_commands.py

def test_drop_import_requires_message(tmp_warehouse):
    result = run_cli(["drop", "import", "somefile.pdf"])
    assert result.exit_code != 0
    assert "message" in result.output.lower() or "-m" in result.output

def test_drop_import_requires_paths(tmp_warehouse):
    result = run_cli(["drop", "import", "-m", "test"])
    assert result.exit_code != 0

def test_drop_export_requires_drop_id(tmp_warehouse):
    result = run_cli(["drop", "export"])
    assert result.exit_code != 0

def test_drop_export_requires_destination(tmp_warehouse, sample_pdf):
    run_cli(["drop", "import", "-m", "t", str(sample_pdf)])
    result = run_cli(["drop", "export", "d_something"])
    assert result.exit_code != 0
```

## Implementation Subtasks

### Phase 1: Foundation

| Task | Description | Tests |
|------|-------------|-------|
| **1.1** | Create new `db.py` with v1 schema | Unit: schema creates tables |
| **1.2** | Create `blob.py` with `compute_hash`, `store_blob`, `get_blob_path` | Unit: hash correctness, dedup |
| **1.3** | Create `warehouse.py` with path helpers | Unit: path construction |
| **1.4** | Implement `dwh init` | E2E: creates structure |

### Phase 2: Import

| Task | Description | Tests |
|------|-------------|-------|
| **2.1** | Implement `expand_paths` (file/dir handling) | Unit: expansion logic |
| **2.2** | Implement `generate_drop_id`, `generate_entry_id` | Unit: format correctness |
| **2.3** | Implement `drop_import` core logic | E2E: import creates drop |
| **2.4** | Implement `project_drop` (filesystem projection) | E2E: files appear in drops/ |
| **2.5** | Implement `generate_receipt`, `write_receipt` | E2E: receipt.json written |
| **2.6** | Wire up `dwh drop import` CLI | E2E: full import flow |

### Phase 3: Query

| Task | Description | Tests |
|------|-------------|-------|
| **3.1** | Implement `drop_list` | E2E: lists drops |
| **3.2** | Implement `drop_inspect` | E2E: shows details |
| **3.3** | Wire up CLI commands | CLI: structure tests |

### Phase 4: Export

| Task | Description | Tests |
|------|-------------|-------|
| **4.1** | Implement `drop_export` | E2E: roundtrip test |
| **4.2** | Verify structure preservation | E2E: nested dirs work |
| **4.3** | Wire up CLI | CLI: structure tests |

### Phase 5: Polish

| Task | Description | Tests |
|------|-------------|-------|
| **5.1** | Error handling (missing files, bad drop_id) | E2E: error cases |
| **5.2** | Output formatting (pretty tables, JSON option) | Manual review |
| **5.3** | Edge cases (empty dirs, symlinks, permissions) | E2E: edge cases |

## Dependencies

```
dwh init
    └── dwh drop import
            └── dwh drop list
            └── dwh drop inspect
            └── dwh drop export
```

Start with init → import → list/inspect → export.

## Definition of Done

- [ ] All E2E tests pass
- [ ] CLI structure tests pass
- [ ] Can import 431 files from `import/` directory
- [ ] Can export any drop and get identical content
- [ ] Receipt contains all required fields
- [ ] Code reviewed

## References

- [DESIGN.md](../DESIGN.md) - Data model, schema
- [ADR-001](001-testing-strategy.md) - Testing approach
- [README.md](../README.md) - CLI interface specification
