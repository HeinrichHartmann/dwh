# ADR-009: Content-Addressed Blob Storage

**Status:** Proposed
**Date:** 2026-03-29
**Context:** Current implementation lacks blob storage, files duplicated in _history

## Problem

**Current implementation:**
- Files copied to `_history/<seq>_drop_<drop_id>/tree/` during import
- Blob metadata tracked in `blobs` table (hash, size)
- **No actual content-addressed blob storage**
- Every drop stores full copy of imported files

**Issues:**

1. **No content deduplication**: Importing same file twice stores it twice
2. **Storage waste**: Identical files duplicated across drops
3. **No central content store**: Can't restore files from blobs
4. **Inefficient operations**: Export/triage must copy from `_history/tree/`
5. **Missing foundation**: ADR-007 (audit) and ADR-008 (trace) assume blob storage exists

**Example problem:**
```bash
# Import same file twice
$ dwh drop import ~/invoice.pdf  # Stored in _history/001_drop_.../tree/
$ dwh drop import ~/invoice.pdf  # Stored AGAIN in _history/002_drop_.../tree/

# Result: 2 copies on disk, same content
```

## Solution

Implement **content-addressed blob storage** as the foundation of DWH.

### Core Principle

**Single source of truth for content:**
```
Blob hash (SHA-256) → Physical file content
```

All file content stored once in `.dwh/blobs/`, referenced by hash. Multiple drops/entries/documents can reference the same blob.

### Storage Layout

```
.dwh/
  blobs/           ← Content-addressed storage
    ab/
      abc123def456789...  ← Actual file content
      abcdef123456789...
    cd/
      cde234567890abc...
    ...
  dwh.db           ← Metadata (blobs, drops, entries, documents)

_history/          ← Filesystem projection (optional, for human inspection)
  001_drop_d_20260329_120000_abc123/
    receipt.json   ← Drop metadata
    tree/          ← Symlinks or reconstructed view (optional)
      file1.txt
      file2.pdf
  002_classify.json
  003_drop_d_20260329_130000_def456/
    receipt.json
    tree/
```

### Blob Storage Structure

**Path computation:**
```python
def get_blob_path(blob_hash: str, blobs_dir: Path) -> Path:
    """Get physical path for a blob.

    Uses 2-char prefix for directory sharding (256 buckets).
    """
    return blobs_dir / blob_hash[:2] / blob_hash
```

**Example:**
```
Hash: abc123def456789abcdef012345678901234567890123456789012345678901
Path: .dwh/blobs/ab/abc123def456789abcdef012345678901234567890123456789012345678901
```

**Sharding rationale:**
- 256 subdirectories (00-ff)
- Prevents single directory with millions of files
- Standard approach (Git, Docker, etc.)

### Drop Import Workflow (Revised)

**New import flow:**

```python
def drop_import(paths, message, warehouse_root, history_dir, conn):
    """Import files with content-addressed blob storage."""
    blobs_dir = warehouse_root / '.dwh' / 'blobs'
    drop_id = generate_drop_id()

    # 1. Store blobs (deduplicated)
    entries = []
    for file_path in expand_paths(paths):
        # Compute hash
        blob_hash = compute_hash(file_path)

        # Store blob if not exists
        blob_path = get_blob_path(blob_hash, blobs_dir)
        if not blob_path.exists():
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, blob_path)

        # Track entry metadata
        entries.append(Entry(
            id=generate_entry_id(drop_id, ...),
            drop_id=drop_id,
            blob_hash=blob_hash,
            filename=file_path.name,
            relative_path=compute_relative_path(file_path, paths),
            source_path=str(file_path.resolve()),
            size=file_path.stat().st_size
        ))

    # 2. Write drop metadata to database
    apply_drop_to_db(conn, receipt, entries)

    # 3. Write history record (filesystem projection)
    write_history_record(history_dir, drop_id, receipt, entries)

    return Drop(...)
```

**Key changes:**
1. **Blobs stored first** (deduplicated automatically)
2. **Database records metadata** (no file duplication)
3. **History is projection** (optional human-readable view)

### History Directory: Projection vs Content

**Two approaches for `_history/`:**

#### Option A: Metadata Only (Recommended)

```
_history/
  001_drop_d_20260329_120000_abc123/
    receipt.json       ← Drop metadata
    manifest.json      ← List of entries (blob hashes + paths)
  002_classify.json
```

**manifest.json:**
```json
{
  "drop_id": "d_20260329_120000_abc123",
  "entries": [
    {
      "entry_id": "e_20260329_120000_abc123_001",
      "blob_hash": "abc123...",
      "filename": "invoice.pdf",
      "relative_path": "invoices/Q1/invoice.pdf",
      "size": 45234
    }
  ]
}
```

**Pros:**
- No file duplication
- Compact history
- Single source of truth (blobs/)

**Cons:**
- Can't directly browse drop contents in filesystem
- Need tool to reconstruct

#### Option B: Symlink Tree (Hybrid)

```
_history/
  001_drop_d_20260329_120000_abc123/
    receipt.json
    tree/
      invoice.pdf → ../../../.dwh/blobs/ab/abc123...
      file2.txt → ../../../.dwh/blobs/cd/cde234...
```

**Pros:**
- Human-browsable
- Compatible with existing code
- No content duplication (symlinks)

**Cons:**
- Symlinks may break if blobs deleted
- More complex to maintain

**Decision: Start with Option A** (metadata only), add symlink reconstruction as optional feature later.

### Database Schema Compatibility

**Current schema review:**

```sql
CREATE TABLE blobs (
    hash TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mime_type TEXT,
    stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Status: ✅ Compatible** - Already designed for blob storage
- Just missing the physical files
- No schema changes needed!

```sql
CREATE TABLE entries (
    id TEXT PRIMARY KEY,
    drop_id TEXT NOT NULL REFERENCES drops(id),
    blob_hash TEXT NOT NULL REFERENCES blobs(hash),  ← References blob
    filename TEXT NOT NULL,
    relative_path TEXT,
    source_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Status: ✅ Compatible** - Already uses blob_hash reference
- Entries reference blobs (not files)
- source_path preserves original location
- No changes needed!

```sql
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE REFERENCES entries(id),
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Status: ✅ Compatible** - References entries (which reference blobs)
- Indirect blob reference through entries
- No changes needed!

**Schema assessment: Current schema already designed for blob storage!**

Only change needed: Implement physical blob storage in `.dwh/blobs/`.

### Operations Impact

#### Triage Checkout

**Before:**
```python
# Copy from _history/tree/
for file in tree_dir.rglob("*"):
    shutil.copy2(file, triage_dir / relative_path)
```

**After:**
```python
# Reconstruct from blobs
for entry in entries:
    blob_path = get_blob_path(entry.blob_hash, blobs_dir)
    dest = triage_dir / entry.relative_path
    shutil.copy2(blob_path, dest)
```

**Impact:** Minimal - same copy operation, different source

#### Drop Export

**Before:**
```python
# Copy from _history/tree/
for file in tree_dir.rglob("*"):
    shutil.copy2(file, dest / relative_path)
```

**After:**
```python
# Reconstruct from blobs using entries
for entry in entries:
    blob_path = get_blob_path(entry.blob_hash, blobs_dir)
    dest_path = dest / entry.relative_path
    shutil.copy2(blob_path, dest_path)
```

**Impact:** Minimal - same copy operation, different source

#### Warehouse Files

**No change needed:**
- Warehouse files stay as regular files in categories
- Classification moves/copies from triage to warehouse
- Blobs provide backup/reconstruction capability

### Migration Strategy

**For existing warehouses:**

```python
def migrate_to_blob_storage(warehouse_root: Path, conn: sqlite3.Connection):
    """Migrate existing _history/ trees to blob storage."""
    blobs_dir = warehouse_root / '.dwh' / 'blobs'
    history_dir = warehouse_root / '_history'

    migrated_count = 0

    # Find all drop directories
    for item in sorted(history_dir.iterdir()):
        if item.is_dir() and '_drop_' in item.name:
            tree_dir = item / 'tree'
            if not tree_dir.exists():
                continue

            # Move files to blob storage
            for file in tree_dir.rglob("*"):
                if file.is_file():
                    # Compute hash
                    blob_hash = compute_hash(file)

                    # Move to blob storage if not exists
                    blob_path = get_blob_path(blob_hash, blobs_dir)
                    if not blob_path.exists():
                        blob_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(file, blob_path)
                        migrated_count += 1
                    else:
                        # Blob already exists, just delete duplicate
                        file.unlink()

            # Remove empty tree directory
            if tree_dir.exists():
                shutil.rmtree(tree_dir)

    return migrated_count
```

**Migration command:**
```bash
$ dwh migrate-blobs

Migrating to blob storage...
Scanning _history/ for files...

Found: 1,247 files
Unique blobs: 823 (424 duplicates eliminated)
Migrated: 823 blobs to .dwh/blobs/
Freed: 156 MB (duplicates removed)

✓ Migration complete
```

### Benefits

**Storage efficiency:**
- Automatic deduplication
- Same file imported 100 times = stored once
- Typical savings: 30-70% for document warehouses

**Operational benefits:**
- Fast reconstruction (just copy from blobs)
- Audit can verify blob existence
- Trace can show blob storage location
- Easy backup (just .dwh/blobs/ + dwh.db)

**Architectural benefits:**
- Single source of truth for content
- Schema already compatible
- Foundation for future features:
  - Blob garbage collection
  - Remote blob storage
  - Blob compression
  - Content verification

### Implementation Plan

#### Phase 1: Blob Storage Core (4-5 hours)

1. Implement `get_blob_path()` helper
2. Implement `store_blob()` function
3. Update `drop_import()` to write blobs
4. Create `.dwh/blobs/` directory on init

#### Phase 2: Update Operations (3-4 hours)

1. Update `triage_checkout()` to read from blobs
2. Update `drop_export()` to read from blobs
3. Remove tree/ copying from import
4. Update history writing (manifest.json)

#### Phase 3: Migration Tool (2-3 hours)

1. Implement `migrate_to_blob_storage()`
2. Add `dwh migrate-blobs` command
3. Safe migration (verify hashes match)

#### Phase 4: Testing (4-5 hours)

1. E2E tests for blob storage
2. Test deduplication (same file imported twice)
3. Test triage/export from blobs
4. Test migration from old format

#### Phase 5: Verification (2-3 hours)

1. Add `dwh verify-blobs` command
2. Check all blobs referenced by entries exist
3. Check all blobs on disk are referenced
4. Verify blob hashes match content

**Total effort:** ~15-20 hours

## Trade-offs

**Advantages:**
- ✅ Automatic deduplication (30-70% storage savings)
- ✅ Single source of truth for content
- ✅ Schema already compatible (no DB migration!)
- ✅ Foundation for advanced features
- ✅ Fast reconstruction from blobs
- ✅ Easy backup strategy

**Disadvantages:**
- ⚠ History not directly browsable (need reconstruction)
- ⚠ Migration needed for existing warehouses
- ⚠ Slightly more complex import (hash + store)
- ⚠ Blob garbage collection needed eventually

**Mitigations:**
- Provide `dwh drop export` to browse drops
- Safe migration tool with verification
- Performance impact minimal (hash already computed)
- GC can be manual in v1 (future: auto-gc)

## Future Enhancements

### Blob Garbage Collection

```bash
$ dwh gc-blobs

Scanning for unreferenced blobs...
Found: 47 orphaned blobs (not referenced by any entry)
Total size: 12.3 MB

Delete orphaned blobs? [y/N]
```

### Blob Verification

```bash
$ dwh verify-blobs

Verifying blob integrity...
Checked: 823 blobs
✓ All blob hashes match content
✓ All entries reference existing blobs
```

### Remote Blob Storage

Future: Store blobs in S3/cloud while keeping metadata local:
```
.dwh/
  dwh.db           ← Local
  blobs/           ← Could be S3/GCS/Azure
```

### Blob Compression

Store blobs compressed (like Git):
```python
# Compress on write
with gzip.open(blob_path, 'wb') as f:
    f.write(content)

# Decompress on read
with gzip.open(blob_path, 'rb') as f:
    content = f.read()
```

## Decision

Implement content-addressed blob storage in `.dwh/blobs/` with 2-char prefix sharding.

**Import workflow:**
1. Compute hash for each file
2. Store blob if not exists (deduplicated)
3. Record metadata in database
4. Write history manifest (no file duplication)

**History format:**
- Metadata only (receipt.json + manifest.json)
- No tree/ directory
- Reconstruct from blobs when needed

**Schema:**
- No changes needed (already compatible!)
- Current references (blob_hash) already correct

**Migration:**
- Provide `dwh migrate-blobs` for existing warehouses
- Safe migration with verification
- Automatic deduplication during migration

This establishes blob storage as the foundation for all future features (audit, trace, GC, remote storage).

## References

- ADR-001: Drop-based archival (original content storage design)
- ADR-007: Warehouse audit (assumes blob storage)
- ADR-008: File provenance trace (assumes blob storage)
- Git object storage (similar content-addressed approach)
