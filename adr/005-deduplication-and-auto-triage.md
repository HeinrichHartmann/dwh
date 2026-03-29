# ADR-005: Deduplication and Auto-Triage

**Status:** Proposed
**Date:** 2026-03-29
**Context:** Warehouse registry implementation (ADR-004)

## Problem

Importing files into the warehouse involves significant human/AI classification cost during triage. When re-importing files or importing trees with previously seen content, we waste time re-classifying the same files.

**Key scenarios:**
1. **Identical tree re-import:** User imports exact same directory tree multiple times
2. **Partial then full import:** User imports subtree, then later imports parent tree
3. **Incremental additions:** User adds files to directory and re-imports

**Current behavior:**
- Every import requires full triage, even for known content
- No detection of duplicate drops
- No memory of previous classifications

**Human cost example:**
- First import of 100 files: 2 hours of triage
- Second import of same 100 files: another 2 hours wasted

## Solution

Implement two-level deduplication and auto-triage system.

### 1. Drop-Level Duplicate Detection

**Goal:** Detect when user is re-importing an identical tree.

**Mechanism:**
- Compute tree fingerprint: hash of sorted (relative_path, blob_hash) pairs
- Check against previous drops before import
- Prompt user: "This exact tree was imported on 2024-01-15. Import again?"

**Value:**
- Prevents accidental duplicate drops
- Creates provenance record: "verified unchanged on date X"
- User choice: skip or create verification record

**Schema:**
```sql
ALTER TABLE drops ADD COLUMN tree_fingerprint TEXT;
CREATE INDEX idx_tree_fingerprint ON drops(tree_fingerprint);
```

### 2. Auto-Triage with Staging Workflow

**Goal:** Reduce human classification time for known content.

**New workflow:**
```
_triage/    →  [suggest]  →    _staging/    →  [merge]  →   warehouse root
(manual)       (auto)          (review)         (apply)     (categories)
```

**Commands:**
- `dwh triage checkout` - Extract drop to _triage/ (existing)
- `dwh triage suggest` - Auto-classify known content to _staging/
- `dwh triage merge` - Apply _staging/ to warehouse root

#### Classification Memory

For the main v1 use case, assume that many documents are uniquely determined by
their content. In other words:

```text
blob_hash -> suggested category
```

This fits document types like invoices, statements, receipts, and contracts,
where re-importing the same bytes usually means "this is the same document
again."

Track blob classifications to enable auto-triage:

```sql
CREATE TABLE blob_classifications (
    blob_hash TEXT PRIMARY KEY,
    category TEXT NOT NULL,           -- suggested category (last wins)
    last_seen_name TEXT,              -- display hint only
    first_classified_at TEXT NOT NULL,
    last_classified_at TEXT NOT NULL,
    classification_count INTEGER NOT NULL DEFAULT 1
);
```

**V1 Simplification:** This table uses a simple "last classification wins" strategy.
If the same blob is classified to different categories over time, the most recent
classification becomes the suggestion. This works well for the common case where
identical content represents the same document.

**Important:** `blob_classifications` is a derived suggestion cache, not the
source of truth. The source of truth remains the append-only history of
classification events.

**What v1 Does NOT Address:**

The following cases are explicitly out of scope for v1 and will be addressed in
future versions:

1. **Ambiguous blobs:** When the same content hash should go to different
   categories depending on context (e.g., a generic template file used in multiple
   projects)

2. **Entry-level provenance:** Distinguishing between two imports of the same blob
   with different metadata/context

3. **Conflict detection/resolution:** No UI for resolving conflicting
   classifications

For these cases, a future version will likely use an **editable filing table** - a
table-based classification format that can be explicitly edited to describe the
mapping between files and categories with full context. This would replace or
augment the simple blob → category cache.

#### Matching Strategy

**V1: Blob Identity Match Only**

- If blob hash exists in `blob_classifications`, use the stored category as a
  suggestion
- Works even if filename/path changes
- Content-based deduplication
- Simple rule: last classification wins

**V2+: Entry Path Match (Future)**
- If relative path matches previous entry path from same import root
- Example: `docs/invoice.pdf` always goes to `finance/taxes/`
- Useful for recurring directory structures
- Requires tracking import context alongside classifications

#### Suggest Command Behavior

```bash
$ dwh triage suggest

Analyzing 150 files...

✓ 100 files auto-classified (known content)
  → 50 to finance/taxes/2024/
  → 30 to work/projects/alpha/
  → 20 to personal/photos/

→ 50 files staged in _staging/ for review
→ 50 files remain in _triage/ (need manual classification)

Review staged files and adjust before running:
  dwh triage merge
```

**Algorithm:**
```python
def triage_suggest(triage_dir, staging_dir, conn):
    auto_classified = []
    needs_manual = []

    for file in triage_dir.rglob("*"):
        if not file.is_file():
            continue

        hash = compute_hash(file)

        # Check classification memory
        memory = conn.execute(
            "SELECT category FROM blob_classifications WHERE blob_hash = ?",
            (hash,)
        ).fetchone()

        if memory:
            # Known blob - auto-classify to staging
            category = memory['category']
            target = staging_dir / category / file.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(file, target)
            auto_classified.append((file.name, category))
        else:
            # Unknown blob - leave in triage for manual handling
            needs_manual.append(file)

    return {
        'auto_classified': auto_classified,
        'needs_manual': needs_manual
    }
```

#### Merge Command Behavior

```bash
$ dwh triage merge

Merging _staging/ to warehouse...

✓ Merged 100 files
  finance/taxes/2024/: 50 files
  work/projects/alpha/: 30 files
  personal/photos/: 20 files

Classification records written to history.
_staging/ cleared.
```

**Algorithm:**
```python
def triage_merge(staging_dir, warehouse_root, history_dir, conn):
    classifications = []

    # Collect all files in staging
    for file in staging_dir.rglob("*"):
        if not file.is_file():
            continue

        # Compute category from path relative to staging
        rel_path = file.relative_to(staging_dir)
        category = str(rel_path.parent)

        # Move to warehouse
        target = warehouse_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(file, target)

        # Record classification by blob hash
        hash = compute_hash(target)
        classifications.append({
            'blob_hash': hash,
            'category': category,
            'name': file.name
        })

    # Write classification event to history
    write_classification_event(classifications, history_dir)

    # Update blob classification memory (last wins)
    for c in classifications:
        conn.execute("""
            INSERT INTO blob_classifications
            (blob_hash, category, last_seen_name, first_classified_at,
             last_classified_at, classification_count)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(blob_hash) DO UPDATE SET
                category = excluded.category,
                last_seen_name = excluded.last_seen_name,
                last_classified_at = excluded.last_classified_at,
                classification_count = classification_count + 1
        """, (c['blob_hash'], c['category'], c['name'], now(), now()))

    # Clear staging
    shutil.rmtree(staging_dir)
```

#### Memory Updates

Classification memory is updated during:

1. **Manual triage sync** (existing workflow):
   - User moves files in `_triage/` to categories
   - On sync, record classifications and update memory

2. **Merge command** (new workflow):
   - Files in `_staging/` merged to warehouse
   - Record classifications and update memory

Both workflows update `blob_classifications` table using the same "last wins"
strategy. The classification events written to history track the full provenance
as before.

## Workflow Examples

### Example 1: First Import

```bash
$ dwh drop import ~/Downloads/invoices/
Imported 10 files
Drop ID: d_20260329_120000_abc123

$ dwh triage checkout
Checked out 10 files to _triage/

$ dwh triage suggest
Analyzing 10 files...
→ 10 files remain in _triage/ (need manual classification)

# User manually organizes
$ mv _triage/invoice1.pdf finance/taxes/2024/
$ mv _triage/invoice2.pdf finance/taxes/2024/
# ... classify all ...

$ dwh triage sync
✓ Classified 10 files
```

### Example 2: Re-import Same Files

```bash
$ dwh drop import ~/Downloads/invoices/

⚠ This exact tree was imported before:
  Drop: d_20260329_120000_abc123
  Date: 2026-03-29 12:00:00
  Message: "Import invoices"

Import again? (Creates record that nothing changed) [y/N]: y

Imported 10 files
Drop ID: d_20260329_150000_def456

$ dwh triage checkout
Checked out 10 files to _triage/

$ dwh triage suggest
Analyzing 10 files...

✓ 10 files auto-classified (known content)
  → 10 to finance/taxes/2024/

→ 10 files staged in _staging/ for review

# User reviews staging
$ ls _staging/finance/taxes/2024/
invoice1.pdf  invoice2.pdf  ...

# Looks good, merge
$ dwh triage merge
✓ Merged 10 files

# Zero manual classification time!
```

### Example 3: Partial Then Full Import

```bash
# Import subtree
$ dwh drop import ~/project/docs/
Imported 10 files

$ dwh triage checkout
$ dwh triage suggest
→ 10 files need manual classification

# User classifies
$ mv _triage/* work/project-alpha/docs/
$ dwh triage sync
✓ Classified 10 files

# Later: Import entire project
$ dwh drop import ~/project/
Imported 50 files

$ dwh triage checkout
$ dwh triage suggest
Analyzing 50 files...

✓ 10 files auto-classified (known content)
  → 10 to work/project-alpha/docs/

→ 10 files staged in _staging/ for review
→ 40 files remain in _triage/ (need manual classification)

# User only triages 40 new files
# Then merges staged + manual classifications
```

## Implementation Plan

### Phase 1: Drop Duplicate Detection

1. Add `tree_fingerprint` column to drops table
2. Compute fingerprint during import
3. Check for duplicates, prompt user
4. **Estimated effort:** 2-3 hours

### Phase 2: Blob Classification Memory

1. Create `blob_classifications` table
2. Update `triage sync` to populate memory
3. **Estimated effort:** 3-4 hours

### Phase 3: Staging Workflow

1. Implement `dwh triage suggest` command
   - Create `_staging/` directory
   - Move known blobs from `_triage/` to `_staging/`
   - Report auto-classified vs needs-manual counts

2. Implement `dwh triage merge` command
   - Move files from `_staging/` to warehouse root
   - Write classification events to history
   - Update blob classification memory
   - Clear `_staging/`

3. **Estimated effort:** 5-6 hours

### Phase 4: Testing & Documentation

1. E2E tests for duplicate detection
2. E2E tests for suggest/merge workflow
3. Update README with new workflow
4. **Estimated effort:** 4-5 hours

**Total effort:** ~15-18 hours

## Future Enhancements

### Entry Path Matching

Track entry paths and suggest based on path patterns:

```sql
CREATE TABLE entry_path_classifications (
    entry_path TEXT,              -- "docs/invoice.pdf"
    import_root_pattern TEXT,     -- "~/Downloads/%"
    category TEXT,
    last_seen_at TEXT,
    PRIMARY KEY (entry_path, import_root_pattern)
);
```

**Use case:** If importing from `~/Downloads/invoices/` and `invoice.pdf` always goes to `finance/taxes/`, suggest it.

### Directory Context Inference

If 80%+ of files in a directory auto-classify to same category, suggest it for remaining unknowns.

### Conflict Resolution

If blob classified to different categories over time, mark it as conflicted and
disable auto-triage for that blob. The user can then resolve it manually:
```
⚠ invoice.pdf has been classified differently:
  2024-01-15: finance/taxes/2024/
  2024-03-20: archive/old-invoices/

Choose destination:
  1) finance/taxes/2024/ (most recent)
  2) archive/old-invoices/
  3) Enter new path
```

## Trade-offs

**Advantages:**
- Dramatically reduces triage time for re-imports
- Supports incremental and partial imports naturally
- Staging area allows review before committing
- Drop duplicate detection prevents accidents
- Content-based memory works even with filename changes
- Keeps the main case simple: identical content usually implies same document

**Disadvantages:**
- More complexity in triage workflow
- Additional `_staging/` directory to understand
- Memory table requires maintenance
- V1 doesn't handle ambiguous blobs (same content, different intended categories)

**Mitigations:**
- Staging area allows review before merge
- User can move files between `_staging/` and `_triage/` to adjust
- Classification memory can be cleared/reset if needed
- Clear command names: suggest (tentative) → merge (commit)
- Future versions will add editable filing table for complex cases

## Decision

Implement drop duplicate detection and auto-triage with staging workflow using the commands:
- `dwh triage suggest` - Auto-classify to `_staging/`
- `dwh triage merge` - Apply `_staging/` to warehouse

Start with blob identity matching only. Entry path matching is future work.

Treat blob classification memory as a derived suggestion cache. Final
classification history continues to be recorded by `entry_id`, and blobs with
conflicting prior classifications are excluded from auto-triage.

## References

- ADR-003: Warehouse layout and filesystem structure
- ADR-004: Warehouse registry and out-of-tree operations
