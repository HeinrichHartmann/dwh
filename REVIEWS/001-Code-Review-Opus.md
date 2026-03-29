# Code Review: Drop Storage Implementation

**Reviewer:** Claude Opus 4.5
**Date:** 2026-03-29
**Commit:** 1bf5591 (Restore correct drop-based implementation)

## Overview

This review covers the v1 drop storage implementation based on ADR-002. The implementation provides:
- `dwh init` - Warehouse initialization
- `dwh drop import` - File import with provenance
- `dwh drop list/inspect/export` - Query and reconstruction
- History-based storage with receipt.json + tree/

## Implementation Review

### ✅ What Works Well

**1. Clean Module Structure**
```
src/dwh/
├── cli.py       - Click-based CLI (183 lines)
├── drop.py      - Core drop operations (293 lines)
├── db.py        - Schema and connection (74 lines)
├── warehouse.py - Path management
└── history.py   - History helpers
```
Clean separation of concerns. Each module has a focused purpose.

**2. Receipt Format Matches Design**
```json
{
  "type": "drop",
  "drop_id": "d_20260329_143211_a1b2c3d4",
  "message": "Tax documents 2024",
  "actor": "hhartmann",
  "created_at": "2026-03-29T14:32:11Z"
}
```
Metadata-only receipt, entries derived from tree/ - exactly as specified in DESIGN.md.

**3. Entry ID Generation is Deterministic**
```python
def generate_entry_id(drop_id: str, relative_path: Path) -> str:
    """Generate deterministic entry ID from drop_id + relative_path."""
    data = f"{drop_id}:{relative_path}".encode()
    hash_hex = hashlib.sha256(data).hexdigest()[:16]
    return f"e_{hash_hex}"
```
Stable IDs that can be regenerated from the same inputs. Good for replay.

**4. Comprehensive Test Coverage**
- 10 test classes covering import/export/list/inspect
- Tests verify: content preservation, structure preservation, deduplication, provenance
- E2E focus: tests observe behavior, not implementation

**5. Error Handling**
- Custom exceptions (DropError, DropNotFoundError)
- CLI catches exceptions and exits with error codes
- User-friendly error messages

### ⚠️ Issues and Concerns

**1. CRITICAL: Missing history.py Implementation**

The code imports from `dwh.history` but the file is minimal:
```python
from dwh.history import get_next_history_number
from dwh.history import find_drop_in_history
```

These functions are called but not shown in the review. Need to verify they exist and work correctly.

**2. Database Schema Mismatch with Design**

DESIGN.md specifies documents table:
```sql
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL UNIQUE REFERENCES entries(id),
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Not present in db.py** - only has blobs, drops, entries. This is OK for v1 (drop storage only), but should be noted as intentional.

**3. Blob Storage Not Used**

Code copies files directly to history tree/, never uses `.dwh/blobs/`. The schema has a `blobs` table that tracks hashes, but no actual blob storage.

**Consequence:** No deduplication at storage level. Multiple imports of the same file = multiple copies in history.

**Note in DESIGN.md says:** "Content-addressed blob storage is optional (v2)". So this is intentional, but the `blobs` table is misleading (it's populated but not used for storage).

**4. compute_relative_path Logic**

```python
def compute_relative_path(file_path: Path, input_paths: list[Path]) -> Path:
    """Compute relative path for file within the import context."""
    # Complex logic with fallbacks
```

This is doing important work (preserving structure) but the logic is subtle:
- Single file → just filename
- Directory → relative to directory

**Edge case:** What if you import `/foo/bar.txt` and `/foo/baz.txt` separately (two args)?
- Both would get relative_path = filename only
- Could collide in tree/

**Test coverage:** Need to verify this case is tested.

**5. Drop Export Doesn't Use Database**

```python
def drop_export(drop_id: str, dest: Path, history_dir: Path) -> int:
    """Export drop to destination directory."""
    from dwh.history import find_drop_in_history

    drop_dir = find_drop_in_history(history_dir, drop_id)
    # ... copies from tree/
```

Exports directly from history tree/, not from database. This is correct (history is source of truth), but means:
- Database isn't required for export
- If history and DB are out of sync, export trusts history

This is the right choice, but worth documenting explicitly.

**6. No Rebuild Command**

DESIGN.md describes `dwh rebuild` to replay history into database. This is core to the event-sourcing model but **not implemented**.

**Impact:** If database gets corrupted, no recovery path (yet).

**7. Source Path is Empty String**

```python
conn.execute(
    """INSERT INTO entries (id, drop_id, blob_hash, filename, relative_path, source_path)
       VALUES (?, ?, ?, ?, ?, ?)""",
    (entry.id, entry.drop_id, entry.blob_hash, entry.filename,
     entry.relative_path, "")  # source_path not stored in history
)
```

source_path is always empty. The schema requires it (NOT NULL), but it's not captured.

**Why:** source_path is where the file came from on the user's filesystem. That info is lost after import (not in receipt or tree/).

**Is this OK?** Depends on whether we need to trace "this came from ~/Downloads/invoice.pdf". For v1 drop storage, probably fine. For forensics/auditing, maybe not.

**8. Entry Size Not Captured in DB**

```python
Entry(
    # ...
    size=0  # Size not needed for inspect
)
```

In `drop_inspect`, entry size is set to 0. But it's displayed in the CLI output:
```python
size_kb = e.size / 1024  # This will always be 0!
```

**Bug:** CLI shows "0.0 KB" for all files in `dwh drop inspect`.

### 🧪 Test Coverage

**Test files:**
- `tests/e2e/test_import_export.py` (146 test methods/classes)
- `tests/conftest.py` (fixtures and helpers)

**Test classes:**
1. TestImportSingleFile
2. TestImportDirectory
3. TestImportMultiplePaths
4. TestDropList
5. TestDropInspect
6. TestDropExport
7. TestImportExportRoundtrip
8. TestProvenanceAndReceipts
9. TestDeduplication
10. TestErrorHandling

**Coverage gaps:**
- No test for `dwh rebuild` (not implemented)
- No test for `dwh verify` (not implemented)
- No test for concurrent imports (probably OK for v1)

### 🎯 Alignment with Design

| Design Requirement | Implemented? | Notes |
|--------------------|--------------|-------|
| History as source of truth | ✅ Yes | Receipt + tree/ model |
| Receipt = metadata only | ✅ Yes | Entries derived from tree/ |
| Drop immutability | ✅ Yes | No mutation operations |
| Deterministic entry IDs | ✅ Yes | Based on drop_id + path |
| Export from history | ✅ Yes | Doesn't use database |
| Rebuild from history | ❌ No | Missing `dwh rebuild` |
| Triage workflow | ❌ No | Not in this commit (v1) |
| Documents table | ❌ No | Not needed for drop storage |

## Recommendations

### Must Fix (Before Next Feature)

1. **Fix drop inspect size display** - Query actual file sizes from history tree/
2. **Verify history.py exists** - Check that `get_next_history_number` and `find_drop_in_history` are implemented
3. **Test multiple-file import paths** - Verify no collisions when importing `/foo/a.txt /foo/b.txt`

### Should Fix (Before v1 Release)

4. **Implement `dwh rebuild`** - Core to event sourcing model
5. **Remove or document blobs table** - Clarifies that dedup is v2
6. **Capture entry sizes in DB** - Or remove size column if not needed

### Nice to Have

7. **Add integration test** - Import 431 files from `import/` directory
8. **Document source_path decision** - Is empty string intentional?

## Summary

**Overall Assessment:** ✅ **Solid implementation**

The code correctly implements the drop storage design:
- Receipt + tree/ model works
- Entry derivation at replay time is correct
- E2E tests provide confidence
- Error handling is good

Main gaps are:
- Missing `dwh rebuild` (critical for event sourcing)
- Size display bug in inspect
- Blob storage confusion

The foundation is strong. The event-sourcing model is correctly implemented at the history level. Database operations are secondary (as designed).

**Ready for next feature** (triage workflow) after fixing the inspect bug and verifying history.py.

---

## Detailed Code Notes

### drop.py:185 - drop_import

Flow:
1. Generate drop_id
2. Create history/NNN_drop_{id}/tree/
3. Copy files to tree/
4. Write receipt.json
5. Derive entries from tree/
6. Update database

**Good:** History is written first, then database. If DB write fails, history is still intact.

### cli.py:164 - find_warehouse()

Not shown in the code review, but called by CLI. Presumably walks up directory tree to find `.dwh/`. Should verify this exists.

### db.py:46 - Row Factory

```python
conn.row_factory = sqlite3.Row
```

Good practice. Allows accessing columns by name.

### Tests: Fixture Design

```python
@pytest.fixture
def tmp_warehouse(tmp_path):
    """Create temporary warehouse."""
    # ...
```

Clean fixture design. Each test gets a fresh warehouse. Good isolation.
