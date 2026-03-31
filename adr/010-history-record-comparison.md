# History Record Types Comparison

Three history record types in DWH:

## 1. Drop Record

**Location:** `_history/{seq}_drop_{drop_id}/`

**Files:**
```
{seq}_drop_{drop_id}/
  receipt.json       ← Metadata
  tree/              ← Filesystem projection (from blobs)
    file1.pdf
    subdir/
      file2.txt
```

**receipt.json:**
```json
{
  "type": "drop",
  "drop_id": "d_20260329_120000_abc123",
  "message": "Import Q1 invoices",
  "actor": "heinrich",
  "created_at": "2026-03-29T12:00:00Z",
  "tree_fingerprint": "sha256:..."
}
```

**Purpose:** Import external files into warehouse.

---

## 2. Classify Record

**Location:** `_history/{seq}_classify.json`

**Format:**
```json
{
  "type": "classify",
  "created_at": "2026-03-29T12:15:00Z",
  "actor": "heinrich",
  "message": "Triage sync",
  "classifications": [
    {
      "entry_id": "e_20260329_120000_abc123_001",
      "document_id": 42,
      "category": "finance/taxes/2024",
      "name": "invoice-001.pdf"
    },
    {
      "entry_id": "e_20260329_120000_abc123_002",
      "document_id": 43,
      "category": "finance/taxes/2024",
      "name": "invoice-002.pdf"
    }
  ]
}
```

**Purpose:** Record classification of entries to archive categories.

---

## 3. Transformation Record

**Location:** `_history/{seq}_transform_{transform_id}.json`

**Format:**
```json
{
  "type": "transformation",
  "transform_id": "t_20260329_140000_xyz789",
  "message": "OCR scanned invoices",
  "actor": "heinrich",
  "created_at": "2026-03-29T14:00:00Z",

  "input": {
    "type": "subtree",
    "paths": ["finance/taxes/2024/scans/"]
  },

  "output": {
    "drop_id": "d_20260329_140100_def456"
  }
}
```

**For merge (with auto-classification):**
```json
{
  "type": "transformation",
  "transform_id": "t_20260329_140000_xyz789",
  "message": "OCR scanned invoices",
  "actor": "heinrich",
  "created_at": "2026-03-29T14:00:00Z",

  "input": {
    "type": "subtree",
    "paths": ["finance/taxes/2024/scans/"]
  },

  "output": {
    "drop_id": "d_20260329_140100_def456",
    "prefix": "finance/taxes/2024/ocr"
  },

  "classifications": [
    {
      "entry_id": "e_20260329_140100_def456_001",
      "document_id": 145,
      "category": "finance/taxes/2024/ocr",
      "name": "invoice-001-ocr.pdf"
    },
    {
      "entry_id": "e_20260329_140100_def456_002",
      "document_id": 146,
      "category": "finance/taxes/2024/ocr",
      "name": "invoice-002-ocr.pdf"
    }
  ]
}
```

**Purpose:** Record derived content with provenance link to inputs.

---

## Side-by-Side Comparison

| Field | Drop | Classify | Transformation |
|-------|------|----------|----------------|
| `type` | `"drop"` | `"classify"` | `"transformation"` |
| `created_at` | ✓ | ✓ | ✓ |
| `actor` | ✓ | ✓ | ✓ |
| `message` | ✓ | ✓ | ✓ |
| `drop_id` | ✓ (self) | - | ✓ (output) |
| `tree_fingerprint` | ✓ | - | - |
| `input` | - | - | ✓ (query spec) |
| `classifications` | - | ✓ | ✓ (if merge) |
| Has `tree/` folder | ✓ | - | - (output drop has it) |

---

## Input Specification Types

The transformation `input` field describes what was selected:

**Subtree (paths in archive):**
```json
{
  "type": "subtree",
  "paths": ["finance/taxes/2024/", "finance/receipts/"]
}
```

**Drop (all entries from a drop):**
```json
{
  "type": "drop",
  "drop_id": "d_20260329_120000_abc123"
}
```

**Query (future - metadata query):**
```json
{
  "type": "query",
  "expression": "category:finance/* AND type:pdf"
}
```

**Key principle:** We do NOT enumerate the resolved entries. Given the archive state at this point in history (derived from replaying prior records), the input can be resolved.

---

## Output Consistency

Transformation output creates a **standard drop**:

```
_history/
  003_transform_t_20260329_140000_xyz789.json   ← Links input → output
  004_drop_d_20260329_140100_def456/            ← Standard drop structure
    receipt.json
    tree/
      invoice-001-ocr.pdf
      invoice-002-ocr.pdf
```

The transformation record's `output.drop_id` points to the output drop.

**For merge:** Transformation also includes `classifications[]` (same format as classify record).

---

## History Sequence Example

```
_history/
  001_drop_d_20260315_100000_aaa/      # Import scanned invoices
    receipt.json
    tree/
      scan-001.pdf
      scan-002.pdf
      scan-003.pdf

  002_classify.json                    # Triage: classify to finance/scans/
    # classifications: scan-*.pdf → finance/scans/

  003_transform_t_20260329_140000_xyz.json   # Transform: OCR processing
    # input: subtree finance/scans/
    # output: drop d_20260329_140100_def

  004_drop_d_20260329_140100_def/      # Output drop (created by transform)
    receipt.json
    tree/
      scan-001-ocr.pdf
      scan-002-ocr.pdf
      scan-003-ocr.pdf

  005_classify.json                    # Triage output OR embedded in transform
    # (if transform import, need separate classify)
    # (if transform merge, classifications in transform record)
```

---

## Rebuild Logic

```python
def rebuild_from_history(history_dir, db_path):
    for item in sorted(history_dir.iterdir()):
        if item.is_dir() and '_drop_' in item.name:
            # Replay drop: create entries from tree/
            replay_drop(item, conn)

        elif item.name.endswith('_classify.json'):
            # Replay classification: create documents
            replay_classify(item, conn)

        elif '_transform_' in item.name:
            # Replay transformation: record provenance
            replay_transform(item, conn)
            # Note: output drop replayed separately (next item usually)
```

---

## Summary

| Record | What it creates | Input | Output |
|--------|-----------------|-------|--------|
| Drop | entries, blobs | External files | tree/ folder |
| Classify | documents | Entry references | Category assignments |
| Transform | provenance link | Query spec | Drop reference |
