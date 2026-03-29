# ADR-006: Triage Queue and Tombstone Classification

**Status:** Proposed
**Date:** 2026-03-29
**Context:** Auto-triage design (ADR-005), current partial triage behavior

## Problem

The current triage workflow has several usability issues:

1. **No queue management**: User must manually track which drops need triage
2. **No safe resume**: `dwh triage checkout` without args picks "latest drop" but doesn't check if user wants to continue previous work
3. **No way to exclude files**: Not all imported entries belong in warehouse (`.DS_Store`, `.git*`, temp files)
4. **Unclear completion**: When is a drop "done"? What if some files should never be classified?

**Current behavior problems:**
- Accidentally checking out new drop loses uncommitted triage work
- No visibility into triage backlog
- Files user wants to exclude must be manually deleted and are forgotten

## Solution

Implement **triage queue model** with **tombstone classification** for excluded entries.

### Core Concepts

#### 1. Triage Queue (LIFO)

Drops form a queue ordered newest-first (LIFO). User processes queue by:
1. Checkout next untriaged drop
2. Classify entries (file or exclude)
3. Sync classifications
4. Repeat

**Queue states:**
- **Complete**: All entries have documents (filed or excluded)
- **In progress**: Some entries have documents, triage_state exists
- **Pending**: No entries have documents yet

#### 2. Entry Classification Outcomes

Every entry from a drop must be classified as one of:

1. **Filed**: Entry → Document with category → File appears in warehouse tree
2. **Excluded**: Entry → Document with empty category `''` (tombstone) → File does not appear in tree
3. **Unclassified**: Entry without document → Triage incomplete

**Tombstone marker:**
```sql
-- Excluded entry example
INSERT INTO documents (entry_id, name, category) VALUES
  ('e_20260329_120000_abc123_001', '.DS_Store', '');
  -- Empty string category = tombstone
```

**Benefits:**
- All entries get resolved (no "forgotten" files)
- Content stays in blob storage (always reconstructable)
- Query: "Show me what I've excluded" → `SELECT * FROM documents WHERE category = ''`
- Re-triage can change tombstone to filed classification

#### 3. Triage Completion

**Drop is complete when:**
```sql
-- All entries have documents
SELECT COUNT(*) FROM entries WHERE drop_id = ?
  =
SELECT COUNT(*) FROM documents d JOIN entries e ON d.entry_id = e.id WHERE e.drop_id = ?
```

This includes both filed entries (category != '') and excluded entries (category = '').

### Commands

#### `dwh triage checkout` (no args)

**Behavior:**
1. If triage in progress → Resume (show status)
2. If no triage in progress → Checkout newest incomplete drop (LIFO)
3. If all drops complete → Show "Queue clear!"

**Examples:**

**Start new triage:**
```bash
$ dwh triage checkout

Checking out: d_20260329_120000_abc123
Message: "Import invoices"
Created: 2026-03-29 12:00:00

Checked out 10 files to _triage/
```

**Resume in-progress:**
```bash
$ dwh triage checkout

Resuming: d_20260329_120000_abc123
5 files remain in _triage/ (5 already classified)
```

**Queue clear:**
```bash
$ dwh triage checkout

✓ All drops triaged! Queue clear.
```

#### `dwh triage checkout <drop-id>`

Explicitly checkout a specific drop (bypass queue).

**Safety check:**
```bash
$ dwh triage checkout d_20260329_110000_xyz

⚠ Triage in progress: d_20260329_120000_abc123
  5 files remain in _triage/

Switch to d_20260329_110000_xyz? This will discard uncommitted work.
Continue? [y/N]
```

#### `dwh triage checkout --force`

Restart current drop from scratch (discard partial classifications).

```bash
$ dwh triage checkout --force

⚠ Discarding triage progress for d_20260329_120000_abc123
  5/10 entries already classified

Re-checking out 10 files to _triage/
```

**Note:** This doesn't delete existing documents, just allows re-classifying from clean slate.

#### `dwh triage status`

Show queue overview and current progress.

```bash
$ dwh triage status

Triage Queue (5 drops):

✓ d_20260329_100000_aaa  Old files          (10/10 complete)
✓ d_20260329_110000_bbb  Documents          (5/5 complete)
→ d_20260329_120000_ccc  Invoices           (5/10 classified) ← IN PROGRESS
⏳ d_20260329_130000_ddd  Downloads          (0/8 pending)
⏳ d_20260329_140000_eee  Attachments        (0/3 pending)

Summary:
- Complete: 2 drops (15 entries)
- In progress: 1 drop (5/10 entries)
- Pending: 2 drops (11 entries)
```

#### `dwh triage sync`

Existing command, enhanced to handle excluded files.

**Algorithm:**
```python
def triage_sync(warehouse_root, triage_dir, history_dir, conn):
    # Get entries from current drop
    entries = get_entries_for_current_drop(conn)

    # Match files moved to warehouse (filed)
    filed_matches = match_warehouse_files(warehouse_root, entries)

    # Find deleted files (excluded/tombstoned)
    excluded_entries = []
    for entry in entries:
        already_classified = check_document_exists(entry['id'], conn)
        if already_classified:
            continue

        in_warehouse = entry['id'] in filed_entry_ids
        in_triage = file_exists_in_triage(entry, triage_dir)

        if not in_warehouse and not in_triage:
            # File was deleted from triage = user excluded it
            excluded_entries.append(entry)

    # Create documents for filed entries
    for match in filed_matches:
        conn.execute("""
            INSERT INTO documents (entry_id, name, category)
            VALUES (?, ?, ?)
        """, (match.entry_id, match.name, match.category))

    # Create tombstone documents for excluded entries
    for entry in excluded_entries:
        conn.execute("""
            INSERT INTO documents (entry_id, name, category)
            VALUES (?, ?, '')
        """, (entry['id'], entry['filename']))

    # Write classification event to history
    write_classification_event(filed_matches + excluded_entries, history_dir)

    # Check if drop is complete
    remaining_files = count_files_in_triage(triage_dir)
    if remaining_files == 0:
        # All entries resolved, clear triage state
        conn.execute("DELETE FROM triage_state")
        shutil.rmtree(triage_dir)
```

### Workflow Examples

#### Example 1: Normal Triage with Exclusions

```bash
$ dwh drop import ~/Downloads/project/
Imported 10 files

$ dwh triage checkout
Checking out: d_20260329_120000_abc123
Checked out 10 files to _triage/

$ ls _triage/
README.md  invoice.pdf  .DS_Store  .gitignore  notes.txt

# User organizes files
$ mv _triage/invoice.pdf finance/taxes/2024/
$ mv _triage/README.md documentation/
$ mv _triage/notes.txt work/project-alpha/

# User excludes system files
$ rm _triage/.DS_Store
$ rm _triage/.gitignore

# Triage incomplete, leave remaining for later
$ dwh triage sync
✓ Filed: 3 entries
✓ Excluded: 2 entries
→ 5 entries remain in _triage/

# Resume later
$ dwh triage checkout
Resuming: d_20260329_120000_abc123
5 files remain in _triage/

# Complete triage
$ mv _triage/* archive/old/
$ dwh triage sync
✓ Filed: 5 entries
Drop d_20260329_120000_abc123 complete!
```

#### Example 2: Queue Processing

```bash
$ dwh drop import ~/inbox/batch1/
$ dwh drop import ~/inbox/batch2/
$ dwh drop import ~/inbox/batch3/

$ dwh triage status
Triage Queue (3 drops):
⏳ d_..._batch3  (0/10 pending)  ← newest (LIFO)
⏳ d_..._batch2  (0/5 pending)
⏳ d_..._batch1  (0/8 pending)

$ dwh triage checkout  # Starts with batch3 (newest)
Checking out: d_..._batch3

# After completing batch3
$ dwh triage checkout  # Auto-advances to batch2
Checking out: d_..._batch2
```

#### Example 3: Redo Triage

```bash
$ dwh triage checkout
Resuming: d_20260329_120000_abc123
5/10 entries classified

# User realizes they want to reclassify from scratch
$ dwh triage checkout --force
⚠ Discarding triage progress (5/10 classified)
Re-checking out 10 files to _triage/

# Note: Old classifications remain in documents table
# New classifications will create duplicate document records
# This is append-only - history shows both attempts
```

### Schema Changes

**No schema changes required!**

Existing schema already supports this:
- `triage_state` table tracks in-progress drop
- `documents` table with `category = ''` represents tombstones
- Drop completion derived by counting entries vs documents

### Implementation Plan

#### Phase 1: Tombstone Support (2-3 hours)

1. Update `triage_sync` to detect deleted files
2. Create documents with empty category for deleted files
3. Add tests for exclusion workflow

#### Phase 2: Queue Logic (3-4 hours)

1. Implement `get_drop_triage_state(drop_id)` helper
2. Implement `get_next_untriaged_drop()` (LIFO order)
3. Update `dwh triage checkout` to use queue by default
4. Add safety check for in-progress work

#### Phase 3: Status Command (2-3 hours)

1. Implement `dwh triage status` command
2. Show queue with completion indicators
3. Add summary statistics

#### Phase 4: Force/Restart (1-2 hours)

1. Add `--force` flag to checkout
2. Implement restart logic (clear triage, re-copy files)

#### Phase 5: Testing & Documentation (3-4 hours)

1. E2E tests for queue workflow
2. E2E tests for tombstone classification
3. E2E tests for resume vs force
4. Update README with queue model

**Total effort:** ~12-16 hours

## Trade-offs

**Advantages:**
- Clear mental model: queue of drops to process
- Resume-by-default prevents accidental data loss
- Tombstones solve "what about files I don't want?" problem
- Content always preserved in blobs (can reconstruct/re-triage)
- LIFO matches "process recent stuff first" workflow
- No schema changes needed

**Disadvantages:**
- Tombstone semantics may be unclear (empty string as marker)
- No bulk exclude commands (users must use shell: `find . -name '.DS_Store' -delete`)
- No resurrection in v1 (must redo entire drop to un-tombstone)
- More complex completion logic (count match instead of empty triage dir)

**Mitigations:**
- Document tombstone pattern clearly
- Provide common exclusion recipes (`.DS_Store`, `.git*`) in docs
- Future: Add `dwh triage restore` for un-tombstoning
- Clear status command shows exactly what's classified/excluded/pending

## Future Enhancements

### Pattern-Based Auto-Exclusion

Allow users to configure exclusion patterns:
```toml
# .dwh/config.toml
[triage]
auto_exclude = [
  ".DS_Store",
  ".git*",
  "Thumbs.db",
  "*.tmp"
]
```

Files matching patterns auto-tombstoned during checkout.

### Resurrection

```bash
$ dwh triage restore <entry-id>
# Delete tombstone document, file reappears in _triage/
```

### Bulk Operations

```bash
$ dwh triage list-excluded
# Show all tombstoned entries

$ dwh triage restore --pattern ".git*"
# Un-tombstone all matching entries
```

### Smart Queue

Skip drops that are entirely duplicates (all blobs already classified):
```bash
$ dwh triage checkout
Skipping d_..._abc: all files already classified
Checking out: d_..._def
```

## Decision

Implement triage queue model with:
- LIFO queue (newest drop first)
- Resume-by-default behavior
- Tombstone classification using empty category `''`
- Explicit `--force` to restart from scratch

Users exclude files by deleting from `_triage/`. Sync creates tombstone documents for deleted entries.

## References

- ADR-003: Warehouse layout and filesystem structure
- ADR-005: Deduplication and auto-triage
