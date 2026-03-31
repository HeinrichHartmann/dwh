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
Query/Selection → _input/    ← Read-only view of inputs
                  _output/   ← User writes transformation results
                      ↓
                  Drop + Provenance Record
```

**Key principle:** We don't operate on individual files. We operate on **containers** (sets of files defined by query or selection).

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
```

**State:**
- `_input/` populated with copies from blob storage
- `_output/` created (empty)
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

**We do NOT specify or track the process** - just inputs and outputs.

#### 3. Complete Transformation

Two options:

**Option A: Import (needs triage)**
```bash
$ dwh transform import -m "OCR scanned invoices"

Created drop: d_20260329_141000_def456
  Source: transformation t_20260329_140000_xyz789
  Files: 10

Transformation complete.
Run 'dwh triage checkout' to classify outputs.
```

**Option B: Merge (auto-classify to archive)**
```bash
$ dwh transform merge -m "OCR scanned invoices"

Created drop: d_20260329_141000_def456
  Source: transformation t_20260329_140000_xyz789
  Files: 10

Merged to archive:
  finance/taxes/2024/invoice-001-ocr.pdf
  finance/taxes/2024/invoice-002-ocr.pdf
  ...

Transformation complete.
```

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
  finance/           ← Archive categories
  work/
  ...
```

### History Record

Transformation creates a history event:

```json
{
  "type": "transformation",
  "id": "t_20260329_140000_xyz789",
  "created_at": "2026-03-29T14:00:00Z",
  "actor": "heinrich",
  "message": "OCR scanned invoices",

  "input_spec": {
    "type": "subtree",
    "path": "finance/taxes/2024/"
  },

  "inputs": [
    {
      "entry_id": "e_20260315_100000_aaa_001",
      "blob_hash": "abc123...",
      "path": "invoice-001.pdf",
      "size": 45234
    },
    {
      "entry_id": "e_20260320_140000_bbb_003",
      "blob_hash": "def456...",
      "path": "invoice-002.pdf",
      "size": 52100
    }
  ],

  "outputs": [
    {
      "entry_id": "e_20260329_141000_def_001",
      "blob_hash": "xyz789...",
      "path": "invoice-001-ocr.pdf",
      "size": 48500
    },
    {
      "entry_id": "e_20260329_141000_def_002",
      "blob_hash": "uvw012...",
      "path": "invoice-002-ocr.pdf",
      "size": 55200
    }
  ],

  "result_drop_id": "d_20260329_141000_def456",
  "result_type": "merge"
}
```

### Database Schema

**New table: transformation_state**
```sql
CREATE TABLE transformation_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),  -- Singleton
    transformation_id TEXT NOT NULL,
    input_spec TEXT NOT NULL,  -- JSON: query, drop_id, or paths
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**New table: transformations**
```sql
CREATE TABLE transformations (
    id TEXT PRIMARY KEY,  -- t_YYYYMMDD_HHMMSS_HASH
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    input_spec TEXT NOT NULL,  -- JSON
    result_drop_id TEXT REFERENCES drops(id),
    result_type TEXT NOT NULL,  -- 'import' or 'merge'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**New table: transformation_inputs**
```sql
CREATE TABLE transformation_inputs (
    transformation_id TEXT NOT NULL REFERENCES transformations(id),
    entry_id TEXT NOT NULL REFERENCES entries(id),
    input_path TEXT NOT NULL,  -- Path in _input/
    PRIMARY KEY (transformation_id, entry_id)
);
```

**Outputs tracked via entries table:**
- Output entries reference their source drop
- Drop references transformation via history record
- Can query: "What transformation created this drop?"

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
Input spec: subtree finance/taxes/2024/

_input/ (10 files, read-only):
  invoice-001.pdf (45 KB)
  invoice-002.pdf (52 KB)
  ...

_output/ (2 files):
  merged.pdf (120 KB)
  summary.txt (2 KB)

Commands:
  dwh transform import -m "..."   Import outputs as new drop
  dwh transform merge -m "..."    Import and merge to archive
  dwh transform abort             Discard transformation
```

#### `dwh transform import`

```bash
dwh transform import -m "MESSAGE"

# Creates drop from _output/
# Links to transformation provenance
# Clears _input/ and _output/
# Drop needs triage
```

#### `dwh transform merge`

```bash
dwh transform merge -m "MESSAGE" [-p PREFIX]

Options:
  -m, --message TEXT     Transformation description (required)
  -p, --prefix PATH      Target prefix in archive (optional)

# Creates drop from _output/
# Auto-classifies outputs to archive
# Classification: _output/path → PREFIX/path (or archive root if no prefix)
# Clears _input/ and _output/

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

  Inputs (3 files):
    finance/taxes/2024/invoice-001.pdf
    finance/taxes/2024/invoice-002.pdf
    finance/taxes/2024/invoice-003.pdf
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

**Disadvantages:**
- Two working directories (_input/, _output/) to understand
- Can't have triage and transformation simultaneously (v1)
- No partial transformations (all-or-nothing)
- Input query language not yet defined

**Mitigations:**
- Clear status command shows state
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
- `dwh transform start` to begin
- `dwh transform import` to create drop (needs triage)
- `dwh transform merge` to create drop and auto-classify

Transformation history records input entries and output entries, enabling full provenance tracking without specifying the transformation process.

## References

- ADR-006: Triage queue (similar workspace model)
- ADR-008: File provenance trace (extended for transformations)
- ADR-009: Content-addressed blob storage (inputs from blobs)
