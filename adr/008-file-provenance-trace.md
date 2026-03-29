# ADR-008: File Provenance Trace Command

**Status:** Proposed
**Date:** 2026-03-29
**Context:** Understanding file history, debugging imports, tracking content

## Problem

Users need to answer provenance questions about warehouse content:

**Common questions:**
1. "Where did this file come from originally?"
2. "When was this imported?"
3. "Has this file been imported multiple times?"
4. "What's the classification history for this content?"
5. "Where else does this content appear in my warehouse?"
6. "Which drop does this file belong to?"

**Current situation:**
- No way to trace file origins
- Can't see if content imported multiple times
- No visibility into classification history
- Must manually inspect `_history/` JSON files
- Can't query by blob hash or ID

**Need:** Command to trace complete provenance for files, blobs, or database IDs.

## Solution

Implement `dwh trace` command with overloaded input types.

### Command Signature

```bash
dwh trace <IDENTIFIER>

# Identifier can be:
dwh trace finance/taxes/2024/invoice.pdf  # File path
dwh trace abc123def456...                 # Blob hash
dwh trace e_20260329_120000_abc123_005    # Entry ID
dwh trace d_20260329_120000_abc123        # Drop ID
dwh trace 42                              # Document ID (integer)
```

**Overloaded input types:**

1. **File path** (contains `/` or is existing file):
   - Relative to warehouse root
   - Must exist in warehouse
   - Shows provenance for that specific file

2. **Blob hash** (64 hex characters, starts with lowercase):
   - SHA-256 hash of file content
   - Shows all imports/classifications of this content
   - Useful for finding deleted files

3. **Entry ID** (format `e_YYYYMMDD_HHMMSS_HASH_NNN`):
   - Shows specific import instance
   - Single drop, single classification

4. **Drop ID** (format `d_YYYYMMDD_HHMMSS_HASH`):
   - Shows all entries from a drop
   - Summary of drop classification status

5. **Document ID** (integer):
   - Shows specific classification record
   - Links to entry and drop

### Output Sections

#### 1. File/Blob Identity

Basic information about the content:

```
File: finance/taxes/2024/invoice.pdf
Hash: abc123def456789abcdef...
Size: 45.2 KB
Type: application/pdf
Modified: 2026-03-29 12:15:30
```

Or for hash lookup:
```
Blob: abc123def456789abcdef...
Size: 45.2 KB
Type: application/pdf
Status: Content imported 3 times, currently in 2 warehouse locations
```

#### 2. Import History

All drops that imported this content:

```
Import History (3 drops)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Drop: d_20260315_100000_aaa111
   Message: "Email attachments"
   Actor: heinrich
   Date: 2026-03-15 10:00:00

   Entry: e_20260315_100000_aaa111_003
   Source: /tmp/thunderbird/attachments/contract_v1.pdf
   Relative: contract_v1.pdf

2. Drop: d_20260320_140000_bbb222
   Message: "Downloads cleanup"
   Actor: heinrich
   Date: 2026-03-20 14:00:00

   Entry: e_20260320_140000_bbb222_007
   Source: /Users/heinrich/Downloads/contract_final.pdf
   Relative: contract_final.pdf

3. Drop: d_20260329_120000_ccc333
   Message: "Project files"
   Actor: heinrich
   Date: 2026-03-29 12:00:00

   Entry: e_20260329_120000_ccc333_001
   Source: /Users/heinrich/projects/client-a/contract.pdf
   Relative: contract.pdf
```

#### 3. Classification History

All document records for this content:

```
Classification History (4 records)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Document: 35
   Entry: e_20260315_100000_aaa111_003
   Category: archive/old
   Name: contract_v1.pdf
   Classified: 2026-03-15 10:30:00
   Event: _history/005_classify.json

2. Document: 52 [TOMBSTONE]
   Entry: e_20260315_100000_aaa111_003
   Category: (excluded)
   Name: contract_v1.pdf
   Classified: 2026-03-16 09:00:00
   Event: _history/007_classify.json
   Note: File was excluded during re-triage

3. Document: 78
   Entry: e_20260320_140000_bbb222_007
   Category: work/contracts
   Name: contract_final.pdf
   Classified: 2026-03-20 14:15:00
   Event: _history/012_classify.json

4. Document: 91
   Entry: e_20260329_120000_ccc333_001
   Category: work/client-a
   Name: contract.pdf
   Classified: 2026-03-29 12:15:00
   Event: _history/018_classify.json
```

#### 4. Current Locations

Where this content currently exists in warehouse:

```
Current Locations (2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. work/contracts/contract_final.pdf
   Document: 78
   Category: work/contracts

2. work/client-a/contract.pdf
   Document: 91
   Category: work/client-a

Note: Same content in multiple locations (valid - different classifications)
```

Or if deleted:
```
Current Locations: None
Note: All classifications are tombstones or files have been deleted
Blob still in storage: .dwh/blobs/ab/abc123def456...
```

#### 5. Blob Storage

Physical storage location:

```
Blob Storage
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Path: .dwh/blobs/ab/abc123def456789abcdef...
Size: 45.2 KB
Stored: 2026-03-15 10:00:00

Restore command:
  cp .dwh/blobs/ab/abc123... <destination>
```

### Algorithm

```python
def trace(identifier: str, warehouse_root: Path, conn: sqlite3.Connection):
    """Trace provenance for file, hash, or ID.

    Args:
        identifier: File path, blob hash, entry_id, drop_id, or document_id
        warehouse_root: Warehouse root path
        conn: Database connection

    Returns:
        Dictionary with import history, classifications, locations
    """
    # Detect identifier type
    if identifier.isdigit():
        # Document ID
        return trace_document_id(int(identifier), conn)
    elif identifier.startswith('d_'):
        # Drop ID
        return trace_drop_id(identifier, conn)
    elif identifier.startswith('e_'):
        # Entry ID
        return trace_entry_id(identifier, conn)
    elif len(identifier) == 64 and all(c in '0123456789abcdef' for c in identifier):
        # Blob hash (SHA-256 = 64 hex chars)
        return trace_blob_hash(identifier, warehouse_root, conn)
    else:
        # Assume file path
        file_path = warehouse_root / identifier
        if not file_path.exists():
            raise TraceError(f"File not found: {identifier}")
        hash = compute_hash(file_path)
        return trace_blob_hash(hash, warehouse_root, conn, file_path=file_path)


def trace_blob_hash(hash: str, warehouse_root: Path, conn: sqlite3.Connection,
                    file_path: Path | None = None):
    """Trace provenance for a blob hash."""
    # Get blob info
    blob = conn.execute(
        "SELECT * FROM blobs WHERE hash = ?", (hash,)
    ).fetchone()

    if not blob:
        raise TraceError(f"Blob not found: {hash}")

    # Get all entries with this hash
    entries = conn.execute("""
        SELECT e.*, d.message, d.actor, d.created_at as drop_date
        FROM entries e
        JOIN drops d ON e.drop_id = d.id
        WHERE e.blob_hash = ?
        ORDER BY d.created_at
    """, (hash,)).fetchall()

    # Get all documents for this blob
    documents = conn.execute("""
        SELECT d.*, e.drop_id, dr.message as drop_message
        FROM documents d
        JOIN entries e ON d.entry_id = e.id
        JOIN drops dr ON e.drop_id = dr.id
        WHERE e.blob_hash = ?
        ORDER BY d.created_at
    """, (hash,)).fetchall()

    # Find current locations in warehouse
    locations = []
    for item in warehouse_root.rglob("*"):
        if item.is_file():
            # Skip system dirs
            rel_path = item.relative_to(warehouse_root)
            if any(part in {'.dwh', '_history', '_triage', '_staging'}
                   for part in rel_path.parts):
                continue

            if compute_hash(item) == hash:
                # Find document for this location
                doc = conn.execute("""
                    SELECT d.id, d.category
                    FROM documents d
                    JOIN entries e ON d.entry_id = e.id
                    WHERE e.blob_hash = ? AND d.category || '/' || d.name = ?
                """, (hash, str(rel_path))).fetchone()

                locations.append({
                    'path': str(rel_path),
                    'document_id': doc['id'] if doc else None,
                    'category': doc['category'] if doc else None
                })

    # Blob storage path
    blob_path = warehouse_root / '.dwh' / 'blobs' / hash[:2] / hash

    return {
        'hash': hash,
        'size': blob['size'],
        'mime_type': blob.get('mime_type'),
        'file_path': str(file_path) if file_path else None,
        'entries': entries,
        'documents': documents,
        'locations': locations,
        'blob_path': blob_path
    }


def trace_entry_id(entry_id: str, conn: sqlite3.Connection):
    """Trace provenance for a specific entry."""
    entry = conn.execute("""
        SELECT e.*, d.message, d.actor, d.created_at as drop_date
        FROM entries e
        JOIN drops d ON e.drop_id = d.id
        WHERE e.id = ?
    """, (entry_id,)).fetchone()

    if not entry:
        raise TraceError(f"Entry not found: {entry_id}")

    # Get document for this entry
    docs = conn.execute(
        "SELECT * FROM documents WHERE entry_id = ?", (entry_id,)
    ).fetchall()

    return {
        'entry': entry,
        'documents': docs,
        'blob_hash': entry['blob_hash']
    }


def trace_drop_id(drop_id: str, conn: sqlite3.Connection):
    """Trace all entries from a drop."""
    drop = conn.execute(
        "SELECT * FROM drops WHERE id = ?", (drop_id,)
    ).fetchone()

    if not drop:
        raise TraceError(f"Drop not found: {drop_id}")

    # Get all entries
    entries = conn.execute(
        "SELECT * FROM entries WHERE drop_id = ? ORDER BY id", (drop_id,)
    ).fetchall()

    # Get classification status
    classifications = []
    for entry in entries:
        doc = conn.execute(
            "SELECT * FROM documents WHERE entry_id = ?", (entry['id'],)
        ).fetchone()
        classifications.append({
            'entry': entry,
            'document': doc
        })

    return {
        'drop': drop,
        'entries': entries,
        'classifications': classifications
    }


def trace_document_id(doc_id: int, conn: sqlite3.Connection):
    """Trace provenance for a specific document."""
    doc = conn.execute("""
        SELECT d.*, e.blob_hash, e.drop_id, dr.message
        FROM documents d
        JOIN entries e ON d.entry_id = e.id
        JOIN drops dr ON e.drop_id = dr.id
        WHERE d.id = ?
    """, (doc_id,)).fetchone()

    if not doc:
        raise TraceError(f"Document not found: {doc_id}")

    return {
        'document': doc,
        'entry_id': doc['entry_id'],
        'drop_id': doc['drop_id'],
        'blob_hash': doc['blob_hash']
    }
```

### Output Examples

#### Trace by file path

```bash
$ dwh trace finance/taxes/2024/invoice.pdf

File: finance/taxes/2024/invoice.pdf
Hash: abc123def456789abcdef012345678901234567890123456789012345678901
Size: 45.2 KB
Type: application/pdf
Modified: 2026-03-29 12:15:30

Import History (1 drop)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Drop: d_20260329_120000_abc123
  Message: "Import Q1 invoices"
  Actor: heinrich
  Date: 2026-03-29 12:00:00

  Entry: e_20260329_120000_abc123_005
  Source: /Users/heinrich/Downloads/invoices/Q1/invoice.pdf
  Relative: Q1/invoice.pdf

Classification History (1 record)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Document: 42
  Entry: e_20260329_120000_abc123_005
  Category: finance/taxes/2024
  Name: invoice.pdf
  Classified: 2026-03-29 12:15:00
  Event: _history/003_classify.json

Current Locations (1)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. finance/taxes/2024/invoice.pdf (this file)
   Document: 42

Blob Storage
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Path: .dwh/blobs/ab/abc123def456...
Size: 45.2 KB
Stored: 2026-03-29 12:00:00
```

#### Trace by blob hash (multiple imports)

```bash
$ dwh trace abc123def456789abcdef012345678901234567890123456789012345678901

Blob: abc123def456789abcdef012345678901234567890123456789012345678901
Size: 256 KB
Type: application/pdf
Status: Imported 3 times, currently in 2 warehouse locations

Import History (3 drops)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Drop: d_20260315_100000_aaa111
   Message: "Email attachments"
   Actor: heinrich
   Date: 2026-03-15 10:00:00
   Entry: e_20260315_100000_aaa111_003
   Source: /tmp/attachments/contract_v1.pdf

2. Drop: d_20260320_140000_bbb222
   Message: "Downloads"
   Actor: heinrich
   Date: 2026-03-20 14:00:00
   Entry: e_20260320_140000_bbb222_007
   Source: /Users/heinrich/Downloads/contract_final.pdf

3. Drop: d_20260329_120000_ccc333
   Message: "Project files"
   Actor: heinrich
   Date: 2026-03-29 12:00:00
   Entry: e_20260329_120000_ccc333_001
   Source: /Users/heinrich/projects/client-a/contract.pdf

Classification History (3 records)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Document: 35
   Entry: e_20260315_100000_aaa111_003
   Category: archive/old
   Classified: 2026-03-15 10:30:00

2. Document: 78
   Entry: e_20260320_140000_bbb222_007
   Category: work/contracts
   Classified: 2026-03-20 14:15:00

3. Document: 91
   Entry: e_20260329_120000_ccc333_001
   Category: work/client-a
   Classified: 2026-03-29 12:15:00

Current Locations (2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. work/contracts/contract_final.pdf
   Document: 78

2. work/client-a/contract.pdf
   Document: 91

Blob Storage
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Path: .dwh/blobs/ab/abc123def456...
Size: 256 KB
```

#### Trace by entry ID

```bash
$ dwh trace e_20260329_120000_abc123_005

Entry: e_20260329_120000_abc123_005
Drop: d_20260329_120000_abc123
  Message: "Import Q1 invoices"
  Date: 2026-03-29 12:00:00

Source: /Users/heinrich/Downloads/invoices/Q1/invoice.pdf
Relative path: Q1/invoice.pdf
Blob hash: abc123def456...
Size: 45.2 KB

Classification
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Document: 42
  Category: finance/taxes/2024
  Name: invoice.pdf
  Classified: 2026-03-29 12:15:00
  Location: finance/taxes/2024/invoice.pdf
```

#### Trace by drop ID

```bash
$ dwh trace d_20260329_120000_abc123

Drop: d_20260329_120000_abc123
Message: "Import Q1 invoices"
Actor: heinrich
Date: 2026-03-29 12:00:00
Fingerprint: xyz789abc...

Entries: 10 files
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Classification Status: 8/10 complete (2 pending)

Classified (8):
  ✓ invoice-001.pdf → finance/taxes/2024 (Doc: 42)
  ✓ invoice-002.pdf → finance/taxes/2024 (Doc: 43)
  ✓ receipt-001.jpg → finance/receipts (Doc: 44)
  ✓ .DS_Store → [EXCLUDED] (Doc: 45)
  ✓ statement.pdf → finance/bank (Doc: 46)
  ✓ notes.txt → work/project-alpha (Doc: 47)
  ✓ README.md → documentation (Doc: 48)
  ✓ invoice-003.pdf → finance/taxes/2024 (Doc: 49)

Pending (2):
  ⏳ contract.pdf (e_..._009) - in _triage/
  ⏳ agreement.pdf (e_..._010) - in _triage/

History Events:
  001_drop.json
  003_classify.json (8 files)
```

### Implementation Plan

#### Phase 1: Core Trace (4-5 hours)

1. Implement identifier detection
2. Implement trace_blob_hash()
3. Basic output formatting
4. CLI command

#### Phase 2: ID Tracing (3-4 hours)

1. Implement trace_entry_id()
2. Implement trace_drop_id()
3. Implement trace_document_id()
4. Unified output format

#### Phase 3: Enhanced Output (2-3 hours)

1. Colored/formatted output
2. History event references
3. Helpful suggestions
4. Tombstone indicators

#### Phase 4: Testing (3-4 hours)

1. E2E tests for each input type
2. Test multiple imports of same content
3. Test missing/deleted files
4. Test tombstone display

**Total effort:** ~12-16 hours

## Trade-offs

**Advantages:**
- Single command for all provenance queries
- Overloaded input (smart detection)
- Complete history visibility
- Useful for debugging imports
- Shows duplicate content
- Links to history events

**Disadvantages:**
- Complex output for content with many imports
- Slow for large warehouses (if searching by hash)
- No filtering options in v1

**Mitigations:**
- Clear section headings
- Summary at top
- Future: `--format json` for programmatic use
- Future: `--limit` to truncate long histories

## Future Enhancements

### JSON Output

```bash
$ dwh trace --format json invoice.pdf
{
  "hash": "abc123...",
  "size": 45200,
  "imports": [...],
  "classifications": [...],
  "locations": [...]
}
```

### Filtering

```bash
$ dwh trace --since 2026-01-01 abc123...
# Only show imports/classifications after date

$ dwh trace --drop d_20260329_120000_abc123 abc123...
# Only show classification from specific drop
```

### Comparison

```bash
$ dwh trace --compare file1.pdf file2.pdf
# Show if files have same content (hash match)
```

### Graph Export

```bash
$ dwh trace --graph abc123... > provenance.dot
# Export provenance graph in DOT format
```

## Decision

Implement `dwh trace <identifier>` command with overloaded input types:
- File path
- Blob hash (64 hex chars)
- Entry ID (e_...)
- Drop ID (d_...)
- Document ID (integer)

Shows complete provenance:
- Import history (all drops with this content)
- Classification history (all document records)
- Current locations in warehouse
- Blob storage location

## References

- ADR-003: Warehouse layout and filesystem structure
- ADR-006: Triage queue and documents table
- ADR-007: Warehouse audit (complementary command)
