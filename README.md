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
# Initialize a warehouse (works with existing directories!)
dwh init ~/Documents

# Import some files
dwh drop import -m "Tax documents 2024" ~/Downloads/tax/

# Triage the drop (checkout for classification)
dwh triage checkout

# Move files to organize them
mv _triage/invoice.pdf finance/taxes/

# Finalize triage
dwh triage sync

# Done. Documents are classified with full provenance.
```

## Usage

### Importing

```bash
# Import files from outside the warehouse
dwh drop import -m "Bank statements Q1 2025" ~/Downloads/statements/

# Import already-organized files (auto-classifies!)
dwh drop import -m "Tax documents" finance/taxes/2024/
```

What happens:
1. Files are copied to `_history/NNN_drop_.../tree/`
2. A drop receipt (metadata) is written to history
3. Each file becomes an entry with full provenance
4. Files already in the warehouse are auto-classified to their current location

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
dwh triage checkout

# Or checkout a specific drop
dwh triage checkout d_2026-03-28_143211_8f3c

# Organize files by moving them into your archive
mkdir -p finance/taxes
mv _triage/invoice.pdf finance/taxes/

# Finalize triage
dwh triage sync
```

What happens during `triage sync`:
- Scans `_triage/` for remaining files
- Scans archive for new files (matching by content hash)
- Creates classification records in history
- Clears `_triage/`

If sync can't match unambiguously:
```bash
dwh triage sync
# ✓ Classified: invoice.pdf → finance/taxes/
# ✗ Ambiguous: receipt.pdf - skipping
# ⚠ Skipped: 1 file remains in _triage/

# Resolve manually with explicit classification
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

**The warehouse root IS your archive.** Categories live at the root alongside DWH system directories.

```
~/Documents/                    # Your warehouse (can be any directory!)
├── .dwh/                       # Hidden metadata
│   ├── config.toml             # Configuration
│   └── dwh.db                  # Cached state (rebuildable)
├── _history/                   # Canonical event log (source of truth)
│   ├── 001_drop_d_.../
│   │   ├── receipt.json        # Drop metadata (like git commit)
│   │   └── tree/               # Actual files at import time
│   │       └── invoice.pdf
│   └── 002_classify.json       # Classification events
├── _triage/                    # Working directory (ephemeral)
│   └── receipt.pdf             # Files being classified
├── finance/                    # Your categories (at root!)
│   ├── taxes/
│   │   └── invoice.pdf
│   └── receipts/
├── medical/
├── personal/
├── draft.docx                  # Untracked files coexist fine
└── temp/                       # Untracked directories too
```

**Key insight:** DWH adds metadata to your existing directory structure. You can run `dwh init ~/Documents` and your files stay exactly where they are.

### Directory Configuration

System directory names are configurable:

```bash
# Default (recommended)
dwh init ~/Documents

# Use hidden directories (good for backup-aware environments)
dwh init ~/Documents --history-dir .history --triage-dir .triage
```

See `.dwh/config.toml` to change directory names after initialization.

## Tracked vs Untracked Files

**DWH only manages files you explicitly import.** Like Git, DWH distinguishes:

- **Tracked files:** Imported via `dwh drop import` - have full provenance
- **Untracked files:** Other files in the warehouse - ignored by DWH

```
~/Documents/
├── finance/
│   ├── invoice.pdf    # Tracked (imported)
│   └── draft.pdf      # Untracked (just copied there)
```

**Why this matters:**

- Only tracked files can be restored from history
- Untracked files coexist peacefully (no conflicts)
- You control what gets tracked

**Recommendation:** Backup your entire warehouse directory, not just `_history/`.

## Concepts (Summary)

| Concept | Description |
|---------|-------------|
| **Drop** | Import event with receipt (who, when, why, from where) |
| **Entry** | One file within a drop (has full provenance) |
| **Document** | Classified entry with category and name |
| **History** | Append-only log stored in `_history/` (source of truth) |
| **Tracked** | Files imported via DWH (have provenance) |
| **Untracked** | Other files in the warehouse (no DWH metadata) |

**Key invariants:**
- History is the source of truth - database can be rebuilt by replaying it
- Tracked files can be restored from history - untracked files cannot
- Import is durable - once in history, files are preserved forever

For technical details, see [DESIGN.md](DESIGN.md).

## Technical Design

See [DESIGN.md](DESIGN.md) for:
- History format and replay
- Database schema
- Triage workflow details
- Invariants and guarantees
