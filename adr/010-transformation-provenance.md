# ADR-010: Transformation Provenance

**Status:** Proposed
**Date:** 2026-03-29
**Context:** Content-addressed blob storage (ADR-009), need for derived content tracking

## Problem

Users create derived content from existing warehouse documents:
- Merge multiple PDFs into one
- OCR scanned documents
- Extract pages from PDFs
- Convert formats (Word → PDF)
- AI-generated summaries
- Any external tool/script processing

**Current situation:**
- No way to track "this output came from these inputs"
- Derived content imported as regular drop (no provenance link)
- Can't answer: "What was this document created from?"
- Can't answer: "What documents were derived from this source?"

**Need:** Record transformations as first-class history events with input/output provenance.

## Solution

Implement **container-to-container transformations** with working directories.

### Core Concept

```
Query/Selection → _input/      ← Read-only view of inputs
                  _output/     ← User writes transformation results
                  _artifacts/  ← Optional: scripts, logs, notes
                      ↓
                  Output Drop + Artifacts Drop + Provenance Record
```

**Key principle:** We don't operate on individual files. We operate on **containers** (sets of files defined by query or selection).

### Drop Visibility

Drops have a `visibility` attribute:

```sql
CREATE TABLE drops (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TIMESTAMP,
    visibility TEXT DEFAULT 'visible'  -- 'visible' | 'hidden'
);
```

**Semantics:**
- `visible` → Appears in triage queue, needs classification
- `hidden` → Stored but skips triage, just exists

**Use cases for hidden drops:**
- Transformation artifacts (scripts, logs)
- Reference materials you don't want to organize
- System/internal content
- Bulk archives you just want preserved

**CLI support:**
```bash
# Regular import (visible, needs triage)
$ dwh drop import ~/invoices/

# Hidden import (stored, no triage)
$ dwh drop import --hidden ~/scripts/

# List commands
$ dwh drop list              # Only visible
$ dwh drop list --all        # All drops
$ dwh drop list --hidden     # Only hidden
```

**Key insight:** Drop is the fundamental unit. We don't create variants—we add attributes like `visibility` to handle different use cases.

### Transformation Workflow

#### 1. Start Transformation

Populate `_input/` from a query, drop, or subtree:

```bash
# From query (future: metadata query language)
$ dwh transform start -q "category:finance/taxes/2024/*.pdf"

# From drop
$ dwh transform start -d d_20260329_120000_abc123

# From subtree in archive
$ dwh transform start finance/taxes/2024/

# From multiple paths
$ dwh transform start finance/invoice1.pdf finance/invoice2.pdf
```

**Result:**
```
Transformation: t_20260329_140000_xyz789

Populated _input/ with 10 files:
  invoice-001.pdf (45 KB)
  invoice-002.pdf (52 KB)
  invoice-003.pdf (38 KB)
  ...

Write transformation outputs to _output/
Optionally save scripts/logs to _artifacts/
```

**State:**
- `_input/` populated with copies from blob storage
- `_output/` created (empty)
- `_artifacts/` created (empty, optional)
- Transformation state recorded in database

#### 2. Execute Transformation (External)

User runs any tool/script/process on `_input/` to generate `_output/`:

```bash
# Merge PDFs
$ pypdf merge _input/*.pdf > _output/merged.pdf

# OCR processing
$ ocrmypdf _input/scan.pdf _output/scan-searchable.pdf

# AI summarization
$ claude "Summarize these" < _input/* > _output/summary.md

# Format conversion
$ pandoc _input/doc.docx -o _output/doc.pdf

# Manual editing
$ cp _input/draft.txt _output/final.txt && vim _output/final.txt

# Any script
$ ./my-transform-script.sh _input/ _output/
```

**Optionally capture process artifacts:**

```bash
# Save the script used
$ cat > _artifacts/transform.sh << 'EOF'
#!/bin/bash
for f in _input/*.pdf; do
    ocrmypdf "$f" "_output/$(basename "$f")"
done
EOF

# Run and capture log
$ bash _artifacts/transform.sh 2>&1 | tee _artifacts/output.log

# Add notes
$ echo "Used OCR with deskew, language=deu" > _artifacts/notes.txt
```

**We do NOT require process tracking** - but artifacts can be captured for provenance.

#### 3. Complete Transformation

Two options:

**Option A: Import (needs triage)**
```bash
$ dwh transform import -m "OCR scanned invoices"

Created drop: d_20260329_141000_def456
  Source: transformation t_20260329_140000_xyz789
  Files: 10
Artifacts drop: d_20260329_141001_abc789 (hidden)
  Files: 3 (transform.sh, output.log, notes.txt)

Transformation complete.
Run 'dwh triage checkout' to classify outputs.
```

**Option B: Merge (auto-classify to archive)**
```bash
$ dwh transform merge -m "OCR scanned invoices" -p "finance/taxes/2024"

Created drop: d_20260329_141000_def456
  Source: transformation t_20260329_140000_xyz789
  Files: 10
Artifacts drop: d_20260329_141001_abc789 (hidden)
  Files: 3

Merged to archive:
  finance/taxes/2024/invoice-001-ocr.pdf
  finance/taxes/2024/invoice-002-ocr.pdf
  ...

Transformation complete.
```

**Note:** Artifacts drop has `visibility: hidden` - it's stored but doesn't appear in triage queue.

### Directory Layout

```
warehouse/
  .dwh/
    blobs/           ← Content storage
    dwh.db           ← Metadata
  _history/          ← History events
  _triage/           ← Triage workspace
  _input/            ← Transformation inputs (read-only)
  _output/           ← Transformation outputs (write here)
  _artifacts/        ← Transformation artifacts (optional)
  finance/           ← Archive categories
  work/
  ...
```

### History Record

Transformation creates a history event. **Key principle:** No entry-level data in the record. Input is a query, output uses drop schema.

**Location:** `_history/{seq}_transform_{transform_id}/`

**Files:**
```
{seq}_transform_{transform_id}/
  receipt.json       ← Transformation metadata
  tree/              ← Output files (from _output/)
```

**receipt.json:**
```json
{
  "type": "transformation",
  "transform_id": "t_20260329_140000_xyz789",
  "message": "OCR scanned invoices",
  "actor": "heinrich",
  "created_at": "2026-03-29T14:00:00Z",

  "input": {
    "query": "path:/finance/taxes/2024/scans/",
    "tree_fingerprint": "sha256:abc123..."
  },

  "output": {
    "drop_id": "d_20260329_140100_def456",
    "message": "OCR scanned invoices",
    "actor": "heinrich",
    "created_at": "2026-03-29T14:01:00Z",
    "tree_fingerprint": "sha256:xyz789..."
  },

  "artifacts": {
    "drop_id": "d_20260329_140101_abc789",
    "visibility": "hidden"
  }
}
```

**Notes:**
- `input.query` uses query language (`path:/...` or `drop:d_...`)
- `input.tree_fingerprint` records exact state at transform start
- `output` uses **exact same schema as drop receipt**
- `artifacts` references hidden drop (optional, only if `_artifacts/` non-empty)
- For merge: Creates **two** history records (transform + separate classify)

See ADR-010-history-record-comparison.md for full comparison of record types.

### Database Schema

**Updated table: drops (add visibility)**
```sql
CREATE TABLE drops (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    visibility TEXT DEFAULT 'visible'  -- 'visible' | 'hidden'
);
```

**New table: transformation_state**
```sql
CREATE TABLE transformation_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),  -- Singleton
    transformation_id TEXT NOT NULL,
    input_query TEXT NOT NULL,  -- Query string: "path:/..." or "drop:..."
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**New table: transformations**
```sql
CREATE TABLE transformations (
    id TEXT PRIMARY KEY,  -- t_YYYYMMDD_HHMMSS_HASH
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    input_query TEXT NOT NULL,  -- Query string
    input_fingerprint TEXT NOT NULL,  -- tree_fingerprint of input
    output_drop_id TEXT REFERENCES drops(id),
    artifacts_drop_id TEXT REFERENCES drops(id),  -- Optional, visibility=hidden
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Tracking:**
- Output entries reference their source drop
- Artifacts drop has `visibility: hidden`
- Drop references transformation via history record
- Can query: "What transformation created this drop?"
- Can query: "What artifacts were used in this transformation?"

### Commands

#### `dwh transform start`

```bash
dwh transform start [OPTIONS] [PATHS...]

Options:
  -d, --drop DROP_ID     Use entries from specific drop
  -q, --query QUERY      Use metadata query (future)
  -m, --message TEXT     Transformation description

Arguments:
  PATHS                  Subtrees or files to include

Examples:
  dwh transform start finance/taxes/2024/
  dwh transform start -d d_20260329_120000_abc123
  dwh transform start finance/inv1.pdf finance/inv2.pdf
```

#### `dwh transform status`

```bash
$ dwh transform status

Transformation in progress: t_20260329_140000_xyz789
Started: 2026-03-29 14:00:00
Input query: path:/finance/taxes/2024/

_input/ (10 files, read-only):
  invoice-001.pdf (45 KB)
  invoice-002.pdf (52 KB)
  ...

_output/ (2 files):
  merged.pdf (120 KB)
  summary.txt (2 KB)

_artifacts/ (2 files):
  transform.sh (256 B)
  output.log (4 KB)

Commands:
  dwh transform import -m "..."   Import outputs as new drop
  dwh transform merge -m "..."    Import and merge to archive
  dwh transform abort             Discard transformation
```

#### `dwh transform import`

```bash
dwh transform import -m "MESSAGE"

# Creates drop from _output/ (visibility: visible, needs triage)
# Creates drop from _artifacts/ if non-empty (visibility: hidden)
# Links to transformation provenance
# Clears _input/, _output/, _artifacts/
```

#### `dwh transform merge`

```bash
dwh transform merge -m "MESSAGE" [-p PREFIX]

Options:
  -m, --message TEXT     Transformation description (required)
  -p, --prefix PATH      Target prefix in archive (optional)

# Creates drop from _output/ (visibility: visible)
# Creates drop from _artifacts/ if non-empty (visibility: hidden)
# Auto-classifies outputs to archive (separate classify record)
# Classification: _output/path → PREFIX/path (or archive root if no prefix)
# Clears _input/, _output/, _artifacts/

Examples:
  # Merge to archive root (preserves _output/ structure)
  dwh transform merge -m "OCR batch"
  # _output/file.pdf → archive/file.pdf

  # Merge with prefix
  dwh transform merge -m "OCR batch" -p "finance/taxes/2024"
  # _output/file.pdf → finance/taxes/2024/file.pdf
  # _output/sub/other.pdf → finance/taxes/2024/sub/other.pdf
```

#### `dwh transform abort`

```bash
$ dwh transform abort

Discarding transformation t_20260329_140000_xyz789
Removed _input/ (10 files)
Removed _output/ (2 files)
Removed _artifacts/ (2 files)
```

### Provenance Queries

With transformation records, we can answer:

**"What was this document created from?"**
```bash
$ dwh trace finance/taxes/2024/merged.pdf

File: finance/taxes/2024/merged.pdf
Hash: xyz789...

Transformation: t_20260329_140000_xyz789
  Message: "Merge Q1 invoices"
  Created: 2026-03-29 14:01:00
  Input query: path:/finance/taxes/2024/Q1/

  Artifacts: d_20260329_140101_abc789
    transform.sh
    output.log
```

**"What was derived from this document?"**
```bash
$ dwh trace --derived finance/taxes/2024/invoice-001.pdf

Derived content:

1. Transformation: t_20260329_140000_xyz789
   Message: "Merge Q1 invoices"
   Output: finance/taxes/2024/merged.pdf

2. Transformation: t_20260401_100000_abc123
   Message: "OCR processing"
   Output: finance/taxes/2024/invoice-001-searchable.pdf
```

**"Show artifacts for a transformation"**
```bash
$ dwh drop show d_20260329_140101_abc789

Drop: d_20260329_140101_abc789 (hidden)
Message: "Merge Q1 invoices [artifacts]"
Files:
  transform.sh (256 B)
  output.log (4 KB)
```

### Merge Behavior

`dwh transform merge` preserves `_output/` structure, optionally under a prefix:

**Without prefix (merge to root):**
```
_output/
  report.pdf          → report.pdf
  attachments/
    data.xlsx         → attachments/data.xlsx
    chart.png         → attachments/chart.png
```

**With prefix:**
```bash
$ dwh transform merge -m "Q1 report" -p "finance/reports/2024"
```
```
_output/
  report.pdf          → finance/reports/2024/report.pdf
  attachments/
    data.xlsx         → finance/reports/2024/attachments/data.xlsx
    chart.png         → finance/reports/2024/attachments/chart.png
```

The `--prefix` option makes it easy to target a specific category without restructuring `_output/`.

### Workflow Examples

#### Example 1: Merge PDFs

```bash
$ dwh transform start finance/taxes/2024/Q1/*.pdf
Transformation: t_...
Populated _input/ with 12 invoices

$ pypdf merge _input/*.pdf > _output/Q1-all-invoices.pdf

$ dwh transform merge -m "Merge Q1 invoices"
Created drop: d_...
Merged: Q1-all-invoices.pdf → archive
```

#### Example 2: OCR Processing

```bash
$ dwh transform start scans/batch-001/
Transformation: t_...
Populated _input/ with 50 scanned PDFs

$ for f in _input/*.pdf; do
    ocrmypdf "$f" "_output/$(basename "$f")"
  done

$ dwh transform import -m "OCR batch 001"
Created drop: d_...
Run 'dwh triage checkout' to classify OCR'd files
```

#### Example 3: AI Summarization

```bash
$ dwh transform start -d d_20260329_120000_abc123
Transformation: t_...
Populated _input/ with drop contents

$ claude "Create executive summary" < _input/* > _output/summary.md

$ dwh transform merge -m "AI summary of imported docs"
Merged: summary.md → archive
```

### Implementation Plan

#### Phase 1: Core Infrastructure (4-5 hours)

1. Create transformation_state table
2. Create transformations table
3. Create transformation_inputs table
4. Implement `generate_transformation_id()`
5. Implement `_input/` population from paths/subtrees

#### Phase 2: Start Command (3-4 hours)

1. Implement `dwh transform start` CLI
2. Copy files from blobs to `_input/`
3. Create empty `_output/`
4. Record transformation state

#### Phase 3: Import/Merge Commands (4-5 hours)

1. Implement `dwh transform import`
   - Create drop from `_output/`
   - Record transformation history
   - Link inputs to outputs
2. Implement `dwh transform merge`
   - Create drop from `_output/`
   - Auto-classify to archive paths
   - Record transformation history

#### Phase 4: Status/Abort (2-3 hours)

1. Implement `dwh transform status`
2. Implement `dwh transform abort`
3. Safety checks (warn if outputs exist)

#### Phase 5: Testing & Provenance (4-5 hours)

1. E2E tests for transformation workflow
2. Update `dwh trace` to show transformation provenance
3. Test derived content queries

**Total effort:** ~18-22 hours

## Trade-offs

**Advantages:**
- Full provenance tracking for derived content
- Container-based (natural batch processing)
- Process-agnostic (any tool/script/manual)
- Integrates with existing drop/triage model
- Enables powerful trace queries
- Artifacts captured as hidden drops (reuses drop abstraction)
- `visibility` attribute keeps drop as single fundamental unit

**Disadvantages:**
- Three working directories (_input/, _output/, _artifacts/) to understand
- Can't have triage and transformation simultaneously (v1)
- No partial transformations (all-or-nothing)
- Input query language not yet defined

**Mitigations:**
- Clear status command shows state
- _artifacts/ is optional (empty = no artifacts drop created)
- Future: Allow concurrent operations
- Start simple (paths/drops), add queries later

## Future Enhancements

### Metadata Query Language

```bash
$ dwh transform start -q "type:pdf AND category:finance/* AND date:2024-*"
```

### Transformation Templates

```bash
$ dwh transform start --template ocr-batch finance/scans/
# Auto-runs predefined OCR script
```

### Chained Transformations

```bash
$ dwh transform start -t t_20260329_140000_xyz789
# Start new transformation from outputs of previous
```

### Process Recording (Optional)

```bash
$ dwh transform start --record finance/taxes/
# Records commands executed during transformation
```

## Decision

Implement container-to-container transformations with:
- `_input/` (populated from query/paths)
- `_output/` (user writes results)
- `_artifacts/` (optional: scripts, logs, notes)
- `dwh transform start` to begin
- `dwh transform import` to create drop (needs triage)
- `dwh transform merge` to create drop and auto-classify

**Drop visibility:**
- Add `visibility` attribute to drops (`visible` | `hidden`)
- Hidden drops skip triage queue, just stored
- Artifacts are created as hidden drops
- `dwh drop list --all` shows all, `--hidden` shows only hidden

**History record format:**
- Input: query string + tree_fingerprint (no entry-level data)
- Output: same schema as drop receipt
- Artifacts: reference to hidden drop (optional)
- For merge: separate classify record (two history events)

Transformation history records input query and output drop, enabling full provenance tracking without specifying the transformation process. Artifacts captured as hidden drops reuse the fundamental drop abstraction.

## References

- ADR-006: Triage queue (similar workspace model)
- ADR-008: File provenance trace (extended for transformations)
- ADR-009: Content-addressed blob storage (inputs from blobs)
