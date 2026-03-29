# Triage Flow Implementation Review

**Reviewer:** Claude Sonnet 4.5
**Date:** 2026-03-29
**Scope:** Triage workflow implementation (cli.py, triage.py, db.py, tests/e2e/test_triage.py)

## Overview

This review examines the triage workflow implementation - the two-phase process for classifying entries from a drop into the document tree.

**Architecture:** Two-phase operation with database state tracking:
1. `dwh triage checkout [drop_id]` - Copy files from history to triage/
2. User organizes files by moving them to documents/
3. `dwh triage sync` - Match moved files by hash, create classifications

## Implementation Analysis

### Triage State Management

**Design:** Single-row table ensures only one triage at a time

```sql
CREATE TABLE triage_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    drop_id TEXT NOT NULL,
    checked_out_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Implementation:** triage.py:78-83
- Clear old state: `DELETE FROM triage_state`
- Insert new state: `INSERT INTO triage_state (id, drop_id) VALUES (1, ?)`
- Atomic: Either triaging or not

**Assessment:** ✅ Clean design. The `CHECK(id = 1)` constraint enforces single-row invariant.

### Phase 1: Checkout (triage.py:36-85)

**Flow:**

1. **Select drop** (lines 44-55):
   ```python
   if drop_id:
       d = drop_module.drop_inspect(drop_id, conn)
   else:
       drops = drop_module.drop_list(conn)
       d = drop_module.drop_inspect(drops[0].id, conn)  # Latest
   ```
   - Explicit drop_id → use that
   - No drop_id → use most recent
   - TODO comment: "Skip drops where all entries are already documents"

2. **Clear workspace** (lines 58-60):
   ```python
   if triage_dir.exists():
       shutil.rmtree(triage_dir)
   triage_dir.mkdir(parents=True)
   ```
   - Removes existing triage/ completely
   - Creates fresh directory

3. **Copy from history** (lines 62-75):
   ```python
   drop_dir = history_module.find_drop_in_history(history_dir, d.id)
   tree_dir = drop_dir / "tree"

   for file in tree_dir.rglob("*"):
       if file.is_file():
           relative_path = file.relative_to(tree_dir)
           dest = triage_dir / relative_path
           dest.parent.mkdir(parents=True, exist_ok=True)
           shutil.copy2(file, dest)  # Full copy with metadata
   ```
   - Source: `.dwh/history/NNN_drop_.../tree/`
   - Preserves directory structure
   - Uses `shutil.copy2` (preserves timestamps)

4. **Record state** (lines 78-83):
   - Clears old triage state
   - Inserts new row with drop_id
   - Commits to database

**Assessment:**
- ✅ History-first: Reads from history tree, not database
- ✅ Clean slate: Clears triage/ completely
- ⚠️ Performance: Full copy could be slow for large drops (symlinks alternative?)
- ⚠️ Selection logic: TODO indicates incomplete feature (skip classified drops)

### Phase 2: Sync (triage.py:88-222)

**Flow:**

1. **Verify state** (lines 99-103):
   ```python
   state_row = conn.execute("SELECT drop_id FROM triage_state WHERE id = 1").fetchone()
   if not state_row:
       raise NoTriageInProgressError()
   ```

2. **Build lookup** (lines 105-117):
   ```python
   entries = conn.execute("SELECT * FROM entries WHERE drop_id = ?", (triaging_drop_id,)).fetchall()

   entries_by_hash = {}
   for entry in entries:
       h = entry["blob_hash"]
       if h not in entries_by_hash:
           entries_by_hash[h] = []
       entries_by_hash[h].append(entry)
   ```
   - Fetches all entries from triaged drop
   - Groups by blob_hash (for deduplication detection)

3. **Scan filesystem** (lines 119-135):
   ```python
   # Remaining files in triage/
   for file in triage_dir.rglob("*"):
       if file.is_file():
           file_hash = drop_module.compute_hash(file)
           triage_files[str(rel_path)] = file_hash

   # New files in documents/
   for file in documents_dir.rglob("*"):
       if file.is_file():
           file_hash = drop_module.compute_hash(file)
           document_files[str(rel_path)] = (file_hash, file)
   ```
   - Computes SHA-256 hash for every file
   - Builds two maps: triage_files (remaining), document_files (moved)

4. **Match algorithm** (lines 138-173):
   ```python
   for doc_path_str, (doc_hash, doc_file) in document_files.items():
       # Skip if already classified
       existing = conn.execute(
           "SELECT id FROM documents WHERE entry_id IN (SELECT id FROM entries WHERE blob_hash = ?)",
           (doc_hash,)
       ).fetchone()
       if existing:
           continue

       # Find matching entries by hash
       matching_entries = entries_by_hash.get(doc_hash, [])

       if len(matching_entries) == 0:
           continue  # Not from this drop
       elif len(matching_entries) == 1:
           # Unambiguous match - create classification
           entry = matching_entries[0]
           category = str(doc_path.parent) if doc_path.parent != Path(".") else ""
           name = doc_path.name
           matches.append(TriageMatch(...))
       else:
           # Ambiguous - multiple entries with same hash
           ambiguous.append(doc_path_str)
   ```

   **Matching logic:**
   - **0 matches**: File from different drop → skip silently
   - **1 match**: Unambiguous → classify
   - **2+ matches**: Same content in multiple entries → ambiguous, skip

5. **Create classification record** (lines 175-209):
   ```python
   if matches:
       seq_num = history_module.get_next_history_number(history_dir)
       classify_file = history_dir / f"{seq_num:03d}_classify.json"

       for match in matches:
           cursor = conn.execute(
               "INSERT INTO documents (entry_id, name, category) VALUES (?, ?, ?)",
               (match.entry_id, match.name, match.category)
           )
           document_id = cursor.lastrowid

           classifications.append({
               "entry_id": match.entry_id,
               "document_id": document_id,
               "category": match.category,
               "name": match.name
           })

       classification_record = {
           "type": "classify",
           "created_at": datetime.now(timezone.utc).isoformat(),
           "actor": getpass.getuser(),
           "message": "Triage sync",
           "classifications": classifications
       }

       with open(classify_file, "w") as f:
           json.dump(classification_record, f, indent=2)
   ```
   - Appends to history: `NNN_classify.json`
   - Inserts into database: `documents` table
   - Both operations together (history + DB)

6. **Cleanup** (lines 211-216):
   ```python
   conn.execute("DELETE FROM triage_state")
   conn.commit()

   if triage_dir.exists():
       shutil.rmtree(triage_dir)
   ```
   - Clears triage state
   - Removes triage/ directory

**Assessment:**
- ✅ Content-based matching: Handles renames correctly
- ✅ Duplicate detection: Identifies same content in multiple entries
- ✅ Skip already-classified: Prevents re-classification
- ✅ History-first: Writes classification to history, then DB
- ⚠️ Silent skip: No indication which files came from other drops
- ⚠️ Lost files: Files left in triage/ are deleted on next checkout

### Category Extraction

```python
# From: documents/finance/taxes/2024/invoice.pdf
category = str(doc_path.parent)  # → "finance/taxes/2024"
name = doc_path.name              # → "invoice.pdf"
```

**Examples:**
- `documents/invoice.pdf` → category: `""`, name: `"invoice.pdf"`
- `documents/finance/invoice.pdf` → category: `"finance"`, name: `"invoice.pdf"`
- `documents/work/clients/acme/contract.pdf` → category: `"work/clients/acme"`, name: `"contract.pdf"`

**Assessment:** ✅ Simple, intuitive, matches design expectations

## Test Coverage (tests/e2e/test_triage.py)

**Test Classes:**

1. **TestTriageCheckout** (6 tests):
   - Latest drop checkout
   - Specific drop checkout
   - Directory creation
   - Clear existing triage
   - Failure without drops

2. **TestTriageSync** (7 tests):
   - Basic classification
   - Classification record creation
   - Database updates
   - Directory cleanup
   - Failure without checkout
   - Nested categories
   - Partial classification (files left in triage)

3. **TestTriageWorkflow** (4 tests):
   - Complete workflow (import → checkout → organize → sync)
   - Multiple triage cycles
   - Independent operations

**Total:** 17 tests, 343 lines

**Coverage assessment:**
- ✅ Happy path: Full workflow tested
- ✅ Error cases: No drops, no checkout, etc.
- ✅ Edge cases: Nested categories, partial classification
- ✅ State management: Multiple cycles work independently
- ❌ Missing: Ambiguous file tests (duplicate content)
- ❌ Missing: Cross-drop file handling
- ❌ Missing: Concurrent triage attempts (should fail)

## Alignment with Design

| Design Requirement | Implementation | Location | Status |
|--------------------|----------------|----------|--------|
| Checkout from history tree | ✅ Yes | triage.py:67-75 | Matches |
| Match by content hash | ✅ Yes | triage.py:124,133 | Matches |
| Ambiguity detection | ✅ Yes | triage.py:172-173 | Matches |
| Classification in history | ✅ Yes | triage.py:179,206 | Matches |
| Single triage at a time | ✅ Yes | db.py:46-50 | Matches |
| Category from path | ✅ Yes | triage.py:161 | Matches |
| Skip classified drops | ⚠️ TODO | triage.py:54 | **Incomplete** |
| `dwh file` command | ❌ No | - | **Missing** |

## Critical Analysis

### ✅ Strengths

1. **Content-based matching is correct**
   - Uses SHA-256 hash, not filename
   - Renames work: `file1.txt` → `invoice.pdf` matched by content
   - Robust against user reorganization

2. **History-first approach**
   - Checkout reads from history tree, not database
   - Export and triage both use history directly
   - Consistent with "history is source of truth" design

3. **Clean state management**
   - Single-row triage_state table prevents concurrency
   - Clear atomic operations: checkout or sync, not partial
   - Database constraint enforces invariant

4. **Ambiguity handling**
   - Detects duplicate content within drop
   - Reports ambiguous files to user
   - Doesn't guess or create invalid classifications

5. **Comprehensive tests**
   - 17 test cases covering common workflows
   - E2E approach verifies actual behavior
   - Tests read history files and database to verify results

### ⚠️ Issues and Concerns

**1. Missing `dwh file` Command**

**Severity:** Medium (blocks ambiguous file resolution)

The design (DESIGN.md:300) expects:
```bash
dwh file <entry_id> --category finance/taxes --name receipt.pdf
```

This command doesn't exist. Users cannot resolve ambiguous cases.

**Current behavior:**
1. Drop has `invoice.pdf` and `copy/invoice.pdf` (same content)
2. User moves one to `documents/finance/`
3. Sync detects ambiguity, skips file, reports: "⚠ Ambiguous: invoice.pdf"
4. User has no way to resolve this

**Recommendation:** Implement `dwh file` command or change sync to prompt user for resolution.

---

**2. Latest Drop Selection Incomplete**

**Severity:** Low (UX issue)

Line 54 has TODO: "Skip drops where all entries are already documents"

**Current behavior:**
- `dwh triage checkout` always uses most recent drop
- If that drop is fully classified, user gets empty triage or errors

**Expected behavior:**
- Skip drops where all entries have document records
- Select latest *unprocessed* drop

**Recommendation:** Implement the TODO before v1 release.

---

**3. Files Left in Triage Are Lost**

**Severity:** Medium (data loss risk)

**Current behavior:**
1. User runs `dwh triage checkout`
2. User moves 2 of 4 files to documents/
3. User runs `dwh triage sync` → "Classified 2 files, Skipped 2 files"
4. User runs `dwh triage checkout` for next drop → previous triage/ is deleted

The 2 unclassified files are gone.

**Expected behavior options:**
- Option A: Sync fails if files remain in triage/ (force complete classification)
- Option B: Sync warns and requires `--force` flag to proceed
- Option C: Keep unclassified files in triage/ across sessions

**Current design is lossy.** User has no way to recover unclassified files.

**Recommendation:** At minimum, fail sync if triage/ is not empty, require explicit `--skip-remaining` flag.

---

**4. Silent Skip for Cross-Drop Files**

**Severity:** Low (potential confusion)

Lines 155-157:
```python
if len(matching_entries) == 0:
    continue  # File in documents/ but not from this drop - skip
```

**Scenario:**
1. Drop A imported with `invoice.pdf`
2. User classifies to `documents/finance/`
3. Drop B imported with `receipt.pdf`
4. User runs `triage checkout` for Drop B
5. User moves `receipt.pdf` to `documents/finance/`

Sync will classify `receipt.pdf` but silently skip `invoice.pdf` (already there from Drop A).

**This is correct behavior,** but might confuse users who expect all files in documents/ to be reported.

**Recommendation:** No code change needed, but document this behavior in help text.

---

**5. Full Copy Performance**

**Severity:** Low (performance for large drops)

Line 75: `shutil.copy2(file, dest)`

**Current behavior:**
- Checkout copies all files from history to triage/
- For 1000 files × 1MB = 1GB copied

**Alternative:** Use symlinks
```python
dest.symlink_to(file)  # Instead of shutil.copy2
```

**Trade-offs:**
- Symlinks: Fast, no space, but breaks if history changes
- Copy: Slow, uses space, but safe

**Current design is safe.** History is immutable, so symlinks would work, but copies are more defensive.

**Recommendation:** Keep current behavior for v1. Optimize only if performance becomes an issue.

---

**6. No Test for Ambiguous Files**

**Severity:** Low (test coverage gap)

Tests cover many scenarios but don't test ambiguous file detection.

**Missing test:**
```python
def test_triage_sync_ambiguous_files(runner, tmp_warehouse, tmp_path):
    """Sync detects and reports ambiguous files (duplicate content)."""
    # Create drop with duplicate content
    file1 = tmp_path / "original.txt"
    file1.write_text("same content")

    file2 = tmp_path / "copy.txt"
    file2.write_text("same content")

    run_cli(runner, ["drop", "import", "-m", "Test", str(file1), str(file2)])
    run_cli(runner, ["triage", "checkout"])

    # Move one file to documents
    documents_dir = tmp_warehouse / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)

    triage_dir = tmp_warehouse / "triage"
    (triage_dir / "original.txt").rename(documents_dir / "doc.txt")

    # Sync should detect ambiguity
    result = run_cli(runner, ["triage", "sync"])

    assert "ambiguous" in result.output.lower()
    assert "doc.txt" in result.output
```

**Recommendation:** Add this test to verify ambiguity detection works.

## Algorithm Deep Dive: Matching Logic

The matching algorithm (triage.py:138-173) is the core of triage sync.

**Input:**
- `entries_by_hash`: Map of blob_hash → list[entry] from triaged drop
- `document_files`: Map of path → (hash, file) from documents/ scan

**Output:**
- `matches`: List of unambiguous classifications
- `ambiguous`: List of paths with multiple matches

**Algorithm:**
```python
for each file F in documents/:
    H = hash(F)

    # Skip if already a document
    if exists(document with blob_hash = H):
        continue

    # Find entries with matching hash
    E = entries_by_hash[H]

    if len(E) == 0:
        # File not from this drop, ignore
        pass
    elif len(E) == 1:
        # Unambiguous match
        matches.append(E[0] → F)
    else:
        # Multiple entries with same content
        ambiguous.append(F)
```

**Example 1: Simple rename**
- Drop: `tree/invoice.pdf` (hash: `abc123`)
- User: `mv triage/invoice.pdf documents/finance/2024_invoice.pdf`
- Match: `entry(hash=abc123)` → `documents/finance/2024_invoice.pdf`
- Result: Classified as category=`"finance"`, name=`"2024_invoice.pdf"`

**Example 2: Duplicate content in drop**
- Drop: `tree/doc.pdf` (hash: `xyz789`), `tree/copy/doc.pdf` (hash: `xyz789`)
- User: `mv triage/doc.pdf documents/archive.pdf`
- Match: 2 entries with hash `xyz789`
- Result: Ambiguous, skipped

**Example 3: Already classified**
- Drop A: `invoice.pdf` (hash: `abc123`) → already document
- Drop B: `receipt.pdf` (hash: `def456`)
- User: `mv triage/receipt.pdf documents/`
- Match: `invoice.pdf` skipped (already document), `receipt.pdf` matched
- Result: Only `receipt.pdf` classified

**Assessment:** Algorithm is correct and handles edge cases properly.

## Recommendations

### Must Fix (Before v1 Release)

1. **Implement `dwh file` command** - Users need a way to resolve ambiguous files
   ```bash
   dwh file <entry_id> --category finance --name invoice.pdf
   ```

2. **Prevent data loss** - Fail sync if files remain in triage/
   ```python
   if triage_files:
       if not force:
           raise TriageError(f"{len(triage_files)} files remain in triage/. Use --force to skip.")
   ```

3. **Implement skip-classified logic** - Complete the TODO at line 54
   ```python
   # Get latest unclassified drop
   for drop in drops:
       classified_count = conn.execute(
           "SELECT COUNT(*) FROM documents d JOIN entries e ON d.entry_id = e.id WHERE e.drop_id = ?",
           (drop.id,)
       ).fetchone()[0]

       entry_count = conn.execute("SELECT COUNT(*) FROM entries WHERE drop_id = ?", (drop.id,)).fetchone()[0]

       if classified_count < entry_count:
           return drop  # Found unprocessed drop
   ```

### Should Add (Before Calling Complete)

4. **Add ambiguous file test** - Verify detection works
5. **Document silent skip behavior** - Help text should explain cross-drop files are ignored

### Nice to Have

6. **Consider symlinks** - For large drops, could improve performance
7. **Add `dwh triage status`** - Show current triage state
8. **Add `dwh triage abort`** - Clear triage without syncing

## Summary

**Overall Assessment:** ✅ **Solid implementation with minor gaps**

The triage workflow is well-designed and correctly implements the event-sourcing model:
- Content-based matching handles renames
- History-first approach is consistent
- Ambiguity detection works
- State management is clean
- Test coverage is good

**Main gaps:**
1. Missing `dwh file` command (blocks ambiguous resolution)
2. Files left in triage/ are lost (data loss risk)
3. Skip-classified logic incomplete (UX issue)

**Code quality:** High. Clean separation, good error handling, comprehensive tests.

**Recommendation:** Fix issues #1 and #2 before v1 release. The triage workflow is otherwise production-ready.

---

## Appendix: Database State Transitions

### State: No Triage

```sql
SELECT * FROM triage_state;
-- (empty)
```

### State: After Checkout

```sql
SELECT * FROM triage_state;
-- id | drop_id                        | checked_out_at
-- 1  | d_20260329_143211_abc123       | 2026-03-29 14:32:11
```

**Filesystem:**
```
triage/
├── invoice.pdf
├── receipt.pdf
└── folder/
    └── nested.txt
```

### State: After User Organizes

```sql
SELECT * FROM triage_state;
-- (same as above)
```

**Filesystem:**
```
triage/
├── receipt.pdf           # Not moved
└── folder/
    └── nested.txt        # Not moved

documents/
└── finance/
    └── invoice.pdf       # Moved here
```

### State: After Sync

```sql
SELECT * FROM triage_state;
-- (empty)

SELECT * FROM documents;
-- id | entry_id          | category | name        | created_at
-- 1  | e_f4bea3104f381c7e | finance  | invoice.pdf | 2026-03-29 14:35:00
```

**Filesystem:**
```
triage/
-- (deleted)

documents/
└── finance/
    └── invoice.pdf

.dwh/history/
├── 001_drop_d_20260329_143211_abc123/
│   ├── receipt.json
│   └── tree/
│       ├── invoice.pdf
│       ├── receipt.pdf
│       └── folder/nested.txt
└── 002_classify.json     # Created by sync
```

**002_classify.json:**
```json
{
  "type": "classify",
  "created_at": "2026-03-29T14:35:00Z",
  "actor": "hhartmann",
  "message": "Triage sync",
  "classifications": [
    {
      "entry_id": "e_f4bea3104f381c7e",
      "document_id": 1,
      "category": "finance",
      "name": "invoice.pdf"
    }
  ]
}
```
