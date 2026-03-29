# DWH - Document Warehouse

A durable document warehouse for archiving files with strong provenance and readable filesystem representation.

## Product Goals

1. **Durability** - What you give us, we keep. Forever.
2. **Provenance** - Every file has a receipt: who, when, why, from where.
3. **Metadata-centric** - History is the source of truth; everything else is derived.
4. **Open Classification** - External tools (including LLMs) can refine organization.

## What DWH Is

- A place to put documents you want to keep for 10+ years
- An archive that tracks *why* things were archived, not just *what*
- Append-only history with rebuildable database and projections
- A system where classification can improve over time

## What DWH Is Not

- A backup system (use restic, borgbackup, etc.)
- A transformation pipeline (use Make, Snakemake, etc.)
- A workflow orchestrator (use Airflow, Prefect, etc.)
- A full-text search engine (use ripgrep, Elasticsearch, etc.)

## Quick Start

```bash
# Initialize a warehouse
dwh init .

# Import some files
dwh drop import -m "Tax documents 2024" ~/Downloads/tax/

# Triage the drop (checkout for classification)
dwh triage

# Move files to documents/ to classify them
mv triage/invoice.pdf documents/finance/taxes/

# Finalize triage
dwh triage sync

# Done. Documents are classified with full provenance.
```

## Usage

### Importing

```bash
# Import files with provenance
dwh drop import -m "Bank statements Q1 2025" ~/Downloads/statements/
dwh drop import -m "Tax receipts" invoice.pdf receipt.pdf folder/
```

What happens:
1. Files are copied into `.dwh/blobs/` (content-addressed)
2. A drop record is appended to `.dwh/history/`
3. Each file becomes an entry within the drop

### Listing and Inspecting

```bash
# List all drops
dwh drop list

# Show drop details
dwh drop inspect d_2026-03-28_143211_8f3c
```

### Triage

Triage is how you classify documents from a drop.

```bash
# Checkout the latest unprocessed drop
dwh triage

# Or checkout a specific drop
dwh triage d_2026-03-28_143211_8f3c

# Organize files by moving them to documents/
mkdir -p documents/finance/taxes
mv triage/invoice.pdf documents/finance/taxes/

# Finalize triage
dwh triage sync
```

What happens during `triage sync`:
- Scans `triage/` for missing files
- Scans `documents/` for new files
- Matches by filename + content hash
- Creates classification records in history
- Clears `triage/`

If sync can't match unambiguously:
```bash
dwh triage sync
# ✓ Classified: invoice.pdf → finance/taxes/
# ✗ Ambiguous: receipt.pdf - skipping

# Resolve manually
dwh file <entry_id> --category finance/taxes --name receipt.pdf
dwh triage sync
```

### Rebuilding

```bash
# Rebuild database from history (if corrupted)
dwh rebuild

# Rebuild projections from database
dwh restore
```

### Verifying

```bash
# Check integrity of all blobs
dwh verify

# Check a specific drop
dwh verify d_2026-03-28_143211_8f3c
```

## Filesystem Layout

```
warehouse/
├── triage/                     # Current drop being classified
│   ├── invoice.pdf
│   └── receipt.pdf
├── documents/                  # Classified documents
│   ├── finance/
│   │   └── taxes/
│   │       └── invoice.pdf
│   └── ...
└── .dwh/
    ├── config.toml
    ├── dwh.db                  # Cached state (rebuildable)
    └── history/                # Append-only log (source of truth)
        ├── 001_drop_d_.../
        │   ├── receipt.json    # Drop metadata (like git commit)
        │   └── tree/           # Actual files (canonical location)
        │       └── invoice.pdf
        ├── 002_classify_...json
        └── ...
```

## Concepts (Summary)

| Concept | Description |
|---------|-------------|
| **Blob** | Raw bytes, content-addressed, immutable |
| **Drop** | Import event with receipt (who, when, why) |
| **Entry** | One file within a drop (has provenance) |
| **Document** | Classified entry with category and name |
| **History** | Append-only log of drops and classifications |

**Key invariant:** Database can be rebuilt by replaying history.

For technical details, see [DESIGN.md](DESIGN.md).

## Technical Design

See [DESIGN.md](DESIGN.md) for:
- History format and replay
- Database schema
- Triage workflow details
- Invariants and guarantees
