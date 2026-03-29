# DWH - Document Warehouse

A durable document warehouse for archiving files with strong provenance and readable filesystem representation.

## Product Goals

**DWH is a storage tool, not a workflow tool.**

It sits in the same category as filesystems (ext4, ZFS, APFS) and object stores (S3), not workflow engines or transformation pipelines.

### Core Promises

1. **Durability** - What you give us, we keep. Forever.
2. **Provenance** - Every file has a receipt: who, when, why, from where.
3. **Metadata-centric** - Metadata is canonical; filesystems are projections.
4. **Open Classification** - External tools (including LLMs) can refine organization.

### What DWH Is

- A place to put documents you want to keep for 10+ years
- An archive that tracks *why* things were archived, not just *what*
- Metadata over content-addressed blobs, with filesystem projections
- A system where classification can improve over time

### What DWH Is Not

- A backup system (use restic, borgbackup, etc.)
- A transformation pipeline (use Make, Snakemake, etc.)
- A workflow orchestrator (use Airflow, Prefect, etc.)
- A full-text search engine (use ripgrep, Elasticsearch, etc.)

## Architecture

DWH has three layers:

1. **Blob layer** - Content-addressed immutable storage. Handles deduplication, replication, caching.
2. **Metadata layer** - SQLite database with drops, entries, provenance, classification, placements.
3. **Projection layer** - Filesystem views (`drops/`, `archive/`) derived from metadata. Rebuildable.

The key principle: **metadata is canonical; filesystems are projections.**

## v1 Interface

### CLI Commands

```bash
# Initialize a warehouse
dwh init .

# Import files with provenance
dwh import -m "Bank statements Q1 2025" ~/Downloads/statements/
dwh import -m "Tax receipts" invoice.pdf receipt.pdf folder/

# Export a drop (reconstruct stored drop)
dwh export d_2026-03-28_143211_8f3c ./restore/

# List drops
dwh list

# Show drop details
dwh show d_2026-03-28_143211_8f3c

# Reconcile archive edits into metadata
dwh sync
```

### Filesystem Layout

After `dwh init .`:

```
warehouse/
├── drops/              # Recent drops (receipts + files)
│   └── 2026/
│       └── 03/
│           └── d_2026-03-28_143211_8f3c/
│               ├── manifest.json
│               └── files/
├── archive/            # Editable categorization view
│   ├── pile/           # Uncategorized documents
│   ├── finance/
│   │   ├── amazon/
│   │   └── comdirect/
│   └── ...
└── .dwh/               # Internal state (do not touch)
    ├── dwh.db          # SQLite metadata
    ├── blobs/          # Content-addressed storage
    ├── derived/        # Computed artifacts (text extraction, etc.)
    └── cache/          # Transient caches
```

### Import Flow

```
┌─────────────────┐      dwh import -m         ┌──────────────┐
│  Your files     │  ───────────────────────▶  │  .dwh/blobs  │
│  (anywhere)     │       "reason"             │  (canonical) │
└─────────────────┘                            └──────────────┘
                                                      │
                              ┌────────────────────────┤
                              ▼                       ▼
                       ┌──────────────┐        ┌──────────────┐
                       │   drops/     │        │  archive/    │
                       │  (receipt)   │        │   pile/      │
                       └──────────────┘        └──────────────┘
```

Files are copied (not moved) into the warehouse. The original stays where it was.

### Classification Flow

```
┌──────────────────┐    mv / Finder / LLM    ┌──────────────────┐
│   archive/pile   │  ─────────────────────▶ │  archive/finance │
└──────────────────┘                         └──────────────────┘
                                                      │
                                 dwh sync             │
                          ◀───────────────────────────┘

                          Reconciles filesystem edits into metadata
```

Classification is expressed through filesystem placement. Move a file to `finance/amazon/` and `dwh sync` records that as classification intent.

### Projections and Retention

`drops/` and `archive/` are **node-local retained projections**, not canonical truth.

- `drops/` - Recent drops (default: 30 days). Older drops remain exportable via `dwh export`.
- `archive/` - Current filing view (default: 5 years). Configurable per category.

Canonical state lives in metadata + blobs. Projections can be rebuilt.

## Design Documents

- [DWH Design](adr/DWH-DESIGN.md) - Comprehensive design document
- [ADR-001](adr/001-drop-based-archival.md) - Drop-based archival with provenance
- [ADR-002](adr/002-metadata-canonical.md) - Metadata is canonical

## Installation

```bash
cd src/dwh
make install   # Uses uv to install
```

## Status

**v1 in development.** Core import and capture workflows are functional.

## License

Private repository. All rights reserved.
