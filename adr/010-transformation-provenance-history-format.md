# ADR-010 Appendix: Transformation History Record Format

## History Record Structure

Transformations create a single history record that captures:
1. Transformation metadata
2. Input specification and resolved entries
3. Output entries created
4. Result (drop created, classification if merge)

### File Location

```
_history/
  001_drop_d_20260329_100000_aaa111/
  002_classify.json
  003_transform_t_20260329_140000_xyz789.json   ← Transformation record
  004_drop_d_20260329_141000_def456/            ← Output drop (created by transform)
```

**Naming:** `{seq}_transform_{transformation_id}.json`

### Record Format

```json
{
  "type": "transformation",
  "id": "t_20260329_140000_xyz789",
  "created_at": "2026-03-29T14:01:30Z",
  "actor": "heinrich",
  "message": "OCR scanned invoices for Q1",

  "input_spec": {
    "type": "subtree",
    "paths": ["finance/taxes/2024/scans/"]
  },

  "inputs": [
    {
      "entry_id": "e_20260315_100000_aaa111_001",
      "blob_hash": "abc123def456789abcdef012345678901234567890123456789012345678901",
      "source_path": "finance/taxes/2024/scans/invoice-001.pdf",
      "input_path": "invoice-001.pdf",
      "size": 45234
    },
    {
      "entry_id": "e_20260315_100000_aaa111_002",
      "blob_hash": "bcd234ef5678901abcdef123456789012345678901234567890123456789012",
      "source_path": "finance/taxes/2024/scans/invoice-002.pdf",
      "input_path": "invoice-002.pdf",
      "size": 52100
    },
    {
      "entry_id": "e_20260320_140000_bbb222_003",
      "blob_hash": "cde345f67890123abcdef234567890123456789012345678901234567890123",
      "source_path": "finance/taxes/2024/scans/invoice-003.pdf",
      "input_path": "invoice-003.pdf",
      "size": 38450
    }
  ],

  "outputs": [
    {
      "entry_id": "e_20260329_141000_def456_001",
      "blob_hash": "xyz789012345678901234567890123456789012345678901234567890123456",
      "output_path": "invoice-001-ocr.pdf",
      "filename": "invoice-001-ocr.pdf",
      "size": 48500
    },
    {
      "entry_id": "e_20260329_141000_def456_002",
      "blob_hash": "uvw012345678901234567890123456789012345678901234567890123456789",
      "output_path": "invoice-002-ocr.pdf",
      "filename": "invoice-002-ocr.pdf",
      "size": 55200
    },
    {
      "entry_id": "e_20260329_141000_def456_003",
      "blob_hash": "rst345678901234567890123456789012345678901234567890123456789012",
      "output_path": "invoice-003-ocr.pdf",
      "filename": "invoice-003-ocr.pdf",
      "size": 41800
    }
  ],

  "result": {
    "type": "merge",
    "drop_id": "d_20260329_141000_def456",
    "prefix": "finance/taxes/2024/ocr",
    "classifications": [
      {
        "entry_id": "e_20260329_141000_def456_001",
        "document_id": 145,
        "category": "finance/taxes/2024/ocr",
        "name": "invoice-001-ocr.pdf"
      },
      {
        "entry_id": "e_20260329_141000_def456_002",
        "document_id": 146,
        "category": "finance/taxes/2024/ocr",
        "name": "invoice-002-ocr.pdf"
      },
      {
        "entry_id": "e_20260329_141000_def456_003",
        "document_id": 147,
        "category": "finance/taxes/2024/ocr",
        "name": "invoice-003-ocr.pdf"
      }
    ]
  }
}
```

### Field Definitions

#### Top-Level Fields

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"transformation"` |
| `id` | string | Transformation ID: `t_YYYYMMDD_HHMMSS_HASH` |
| `created_at` | string | ISO 8601 timestamp |
| `actor` | string | Username who ran transformation |
| `message` | string | User-provided description |

#### Input Specification (`input_spec`)

Describes how inputs were selected:

**Type: subtree**
```json
{
  "type": "subtree",
  "paths": ["finance/taxes/2024/", "finance/receipts/2024/"]
}
```

**Type: drop**
```json
{
  "type": "drop",
  "drop_id": "d_20260329_120000_abc123"
}
```

**Type: entries (explicit files)**
```json
{
  "type": "entries",
  "paths": ["finance/invoice1.pdf", "finance/invoice2.pdf"]
}
```

**Type: query (future)**
```json
{
  "type": "query",
  "expression": "category:finance/* AND type:pdf AND date:2024-*"
}
```

#### Inputs Array

Each input entry:

| Field | Type | Description |
|-------|------|-------------|
| `entry_id` | string | Original entry ID |
| `blob_hash` | string | Content hash (SHA-256) |
| `source_path` | string | Path in archive where file lives |
| `input_path` | string | Path in `_input/` (relative) |
| `size` | integer | File size in bytes |

#### Outputs Array

Each output entry:

| Field | Type | Description |
|-------|------|-------------|
| `entry_id` | string | New entry ID (from output drop) |
| `blob_hash` | string | Content hash of output |
| `output_path` | string | Path in `_output/` (relative) |
| `filename` | string | Filename |
| `size` | integer | File size in bytes |

#### Result Object

**For `dwh transform import`:**
```json
{
  "type": "import",
  "drop_id": "d_20260329_141000_def456"
}
```

**For `dwh transform merge`:**
```json
{
  "type": "merge",
  "drop_id": "d_20260329_141000_def456",
  "prefix": "finance/taxes/2024/ocr",
  "classifications": [
    {
      "entry_id": "e_20260329_141000_def456_001",
      "document_id": 145,
      "category": "finance/taxes/2024/ocr",
      "name": "invoice-001-ocr.pdf"
    }
  ]
}
```

### Examples

#### Example 1: Simple Merge (PDF combine)

```json
{
  "type": "transformation",
  "id": "t_20260329_150000_aaa123",
  "created_at": "2026-03-29T15:00:30Z",
  "actor": "heinrich",
  "message": "Merge Q1 invoices into single PDF",

  "input_spec": {
    "type": "subtree",
    "paths": ["finance/taxes/2024/Q1/"]
  },

  "inputs": [
    {
      "entry_id": "e_20260315_100000_aaa_001",
      "blob_hash": "abc123...",
      "source_path": "finance/taxes/2024/Q1/invoice-jan.pdf",
      "input_path": "invoice-jan.pdf",
      "size": 45000
    },
    {
      "entry_id": "e_20260315_100000_aaa_002",
      "blob_hash": "def456...",
      "source_path": "finance/taxes/2024/Q1/invoice-feb.pdf",
      "input_path": "invoice-feb.pdf",
      "size": 52000
    },
    {
      "entry_id": "e_20260315_100000_aaa_003",
      "blob_hash": "ghi789...",
      "source_path": "finance/taxes/2024/Q1/invoice-mar.pdf",
      "input_path": "invoice-mar.pdf",
      "size": 48000
    }
  ],

  "outputs": [
    {
      "entry_id": "e_20260329_150000_bbb_001",
      "blob_hash": "xyz999...",
      "output_path": "Q1-combined.pdf",
      "filename": "Q1-combined.pdf",
      "size": 142000
    }
  ],

  "result": {
    "type": "merge",
    "drop_id": "d_20260329_150000_bbb456",
    "prefix": "finance/taxes/2024",
    "classifications": [
      {
        "entry_id": "e_20260329_150000_bbb_001",
        "document_id": 200,
        "category": "finance/taxes/2024",
        "name": "Q1-combined.pdf"
      }
    ]
  }
}
```

#### Example 2: Import for Triage (no auto-classify)

```json
{
  "type": "transformation",
  "id": "t_20260329_160000_ccc789",
  "created_at": "2026-03-29T16:00:45Z",
  "actor": "heinrich",
  "message": "Extract pages from contract",

  "input_spec": {
    "type": "entries",
    "paths": ["work/contracts/big-contract.pdf"]
  },

  "inputs": [
    {
      "entry_id": "e_20260310_090000_xxx_001",
      "blob_hash": "contract123...",
      "source_path": "work/contracts/big-contract.pdf",
      "input_path": "big-contract.pdf",
      "size": 5000000
    }
  ],

  "outputs": [
    {
      "entry_id": "e_20260329_160000_ddd_001",
      "blob_hash": "page1...",
      "output_path": "page-001.pdf",
      "filename": "page-001.pdf",
      "size": 50000
    },
    {
      "entry_id": "e_20260329_160000_ddd_002",
      "blob_hash": "page2...",
      "output_path": "page-002.pdf",
      "filename": "page-002.pdf",
      "size": 48000
    }
  ],

  "result": {
    "type": "import",
    "drop_id": "d_20260329_160000_ddd456"
  }
}
```

#### Example 3: From Drop Input

```json
{
  "type": "transformation",
  "id": "t_20260329_170000_eee111",
  "created_at": "2026-03-29T17:00:00Z",
  "actor": "heinrich",
  "message": "OCR entire scan batch",

  "input_spec": {
    "type": "drop",
    "drop_id": "d_20260329_090000_scans"
  },

  "inputs": [
    {
      "entry_id": "e_20260329_090000_scans_001",
      "blob_hash": "scan1...",
      "source_path": null,
      "input_path": "scan-001.pdf",
      "size": 2000000
    },
    {
      "entry_id": "e_20260329_090000_scans_002",
      "blob_hash": "scan2...",
      "source_path": null,
      "input_path": "scan-002.pdf",
      "size": 1800000
    }
  ],

  "outputs": [
    {
      "entry_id": "e_20260329_170000_fff_001",
      "blob_hash": "ocr1...",
      "output_path": "scan-001-ocr.pdf",
      "filename": "scan-001-ocr.pdf",
      "size": 2100000
    },
    {
      "entry_id": "e_20260329_170000_fff_002",
      "blob_hash": "ocr2...",
      "output_path": "scan-002-ocr.pdf",
      "filename": "scan-002-ocr.pdf",
      "size": 1900000
    }
  ],

  "result": {
    "type": "import",
    "drop_id": "d_20260329_170000_fff789"
  }
}
```

**Note:** When input is from a drop that hasn't been triaged yet, `source_path` is `null` (files not in archive).

### Provenance Queries

With this format, we can answer:

**"What transformation created this output?"**
```python
# Find transformation where outputs[].entry_id matches
SELECT * FROM transformations WHERE id = (
    SELECT transformation_id FROM transformation_outputs
    WHERE entry_id = ?
)
```

**"What inputs went into this transformation?"**
```python
# Parse transformation record, read inputs array
transform = load_history_record(transformation_id)
for input in transform['inputs']:
    print(f"{input['source_path']} ({input['blob_hash'][:8]}...)")
```

**"What was derived from this input?"**
```python
# Find all transformations where inputs[].entry_id matches
# OR where inputs[].blob_hash matches (same content, different entry)
SELECT * FROM transformations WHERE id IN (
    SELECT transformation_id FROM transformation_inputs
    WHERE entry_id = ? OR blob_hash = ?
)
```

### Relationship to Other History Records

**Transformation + Drop:**
```
_history/
  003_transform_t_20260329_140000_xyz.json  ← Records inputs → outputs
  004_drop_d_20260329_141000_def/           ← Output drop
    receipt.json
    tree/
```

The transformation record references the output drop, and the drop's entries are the transformation outputs.

**Transformation + Classification (for merge):**
The transformation record includes `classifications` in result, so no separate `_classify.json` file is needed. The classification is part of the transformation event.

### Database Tables (Updated)

```sql
-- Transformation records
CREATE TABLE transformations (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    input_spec TEXT NOT NULL,  -- JSON
    result_type TEXT NOT NULL,  -- 'import' or 'merge'
    result_drop_id TEXT REFERENCES drops(id),
    result_prefix TEXT,  -- For merge, the target prefix
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Transformation inputs (many-to-many)
CREATE TABLE transformation_inputs (
    transformation_id TEXT NOT NULL REFERENCES transformations(id),
    entry_id TEXT NOT NULL REFERENCES entries(id),
    input_path TEXT NOT NULL,
    PRIMARY KEY (transformation_id, entry_id)
);

-- Index for provenance queries
CREATE INDEX idx_transformation_inputs_entry ON transformation_inputs(entry_id);
CREATE INDEX idx_transformation_inputs_blob ON transformation_inputs(entry_id);
```

**Outputs are tracked via entries table:**
- Output entries belong to the result drop
- Query: `SELECT * FROM entries WHERE drop_id = transform.result_drop_id`

### Rebuild Considerations

When rebuilding database from history:

```python
def replay_transformation(record: dict, conn: sqlite3.Connection):
    """Replay transformation record."""
    # 1. Insert transformation
    conn.execute("""
        INSERT INTO transformations (id, message, actor, input_spec,
                                      result_type, result_drop_id, result_prefix)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        record['id'],
        record['message'],
        record['actor'],
        json.dumps(record['input_spec']),
        record['result']['type'],
        record['result']['drop_id'],
        record['result'].get('prefix')
    ))

    # 2. Insert transformation inputs
    for input in record['inputs']:
        conn.execute("""
            INSERT INTO transformation_inputs (transformation_id, entry_id, input_path)
            VALUES (?, ?, ?)
        """, (record['id'], input['entry_id'], input['input_path']))

    # 3. If merge, insert classifications
    if record['result']['type'] == 'merge':
        for classification in record['result']['classifications']:
            conn.execute("""
                INSERT INTO documents (entry_id, name, category)
                VALUES (?, ?, ?)
            """, (
                classification['entry_id'],
                classification['name'],
                classification['category']
            ))

    conn.commit()
```

**Note:** The output drop (`004_drop_d_...`) is replayed separately - it's a standard drop record.
