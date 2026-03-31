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

**Location:** `_history/{seq}_transform_{transform_id}/`

**Files:**
```
{seq}_transform_{transform_id}/
  receipt.json       ← Transformation metadata
  tree/              ← Output files (same as drop)
    output-001.pdf
    output-002.pdf
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
  }
}
```

**That's it.** No entry-level data. No classifications embedded.

**For merge:** Creates TWO history records:
1. Transformation record (above)
2. Separate classify record (standard format)

**Purpose:** Record derived content with provenance link to inputs.

---

## Side-by-Side Comparison

| Field | Drop | Classify | Transformation |
|-------|------|----------|----------------|
| `type` | `"drop"` | `"classify"` | `"transformation"` |
| `created_at` | ✓ | ✓ | ✓ |
| `actor` | ✓ | ✓ | ✓ |
| `message` | ✓ | ✓ | ✓ |
| `drop_id` | ✓ (self) | - | ✓ (in output) |
| `tree_fingerprint` | ✓ | - | ✓ (input + output) |
| `input` | - | - | ✓ (query + fingerprint) |
| `output` | - | - | ✓ (same schema as drop) |
| `classifications` | - | ✓ | - (separate record) |
| Has `tree/` folder | ✓ | - | ✓ (output files) |

---

## Input Query Language

The transformation `input.query` field uses a simple query language:

**Path query (subtree in archive):**
```
path:/finance/taxes/2024/scans/
```

**Drop query (all entries from a drop):**
```
drop:d_20260329_120000_abc123
```

**Multiple paths (future):**
```
path:/finance/taxes/ OR path:/finance/receipts/
```

**Metadata query (future - TBD):**
```
type:pdf AND category:finance/*
```

**Input object:**
```json
{
  "query": "path:/finance/taxes/2024/scans/",
  "tree_fingerprint": "sha256:abc123..."
}
```

- `query`: The selection query (resolved at transform start)
- `tree_fingerprint`: Hash of the checkout (records exact state)

**Key principle:** We store the QUERY, not the resolved entries. Given the archive state at this point in history (derived from replaying prior records), the input can be resolved. The `tree_fingerprint` provides verification.

---

## Output Schema

The `output` field uses **exact same schema as drop receipt**:

```json
"output": {
  "drop_id": "d_20260329_140100_def456",
  "message": "OCR scanned invoices",
  "actor": "heinrich",
  "created_at": "2026-03-29T14:01:00Z",
  "tree_fingerprint": "sha256:xyz789..."
}
```

The transformation folder contains the output `tree/` directly - it's self-contained with both provenance (input) and content (output tree/).

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

  003_transform_t_20260329_140000_xyz/  # Transform: OCR processing
    receipt.json                        # Contains input query + output metadata
    tree/                               # Output files
      scan-001-ocr.pdf
      scan-002-ocr.pdf
      scan-003-ocr.pdf

  004_classify.json                    # Classify record (always separate)
                                       # For import: user triages manually
                                       # For merge: auto-generated with prefix
```

**Note:** Transformation is a directory (like drop), not a standalone .json file. It contains both the provenance metadata and the output tree. For merge operations, the classify record is created automatically.

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
| Transform | provenance link | Query spec | Output drop (same as drop) |

**Note:** Transform + Merge = Transform record + Classify record (two separate history events)
