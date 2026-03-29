# ADR-006: Metadata-Centric Document Warehouse Architecture

**Status:** Proposed
**Date:** 2026-03-28
**Deciders:** Repository maintainer
**Supersedes:** ADR-001 (Git-Based Storage), ADR-002 (Source-Based Organization)

## Context

The current architecture treats the filesystem as the source of truth. Documents are organized by source (e.g., `data/finance/Comdirect-8862-Private/PostBox/...`), and provenance is tracked through Git commit messages. This approach has limitations:

1. **Filesystem paths encode meaning.** Renaming or moving a file changes its identity. Reclassification requires physical file moves.

2. **Classification is coupled to placement.** A document can only exist in one location. Cross-cutting views (e.g., "all 2025 tax documents" spanning multiple sources) require manual aggregation.

3. **Metadata is scattered.** Document attributes live in filenames, folder structure, and Git history. There is no unified query interface.

4. **Ingestion requires upfront classification.** Documents cannot be stored until they are triaged and placed in the correct folder, creating friction.

5. **Extraction artifacts are loosely managed.** OCR results and text extractions are not systematically tracked or cached.

The system should evolve from a filesystem archive to a document warehouse where:

- Raw blobs are immutable and stored independently of their classification
- Metadata is the canonical source of truth
- Filesystem paths are derived projections over metadata
- Classification can improve over time without moving files
- Documents can appear in multiple views without duplication

## Decision

We will adopt a **metadata-centric document warehouse architecture** with the following design principles and structure.

### Core Principles

1. **Raw permanence, semantic malleability.**
   - Original blobs are never modified or deleted.
   - Classification, extraction, and folder placement can evolve over time.

2. **Metadata is canonical truth.**
   - Document identity, classification, and relationships live in a database.
   - Filesystem paths are derived views, not authoritative records.

3. **Separation of storage from presentation.**
   - Blobs are stored by content hash in an internal store.
   - The `originals/` tree is a rendered projection that can be regenerated.

4. **Ingestion before classification.**
   - Documents can be stored immediately upon arrival.
   - Classification happens asynchronously and can be refined over time.

### Folder Structure

```
~/warehouse/personal/           # Repository root (dwh init .)
├── .dwh/                       # Internal store (owned entirely by dwh)
│   ├── config.toml             # Configuration
│   ├── dwh.db                  # SQLite database (metadata keyed by hash)
│   └── store/
│       └── blob/               # Content-addressed blob store (SHA-256)
│           └── ab/cd/abcd...   # Raw file bytes by hash prefix
│
├── inbox/                      # User drop zone (writable by users)
│
└── originals/                  # Canonical projection (read-only for users)
    ├── pile/                   # Default category for unclassified documents
    │   └── 2026/
    │       └── 2026-03-28_invoice_a1b2c3d4.pdf
    ├── finance/
    │   └── amazon/
    └── ...
```

### Ownership Model

| Directory     | Owned by | User access | Purpose |
|---------------|----------|-------------|---------|
| `.dwh/`       | dwh      | None        | Internal state, blobs, database |
| `inbox/`      | dwh      | Read/Write  | Ingestion drop zone |
| `originals/`  | dwh      | Read-only   | Browsable projection of classified documents |

Users interact with `inbox/` to add new documents and `originals/` to browse and access stored documents. The `originals/` tree can always be regenerated from `.dwh/` state.

### Blob Storage

Blobs are stored by content hash (SHA-256) in a sharded directory structure:

```
.dwh/store/blob/ab/cd/abcd1234567890...
```

This provides:
- Natural deduplication (identical files share storage)
- Integrity verification (hash is the address)
- Stable identity independent of classification

### Metadata Storage

Metadata is stored in SQLite (`dwh.db`), keyed by content hash:

```
hash ──1:1──▶ document ──1:N──▶ classifications
                       ──1:N──▶ placements
```

The hash is the join key between the filesystem blob store and the database:
- Blob existence check without database lookup (just check file exists)
- Database queries without filesystem traversal
- Integrity verification by comparing stored hash to actual file hash

### Database Schema (Core Entities)

**blobs** - Immutable file content
```
hash            TEXT PRIMARY KEY   -- SHA-256
size            INTEGER
mime_type       TEXT
ingested_at     TIMESTAMP
```

**documents** - Logical document records
```
id              TEXT PRIMARY KEY   -- UUID
blob_hash       TEXT REFERENCES blobs(hash)
original_name   TEXT               -- Filename at ingestion
source          TEXT               -- Where it came from
ingested_at     TIMESTAMP
state           TEXT               -- ingested|classified|published
```

**classifications** - Semantic metadata
```
document_id     TEXT REFERENCES documents(id)
domain          TEXT               -- finance, household, vehicles, ...
kind            TEXT               -- invoice, statement, contract, ...
counterparty    TEXT               -- Amazon, Comdirect, ...
year            INTEGER            -- Relevant tax/fiscal year
period_start    DATE
period_end      DATE
tags            TEXT               -- JSON array
confidence      REAL               -- 0.0-1.0
reviewed_at     TIMESTAMP
reviewed_by     TEXT               -- human|auto
```

**placements** - Filesystem projection mapping
```
document_id     TEXT REFERENCES documents(id)
path            TEXT               -- Relative path in originals/
is_primary      BOOLEAN            -- Primary location for this document
```

### Document Lifecycle

```
┌─────────┐  dwh inbox    ┌────────────┐  LLM/human   ┌─────────────────┐
│  inbox  │ ───store────▶ │   stored   │ ──  moves ─▶ │ originals/path  │
└─────────┘               └────────────┘              └─────────────────┘
     │                          │                            │
     │ (file stays)             │ (blob in .dwh)             │
     ▼                          ▼                            ▼
┌─────────┐               ┌────────────┐  dwh origs   ┌─────────────────┐
│  inbox  │               │   stored   │ ◀──capture── │   classified    │
└─────────┘               └────────────┘              └─────────────────┘
```

1. **Store**: `dwh inbox store` scans `inbox/`, computes content hashes, stores blobs in `.dwh/store/blob/`, creates document records. Files remain in `inbox/` until moved.

2. **Classify (LLM-assisted)**: Human or LLM moves files from `inbox/` into the `originals/` tree, choosing appropriate category paths. Classification is expressed through filesystem placement.

3. **Capture**: `dwh originals capture` scans `originals/`, matches files to stored blobs by content hash, infers classification metadata from the placement path, and updates the database. Prompts for confirmation (or `-y` to auto-confirm).

This "filesystem-first" approach means:
- Users express classification intent by placing files
- The tool learns from placement, not the other way around
- Metadata becomes authoritative only after capture

### Projection Strategy

The `originals/` directory is rendered from database state. Options for file materialization:

| Strategy | Implementation | Trade-offs |
|----------|----------------|------------|
| Symlinks | `originals/path → ../.dwh/store/blob/xx/yy/...` | Light, but fragile on moves |
| Hardlinks | Same inode as blob | Transparent, same-filesystem only |
| Copies | Duplicate bytes | Safe but wastes space |
| Reflinks | Copy-on-write (APFS/Btrfs) | Best of both, filesystem-dependent |

**Recommended for v1:** Hardlinks on macOS/APFS (same filesystem guaranteed within repository). Fall back to symlinks if hardlinks fail.

### Filename Convention in `originals/`

```
originals/{category}/{subcategory}/{date}_{description}_{short-id}.{ext}
```

Example:
```
originals/finance/amazon/2025-03-15_invoice_a1b2c3d4.pdf
originals/pile/2026/2026-03-28_unknown_document_e5f6g7h8.pdf
```

The short-id (first 8 chars of document UUID) ensures uniqueness and provides a lookup key.

### CLI Interface

```bash
# Initialization
dwh init .                    # Initialize warehouse in current directory

# Storage
dwh inbox store               # Scan inbox/, store blobs in .dwh/store/blob/
dwh inbox store --dry-run     # Show what would be stored

# Classification (filesystem-first)
# 1. LLM or human moves files from inbox/ to originals/{category}/
# 2. Then capture the placement:
dwh originals capture         # Scan originals/, match to stored blobs,
                              # infer metadata from paths, prompt y/n
dwh originals capture -y      # Auto-confirm without prompting

# Querying
dwh list                      # List documents
dwh list --state stored       # Filter by state
dwh list --domain finance     # Filter by classification
dwh show <id>                 # Show document details and metadata
dwh open <id>                 # Open document in default application

# Projection management
dwh originals sync            # Regenerate originals/ from metadata
dwh originals sync --dry-run  # Show what would change
dwh originals status          # Show drift between metadata and filesystem

# Search and export
dwh find <query>              # Search documents by metadata/content
dwh export --domain taxes --year 2025 --to ./tax-export/
```

### Capture Behavior

`dwh originals capture` scans the `originals/` tree and:

1. **Matches files to stored blobs** by content hash
2. **Infers classification** from the path (e.g., `finance/amazon/` → domain=finance, counterparty=amazon)
3. **Updates the database** with placement and inferred metadata
4. **Prompts for confirmation** before committing changes (unless `-y`)

**Constraints:**
- Only captures files that were pre-stored via `dwh inbox store` (by default)
- Use `--force` to store + capture unknown files in one step
- Files in `pile/` are skipped (pile uses dwh-managed stamping/renaming)
- Moving a file to a new location updates its placement metadata

### Transformation and Extraction (Out of Scope for v1)

Transformations (e.g., extracting CSV from PDFs, splitting documents) remain a separate concern:

- Consume classified documents from `originals/`
- Write outputs elsewhere (not managed by dwh)
- Use Make or similar for caching and dependencies
- May reference dwh documents by ID or path

This separation keeps dwh focused on storage, classification, and retrieval.

### Migration from Current Architecture

1. Existing `data/` content becomes initial `originals/` population
2. Import existing files into `.dwh/store/blob/` with provenance
3. Bootstrap classifications from current folder structure
4. Existing Git history preserved for provenance of legacy imports

## Consequences

### Positive

- **Stable document identity**: Documents have UUIDs independent of filesystem location.
- **Flexible classification**: Reclassify without moving files. Improve over time.
- **Multiple views**: Same document can appear in multiple `originals/` paths.
- **Query interface**: Find documents by metadata, not just browsing.
- **Robust ingestion**: Store first, classify later. No blocking triage.
- **Deduplication**: Content-addressed storage eliminates duplicates automatically.
- **Extraction caching**: OCR and text extraction are systematically managed.
- **Regenerable projections**: `originals/` can be rebuilt from `.dwh/` state.

### Negative

- **Added complexity**: Database + blob store vs. simple filesystem.
- **New tooling required**: `dwh` CLI must be built and maintained.
- **Learning curve**: Users must understand the inbox → classify → render flow.
- **Projection maintenance**: `originals/` can drift if `dwh render` is not run.
- **Git becomes secondary**: Version control shifts from primary to backup role.
- **Migration effort**: Existing archive must be imported into new structure.

### Risks

- **Database corruption**: SQLite is robust, but backups of `.dwh/dwh.db` are critical.
- **Blob/DB mismatch**: Orphaned blobs or dangling references require integrity checks.
- **Hardlink limitations**: May not work across filesystem boundaries or with some tools.

### Mitigations

- Regular `dwh verify` command to check blob/DB consistency
- SQLite WAL mode for crash safety
- Export capability for database state
- Keep Git as secondary backup layer

## References

- SQLite as application file format: https://sqlite.org/appfileformat.html
- Content-addressable storage: https://en.wikipedia.org/wiki/Content-addressable_storage
- ADR-001: Git-Based Storage (superseded for primary storage, retained for backup)
- ADR-004: Transformation Provenance (still applicable for derived artifacts)
