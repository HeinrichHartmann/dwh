# DWH Technical Design

This document covers the technical design of DWH. For product goals and usage, see [README.md](README.md).

## Core Insight

**History is the source of truth. Everything else is derived.**

The warehouse maintains an append-only history log. The database is a cached materialization of that history. Projections (`triage/`, `documents/`) are views derived from the database.

```
┌─────────────────────────────────────┐
│  Projections                        │  triage/, documents/
│  (rebuildable views)                │
├─────────────────────────────────────┤
│  Database                           │  .dwh/dwh.db
│  (cached state, rebuildable)        │
├─────────────────────────────────────┤
│  History                            │  .dwh/history/
│  (append-only log, source of truth) │
├─────────────────────────────────────┤
│  Blobs                              │  .dwh/blobs/
│  (content-addressed, immutable)     │
└─────────────────────────────────────┘
```

**Key invariant:** The database can be rebuilt by replaying history from scratch.

## Concepts

### Blob

Byte-array stored on disk. Content-addressed by SHA-256 hash.

Properties:
- Immutable (content never changes)
- Nameless (no filename, just hash)
- Deduplicated (same content = same blob)

Storage: Files live in `history/NNN_drop_.../tree/`. Content-addressed blob storage is optional (v2).

### Drop

An import event. Represented as a folder in history.

Contains:
- **drop_id** - Generated identifier: `d_{YYYYMMDD}_{HHMMSS}_{hash8}`
- **message** - User-provided import reason (required)
- **actor** - Username who performed the import
- **created_at** - Timestamp
- **entries** - List of Entry records

Stored in history as: `.dwh/history/NNN_drop_{drop_id}/receipt.json`

### Entry

One file occurrence within a drop.

Contains:
- **entry_id** - Deterministic ID: `e_<hash16>` derived from `drop_id:relative_path`
- **drop_id** - Parent drop (FK)
- **blob_hash** - Content reference (FK)
- **filename** - Original filename with extension
- **relative_path** - Path structure within the import
- **source_path** - Absolute path where file was found

### Document

A classified entry with metadata.

Contains:
- **document_id** - Integer ID (database AUTOINCREMENT)
- **entry_id** - Source entry (FK, immutable)
- **name** - Display name (mutable)
- **category** - Classification path (mutable)

**Relationship:** Entry + classification → Document

### Classification Event

A record of filing/classification. Represented as a JSON file in history.

The history sequence number serves as the classification ID.

Stored in history as: `.dwh/history/NNN_classify.json`

## History: Append-Only Log

History is a numbered, append-only sequence of events:

```
.dwh/history/
├── 001_drop_d_20260329_143211/       # Drop (folder)
│   ├── receipt.json                  # Metadata
│   └── tree/                         # Files (entries derived from this)
│       └── invoice.pdf
├── 002_drop_d_20260329_150000/       # Drop (folder)
│   ├── receipt.json
│   └── tree/
│       └── ...
├── 003_classify_a1b2c3d4.json        # Classification (file)
├── 004_drop_d_20260330_091500/       # Drop (folder)
│   ├── receipt.json
│   └── tree/
│       └── ...
└── 005_classify_e5f6g7h8.json        # Classification (file)
```

### Drop Record (folder)

Like a git commit: metadata in `receipt.json`, content in `tree/`.

```
NNN_drop_{drop_id}/
├── receipt.json      # Drop metadata (like commit object)
└── tree/             # Actual files (entries derived from this)
    ├── invoice.pdf
    └── march/
        └── receipt.pdf
```

`receipt.json` (metadata only):
```json
{
  "type": "drop",
  "drop_id": "d_20260329_143211_a1b2c3d4",
  "message": "Tax documents 2024",
  "actor": "hhartmann",
  "created_at": "2026-03-29T14:32:11Z"
}
```

**Entries are derived** by scanning `tree/`:
- `filename` = file name
- `relative_path` = path within tree/
- `blob_hash` = computed from file content
- `size` = file size
- `entry_id` = generated deterministically from drop_id + relative_path

### Classification Record (file)

`NNN_classify.json`:
```json
{
  "type": "classify",
  "created_at": "2026-03-29T15:00:00Z",
  "actor": "hhartmann",
  "message": "Filing tax documents",
  "classifications": [
    {
      "entry_id": "e_f4bea3104f381c7e",
      "document_id": 1,
      "category": "finance/taxes",
      "name": "invoice.pdf"
    }
  ]
}
```

### Rebuilding from History

```python
def rebuild_database(history_dir: Path, db_path: Path):
    """Rebuild database by replaying history."""
    init_empty_db(db_path)
    conn = connect(db_path)

    for item in sorted(history_dir.iterdir()):
        if item.is_dir() and "_drop_" in item.name:
            # Load drop metadata
            receipt = json.loads((item / "receipt.json").read_text())

            # Derive entries by scanning tree/
            entries = scan_tree(item / "tree", receipt["drop_id"])

            apply_drop(conn, receipt, entries)
        elif item.suffix == ".json" and "_classify_" in item.name:
            record = json.loads(item.read_text())
            apply_classification(conn, record)

    conn.commit()

def scan_tree(tree_dir: Path, drop_id: str) -> list[Entry]:
    """Derive entries from tree/ contents."""
    entries = []
    for file in tree_dir.rglob("*"):
        if file.is_file():
            relative_path = file.relative_to(tree_dir)
            entries.append(Entry(
                id=generate_entry_id(drop_id, relative_path),
                filename=file.name,
                relative_path=str(relative_path),
                blob_hash=compute_hash(file),
                size=file.stat().st_size,
            ))
    return entries
```

## Database Schema

The database is a cached materialization of history. It can be rebuilt at any time.

```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE blobs (
    hash TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mime_type TEXT,
    stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE drops (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE entries (
    id TEXT PRIMARY KEY,
    drop_id TEXT NOT NULL REFERENCES drops(id),
    blob_hash TEXT NOT NULL REFERENCES blobs(hash),
    filename TEXT NOT NULL,
    relative_path TEXT,
    source_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL UNIQUE REFERENCES entries(id),
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_entries_drop_id ON entries(drop_id);
CREATE INDEX idx_entries_blob_hash ON entries(blob_hash);
CREATE INDEX idx_documents_entry_id ON documents(entry_id);
CREATE INDEX idx_documents_category ON documents(category);
```

## Triage Workflow

Triage is the process of classifying entries from a drop.

### Commands

```bash
# Checkout a drop for triage
dwh triage checkout           # Latest unprocessed drop
dwh triage checkout <drop_id> # Specific drop

# User organizes files
mv triage/invoice.pdf documents/finance/taxes/

# Finalize triage
dwh triage sync
# Matches missing triage files to documents/
# Creates classification record in history
# Clears triage/
```

### Triage Flow

1. **`dwh triage checkout`** - Checks out a drop to `./triage/`
   - Creates `triage/` directory
   - Links/copies files from the drop's entries
   - Records which drop is being triaged

2. **User organizes** - Moves/copies files from `triage/` to `documents/`
   - `mv triage/invoice.pdf documents/finance/`
   - Can create category directories as needed

3. **`dwh triage sync`** - Finalizes the triage
   - Scans `triage/` for missing files
   - Scans `documents/` for new files
   - Matches by filename + content hash
   - If unambiguous: creates classification records
   - If ambiguous: reports and skips
   - Appends classification event to history
   - Clears `triage/`

### Ambiguity Handling

When `dwh triage sync` cannot determine a unique match:
1. Skip the file
2. Report it clearly
3. User resolves via explicit `dwh file` command

```bash
dwh triage sync
# ✓ Classified: invoice.pdf → finance/taxes/
# ✗ Ambiguous: receipt.pdf - multiple matches, skipping

# Resolve manually
dwh file <entry_id> --category finance/taxes --name receipt.pdf
dwh triage sync
```

## Projections

### triage/

Working directory for current triage operation.

```
triage/
├── invoice.pdf
├── receipt.pdf
└── folder/
    └── nested.txt
```

- Ephemeral (cleared after sync)
- Contains files from one drop at a time
- User moves files out to `documents/`

### documents/

Normalized view of all classified documents.

```
documents/
├── finance/
│   ├── taxes/
│   │   └── invoice.pdf
│   └── amazon/
│       └── receipt.pdf
└── medical/
    └── insurance.pdf
```

- Projection of documents table
- Rebuildable from database
- Files are links/copies of blobs

## Invariants

1. **History is append-only** - Events are only added, never modified or deleted.
2. **History is source of truth** - Database can be rebuilt by replaying history.
3. **Blobs are immutable** - Content never changes after storage.
4. **Drops are immutable** - Once recorded, drop data never changes.
5. **Documents are mutable** - Name and category can change (via new classification events).
6. **Provenance is preserved** - Document → Entry → Drop chain is always traceable.
7. **Projections are rebuildable** - `triage/` and `documents/` can be regenerated.

## Filesystem Layout

```
warehouse/
├── triage/                     # Current drop being processed
│   └── ...
├── documents/                  # Classified documents (projection)
│   └── {category}/
│       └── {name}
└── .dwh/
    ├── config.toml
    ├── dwh.db                  # Cached state (rebuildable)
    └── history/                # Append-only log (source of truth)
        ├── 001_drop_d_.../
        │   ├── receipt.json    # Drop metadata
        │   └── tree/           # Actual files (canonical location)
        │       └── invoice.pdf
        ├── 002_classify_...json
        └── ...
```

## Commands Summary

| Command | Purpose |
|---------|---------|
| `dwh init` | Initialize warehouse |
| `dwh drop import -m "msg" <paths>` | Import files, create drop in history |
| `dwh drop list` | List all drops |
| `dwh drop inspect <drop_id>` | Show drop details |
| `dwh triage checkout [drop_id]` | Checkout drop for triage |
| `dwh triage sync` | Finalize triage, create classifications |
| `dwh file <entry_id> --category X` | Explicitly classify an entry |
| `dwh restore` | Rebuild projections from database |
| `dwh rebuild` | Rebuild database from history |
| `dwh verify` | Check blob integrity |

## Durability

### What DWH Provides

1. **Append-only history** - Full audit trail of all operations
2. **Rebuildable state** - Database can be rebuilt from history
3. **Blob integrity** - Content-addressed, verifiable
4. **Immutable provenance** - Entry → Drop chain never changes

### What DWH Does NOT Provide

1. **Backup / Replication** - Single copy on local disk
2. **Disaster recovery** - If `.dwh/` is lost, data is gone

### Backup Strategy

Back up the entire warehouse directory:
```bash
restic backup ~/warehouse/
```

The `.dwh/` directory contains everything needed to rebuild.

## Open Questions

1. **History compaction** - Should old history be compactable? (Probably no for v1)
2. **Concurrent triage** - One triage at a time for v1
3. **Undo** - Add `undo` events to history? (v2)
