# ADR-007: Warehouse Audit Command

**Status:** Proposed
**Date:** 2026-03-29
**Context:** Warehouse consistency checking, filesystem drift detection

## Problem

The warehouse filesystem can drift from database state over time:

**Drift scenarios:**
1. **Orphaned files**: User manually copies files to warehouse (not imported)
2. **Missing files**: Files deleted manually but documents remain in DB
3. **Relocated files**: User moves files between categories outside of DWH
4. **Duplicates**: Same blob appears in multiple locations (valid but worth knowing)

**Current situation:**
- No way to verify warehouse consistency
- No detection of manual changes
- User doesn't know if filesystem matches DB state
- Unclear what to do with orphaned/missing files

**Need:** Command to audit warehouse and report discrepancies.

## Solution

Implement `dwh audit` command to check warehouse filesystem consistency.

### Command Signature

```bash
dwh audit [PATH]

# Examples:
dwh audit                           # Audit entire warehouse
dwh audit finance/                  # Audit finance/ subtree
dwh audit finance/taxes/2024/       # Audit specific directory
```

**Arguments:**
- `PATH` (optional): Directory to audit, relative to warehouse root
  - Default: `.` (entire warehouse)
  - Can be category or subcategory
  - Must be within warehouse

**Flags:**
- None in v1 (no auto-fix, just report)

### Audit Checks

The audit performs four checks:

#### 1. Orphaned Files

Files exist in warehouse but have no document record.

**Detection:**
```python
# For each file in filesystem:
#   Compute hash
#   Query: SELECT * FROM documents d JOIN entries e ON d.entry_id = e.id
#          WHERE e.blob_hash = ?
#   If no result → orphaned
```

**Possible causes:**
- User manually copied file to warehouse
- Document record was manually deleted from DB
- File from failed/incomplete import

**Report format:**
```
Orphaned Files (3):
  finance/taxes/2024/unknown.pdf
    Hash: abc123...
    Size: 45.2 KB
    Not tracked in database

  work/projects/mystery.doc
    Hash: def456...
    Size: 128 KB
    Not tracked in database
```

#### 2. Missing Files

Document records exist but files not found at expected location.

**Detection:**
```python
# For each document with category != '':
#   expected_path = category / name
#   If file doesn't exist at expected_path:
#     Check if blob exists elsewhere (relocated vs missing)
```

**Possible causes:**
- User manually deleted file
- File moved/renamed outside DWH
- Filesystem corruption

**Report format:**
```
Missing Files (2):
  personal/photos/vacation.jpg
    Document: 42
    Entry: e_20260329_120000_abc123_005
    Hash: ghi789...
    File not found on disk

  finance/invoices/Q1/bill.pdf
    Document: 15
    Entry: e_20260320_140000_bbb123_003
    Hash: jkl012...
    File not found on disk
```

#### 3. Relocated Files

Files moved from their recorded category location.

**Detection:**
```python
# For each document:
#   expected_path = category / name
#   If file missing at expected_path:
#     Search warehouse for blob_hash
#     If found elsewhere → relocated
```

**Possible causes:**
- User manually reorganized files
- File moved between categories without updating DB
- Intent to reclassify but didn't sync

**Report format:**
```
Relocated Files (1):
  finance/invoices/Q1/bill.pdf → finance/taxes/2024/bill.pdf
    Document: 15 (recorded category: finance/invoices/Q1)
    Actual location: finance/taxes/2024/bill.pdf
    Hash: jkl012...
    File moved from recorded location
```

#### 4. Duplicates

Same blob appears in multiple warehouse locations.

**Detection:**
```python
# Group filesystem files by hash
# Report any hash with count > 1
```

**Note:** This is not necessarily an error (same document legitimately in multiple categories), but worth reporting.

**Report format:**
```
Duplicate Content (1 blob in 2 locations):
  Hash: mno345... (contract.pdf)
    1. work/contracts/client-a/contract.pdf
       Document: 50, Category: work/contracts/client-a
    2. archive/2024/Q1/contract.pdf
       Document: 78, Category: archive/2024/Q1
    Same content in multiple categories
```

### Algorithm

```python
def audit_warehouse(warehouse_root: Path, audit_path: Path, conn: sqlite3.Connection):
    """Audit warehouse filesystem consistency.

    Args:
        warehouse_root: Warehouse root directory
        audit_path: Path to audit (relative to warehouse_root)
        conn: Database connection

    Returns:
        Dictionary with orphans, missing, relocated, duplicates
    """
    # Resolve audit path
    full_audit_path = warehouse_root / audit_path
    if not full_audit_path.exists():
        raise AuditError(f"Path not found: {audit_path}")

    # Scan filesystem (skip system dirs)
    system_dirs = {'.dwh', '_history', '_triage', '_staging'}
    fs_files = {}  # {rel_path: hash}
    hash_locations = {}  # {hash: [paths]}

    for item in full_audit_path.rglob("*"):
        if item.is_file():
            # Check if any parent is a system dir
            rel_to_root = item.relative_to(warehouse_root)
            if any(part in system_dirs for part in rel_to_root.parts):
                continue

            hash = compute_hash(item)
            rel_path = str(rel_to_root)
            fs_files[rel_path] = hash

            if hash not in hash_locations:
                hash_locations[hash] = []
            hash_locations[hash].append(rel_path)

    # Get all documents in audit scope
    if audit_path == Path('.'):
        # Audit entire warehouse
        docs = conn.execute("""
            SELECT d.id, d.entry_id, d.name, d.category, e.blob_hash
            FROM documents d
            JOIN entries e ON d.entry_id = e.id
            WHERE d.category != ''
        """).fetchall()
    else:
        # Audit specific subtree
        prefix = str(audit_path)
        docs = conn.execute("""
            SELECT d.id, d.entry_id, d.name, d.category, e.blob_hash
            FROM documents d
            JOIN entries e ON d.entry_id = e.id
            WHERE d.category != '' AND d.category LIKE ?
        """, (f"{prefix}%",)).fetchall()

    # Check results
    orphans = []
    missing = []
    relocated = []
    db_hashes = set()

    # Check each document
    for doc in docs:
        expected_path = f"{doc['category']}/{doc['name']}"
        hash = doc['blob_hash']
        db_hashes.add(hash)

        if expected_path in fs_files:
            # File exists at expected location
            if fs_files[expected_path] != hash:
                # Different content! (shouldn't happen but check)
                pass  # Could add content-mismatch check
        else:
            # File not at expected location
            if hash in hash_locations:
                # File exists but in wrong location
                relocated.append({
                    'document_id': doc['id'],
                    'entry_id': doc['entry_id'],
                    'expected': expected_path,
                    'actual': hash_locations[hash],
                    'hash': hash
                })
            else:
                # File completely missing
                missing.append({
                    'document_id': doc['id'],
                    'entry_id': doc['entry_id'],
                    'path': expected_path,
                    'hash': hash
                })

    # Check for orphans
    for path, hash in fs_files.items():
        if hash not in db_hashes:
            orphans.append({
                'path': path,
                'hash': hash,
                'size': (warehouse_root / path).stat().st_size
            })

    # Check for duplicates
    duplicates = []
    for hash, locations in hash_locations.items():
        if len(locations) > 1:
            # Get document info for each location
            docs_for_hash = [doc for doc in docs if doc['blob_hash'] == hash]
            duplicates.append({
                'hash': hash,
                'locations': locations,
                'documents': docs_for_hash
            })

    return {
        'orphans': orphans,
        'missing': missing,
        'relocated': relocated,
        'duplicates': duplicates,
        'total_files': len(fs_files),
        'total_documents': len(docs)
    }
```

### Output Format

```bash
$ dwh audit

Warehouse Audit Report
======================
Audited: 147 files, 145 documents

✓ 142 files correctly tracked

Issues Found: 5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Orphaned Files (2):
  Files in warehouse but not tracked in database

  finance/taxes/2024/unknown.pdf
    Hash: abc123def456...
    Size: 45.2 KB
    → Import this file: dwh drop import finance/taxes/2024/unknown.pdf

  work/projects/mystery.doc
    Hash: def456789abc...
    Size: 128 KB
    → Import this file: dwh drop import work/projects/mystery.doc

Missing Files (2):
  Documents in database but files not found on disk

  personal/photos/vacation.jpg
    Document: 42, Entry: e_20260329_120000_abc123_005
    Hash: ghi789012def...
    → Restore from blob: cp .dwh/blobs/gh/ghi789... personal/photos/vacation.jpg

  finance/invoices/Q1/bill.pdf
    Document: 15, Entry: e_20260320_140000_bbb123_003
    Hash: jkl012345ghi...
    → Restore from blob: cp .dwh/blobs/jk/jkl012... finance/invoices/Q1/bill.pdf

Relocated Files (1):
  Files moved from recorded location

  finance/taxes/2024/bill.pdf (expected: finance/invoices/Q1/bill.pdf)
    Document: 15
    Hash: jkl012345ghi...
    → File moved outside DWH, database not updated
    → Restore: mv finance/taxes/2024/bill.pdf finance/invoices/Q1/bill.pdf
    → Or re-classify: (manual triage workflow)

Duplicates (1 blob in 2 locations):
  Same content in multiple categories

  Hash: mno345678jkl... (contract.pdf, 256 KB)
    1. work/contracts/client-a/contract.pdf (Document: 50)
    2. archive/2024/Q1/contract.pdf (Document: 78)
    → Both classifications are valid (same document in 2 categories)
```

**Audit specific subtree:**
```bash
$ dwh audit finance/

Warehouse Audit Report
======================
Audited: finance/
Files: 42, Documents: 41

✓ 41 files correctly tracked

Issues Found: 1
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Orphaned Files (1):
  finance/taxes/2024/unknown.pdf
    Hash: abc123...
    Size: 45.2 KB
```

**No issues:**
```bash
$ dwh audit

Warehouse Audit Report
======================
Audited: 147 files, 147 documents

✓ Warehouse is consistent!
  All files correctly tracked, no orphans or missing files.
```

### Exit Codes

```
0 - Audit passed (no issues)
1 - Issues found (orphans, missing, relocated)
2 - Error (invalid path, database error)
```

Useful for scripts:
```bash
if ! dwh audit; then
    echo "Warehouse has consistency issues!"
    exit 1
fi
```

### Integration with Other Commands

**Suggested workflow for fixing issues:**

1. **Orphaned files** → Import them:
   ```bash
   dwh drop import <orphaned-file>
   ```

2. **Missing files** → Restore from blobs:
   ```bash
   # Blob path from audit output
   cp .dwh/blobs/ab/abc123... <original-path>
   ```

3. **Relocated files** → Move back or re-classify:
   ```bash
   # Option A: Move back
   mv <actual-path> <expected-path>

   # Option B: Accept new location (re-triage)
   # (Future: dwh reclassify command)
   ```

4. **Duplicates** → Usually OK, or deduplicate:
   ```bash
   # If duplicate is unintentional:
   rm <duplicate-path>
   ```

### Implementation Plan

#### Phase 1: Core Audit (4-5 hours)

1. Implement `audit_warehouse()` function
2. Add `dwh audit` CLI command
3. Basic output formatting
4. Exit codes

#### Phase 2: Report Formatting (2-3 hours)

1. Colored output (✓ ⚠ →)
2. Helpful suggestions for each issue type
3. Summary statistics

#### Phase 3: Subtree Audit (1-2 hours)

1. Path argument handling
2. Scope filtering for documents query
3. Relative path display

#### Phase 4: Testing (3-4 hours)

1. E2E tests for each issue type
2. Test subtree audits
3. Test exit codes

**Total effort:** ~10-14 hours

## Trade-offs

**Advantages:**
- Detect filesystem drift early
- Clear actionable output
- Subtree auditing for large warehouses
- No modifications (safe, read-only operation)
- Exit codes for scripting

**Disadvantages:**
- No auto-fix in v1 (manual remediation)
- Large warehouses may be slow (hash every file)
- Duplicate detection may report valid scenarios

**Mitigations:**
- Provide clear fix suggestions in output
- Subtree auditing for targeted checks
- Future: `--fix` flag for auto-remediation
- Future: `--fast` mode (skip hash computation, only check existence)

## Future Enhancements

### Auto-Fix Mode

```bash
$ dwh audit --fix

Fixing Issues:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Restored 2 missing files from blobs
⚠ 2 orphaned files (manual import required)
⚠ 1 relocated file (manual decision required)
```

### Fast Mode

```bash
$ dwh audit --fast
# Skip hash computation, only check if files exist at expected paths
```

### Scheduled Audits

Run audit on cron, email report if issues found:
```bash
0 2 * * * cd /path/to/warehouse && dwh audit || mail -s "Warehouse issues" user@example.com
```

### Audit History

Track audit results over time:
```bash
$ dwh audit --save
# Saves audit report to _history/audit/YYYY-MM-DD.json

$ dwh audit --compare
# Compare current state to last audit
```

## Decision

Implement `dwh audit [PATH]` command for warehouse consistency checking.

Reports four issue types:
- Orphaned files (in FS, not in DB)
- Missing files (in DB, not in FS)
- Relocated files (moved from recorded location)
- Duplicates (same blob in multiple locations)

Read-only operation with helpful fix suggestions. Exit codes for scripting.

## References

- ADR-003: Warehouse layout and filesystem structure
- ADR-006: Triage queue (documents table structure)
