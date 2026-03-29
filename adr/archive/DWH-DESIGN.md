# DWH Design Document

**Status:** Active
**Date:** 2026-03-29
**Supersedes:** adr/006-metadata-centric-document-warehouse.md (refined thinking)

## 1. Purpose

DWH (Document Warehouse) is a durable document warehouse for archiving files and file drops with strong provenance, readable filesystem representation, and metadata/classification support.

**Target use cases:**
- Personal document archiving
- Tax document collection
- Household/admin records
- Business record archiving
- Long-lived PDF and blob storage

DWH is not just a folder tree and not just a database. It is a storage tool with a CLI and a filesystem-facing interface.

## 2. Core Design Principle

**Raw permanence, semantic malleability.**

- Never lose or rewrite the original blob
- Allow extraction, OCR, classification, and folder placement to improve over time
- Metadata is canonical truth; filesystem paths are derived projections

## 3. Architecture

### 3.1 Layer Model

The warehouse operates in four distinct layers:

| Layer | Purpose | Persistence |
|-------|---------|-------------|
| **1. Raw blob persistence** | Immutable original files | Always persisted |
| **2. Extraction layer** | OCR, text extraction, metadata detection | Cached when expensive |
| **3. Semantic metadata layer** | Classification, categories, tags, confidence | Persisted in DB |
| **4. Filesystem projection layer** | Readable archive in `archive/` | Derived/regenerable |

### 3.2 Store Scope

**A DWH store is folder-local and self-contained.**

- A user may own multiple warehouses for different accountability domains
- Each warehouse is portable, syncable, backupable
- Stores are not machine-global or user-singleton

Example structure:
```
~/warehouse/personal/
~/warehouse/hartmann-it/
~/warehouse/family/
```

### 3.3 Per-Store Layout

```
<store>/
├── inbox/          # Optional intake surface (secondary)
├── archive/        # Published archive view (treat as read-only)
│   ├── pile/       # Default catch-all category
│   ├── finance/
│   └── ...
└── .dwh/           # Internal state (owned entirely by dwh)
    ├── dwh.db      # SQLite database
    ├── config.toml # Configuration
    └── blobs/      # Content-addressed storage
        └── ab/cd/abcd...
```

## 4. Core Promises

### 4.1 Durability

If a file or drop is successfully accepted by DWH, it will not be silently lost.

- Accepted content is durably archived
- The system records that acceptance
- The user receives a stable drop ID / receipt
- Retrieval by that ID remains possible later

### 4.2 Provenance

DWH archives content as **drops** (import packages).

A drop is a first-class import event with provenance:
- When it was imported
- Into which store
- From what source
- With what note/reason
- Which files were included
- What archive outcome resulted

**Critical design rule:** The system must not skip provenance just because content is already known. Repeated imports of identical files must still be represented as occurrences within distinct drops.

### 4.4 Open Classification

Classification and refinement should be open to external tooling, including LLMs.

- Classify through the CLI
- Classify through filesystem actions
- Use external LLM tooling directly on the archive representation
- Improve categorization over time without rewriting the archival core

## 5. Data Model

### 5.1 Key Insight

**Most metadata does not belong to the blob. It belongs to the occurrence of the blob inside a drop.**

A blob is just content (bytes, hash, size, MIME type). Everything else is about a sighting or occurrence:
- Filename
- Relative path
- Source path
- Import time
- Package/drop membership
- Reason for import
- Category hint
- Placement decision

### 5.2 Entity Model

```
┌─────────┐       ┌─────────┐       ┌─────────┐
│  Blob   │◀──────│  Entry  │──────▶│  Drop   │
│ (bytes) │  N:1  │(occur.) │  N:1  │(package)│
└─────────┘       └─────────┘       └─────────┘
                       │
                       │ N:1
                       ▼
                 ┌───────────┐
                 │ Document  │  (optional grouping)
                 └───────────┘
```

**Blob** - Content-addressed bytes
- `blob_id` (hash)
- `size`
- `mime_type`
- Storage location in CAS

**Drop** - Import package/deposit
- `drop_id`
- `timestamp`
- `actor`
- `message/note`
- `store`
- `import_mode` (copy/move)

**Entry** - One item inside a drop
- `entry_id`
- `drop_id` (FK)
- `blob_id` (FK)
- `original_filename`
- `relative_path` (within drop)
- `source_path`
- `archive_path` (placement)
- Classification fields

**Document** - Logical semantic grouping (optional, for v2)
- Groups entries that represent the same logical document
- Enables version/revision tracking

### 5.3 Three Types of Paths

| Path Type | Meaning | Example |
|-----------|---------|---------|
| Source path | Where file came from | `~/Downloads/invoice.pdf` |
| Relative path | Structure within drop | `march/invoice.pdf` |
| Archive path | Warehouse placement | `archive/finance/amazon/...` |

These must remain distinct. Do not collapse them.

## 6. Primary Workflow

### 6.1 Import (Primary Flow)

```bash
dwh store import -m "message" <paths...>
```

**Not inbox-based.** The primary ingestion is CLI import, not a watched folder.

Expected behavior:
1. Accept one or more files/directories
2. Archive them into the target store
3. Create a durable drop record
4. Return a stable drop ID / receipt
5. Preserve provenance (source paths, timestamps, actor)

**Copy is the default** - files remain at source location.

### 6.2 Import Receipt

Every import produces a receipt:

```
Import: imp_20260329_8f3c
Store: personal
Time: 2026-03-29T14:32:11+01:00
Mode: copy
Sources:
  - ~/Downloads/amazon-march.pdf
  - ~/Desktop/tax/receipts/
Imported: 12 files
Message: 2025 tax intake batch
```

This answers "why is this here?" for every archived file.

### 6.3 Classification Flow

Classification happens **after** storage:

1. Files enter via import
2. Initially placed in `archive/pile/` (catch-all)
3. Human or LLM moves files to category paths
4. `dwh sync` records the classification

**Filesystem-assisted classification:** Moving a file to `archive/finance/amazon/` is a classification act that dwh captures.

## 7. CLI Interface

### 7.1 Core Commands

```bash
# Initialization
dwh init .                              # Initialize warehouse

# Storage
dwh store import -m "msg" <paths...>    # Import files with provenance

# Archive sync
dwh sync                                # Reconcile archive/ edits into metadata

# Querying
dwh list                                # List documents
dwh show <id>                           # Show document details
dwh receipt <drop-id>                   # Show import receipt
dwh find <query>                        # Search documents
```

### 7.2 Import Semantics

```bash
# Basic import
dwh store import -m "Bank statements Q1" ~/Downloads/statements/

# Multiple sources
dwh store import -m "Tax docs" file1.pdf file2.pdf folder/

# All paths are recursively scanned
```

## 8. Storage Model Comparison

The design draws from multiple storage model inspirations:

| Model | Strengths for DWH | Weaknesses for DWH |
|-------|-------------------|-------------------|
| **Plain filesystem** | Readable, tool-independent | Weak provenance, no first-class drops |
| **Zip-per-drop** | Strong drop semantics | Poor browsability |
| **Git-like** | Content-addressed, strong provenance | Tree-snapshot oriented, not document-native |
| **S3-like** | Simple durability | Key-centric identity, weak occurrence model |
| **restic-like** | Best structural fit: snapshots over blobs | Backup-oriented, not classification-native |

### 8.1 Chosen Approach

**Git/restic inside, filesystem outside.**

- Internal model: Content-addressed blobs with drop-based provenance
- External interface: Readable filesystem archive in `archive/`
- Best of both: Strong provenance with human-readable transparency

## 9. Functional Requirements

### 9.1 Import and Archival

| ID | Requirement |
|----|-------------|
| FR-1 | Primary CLI import (`dwh store import`) |
| FR-2 | Drop-based archival with receipt |
| FR-3 | Stable drop ID for later retrieval |
| FR-4 | Copy-by-default (preserve source files) |
| FR-5 | Provenance capture (who, when, why, from where) |
| FR-6 | Support bulk imports (files, folders, trees) |

### 9.2 Durability Contract

| ID | Requirement |
|----|-------------|
| FR-7 | Accepted content is durably persisted |
| FR-8 | Retrieval by drop ID always possible |
| FR-9 | No silent discard of duplicate occurrences |

### 9.3 Filesystem Transparency

| ID | Requirement |
|----|-------------|
| FR-10 | Readable archive in `archive/` |
| FR-11 | Tool-independent discoverability |
| FR-12 | Human-meaningful category layout |
| FR-13 | PDFs accessible as actual files |

### 9.4 Classification

| ID | Requirement |
|----|-------------|
| FR-14 | Classification is first-class metadata |
| FR-15 | Classification does not block archival |
| FR-16 | External tools (LLMs) can refine classification |
| FR-17 | Filesystem placement is a classification mechanism |
| FR-18 | Classification can evolve over time |

## 10. Non-Goals

DWH is **not** intended to be:

- A workflow orchestrator
- A general transformation engine
- A full backup replacement
- A distributed cloud-native storage platform
- A hidden database with no human-readable archive

**Derivatives are a separate layer.** Transformations, downstream processing, reporting, and custom workflows may consume DWH but are not part of DWH itself.

## 11. v1 Scope

### 11.1 Included

- One or more local stores
- CLI import as primary workflow
- Copy-based archival
- Durable drop IDs / receipts
- Visible archive of originals
- Provenance recording (drop + entry level)
- Basic classification support
- Readable filesystem layout
- SQLite + local filesystem implementation

### 11.2 Deferred to v2+

- Content-addressable deduplication
- Advanced filesystem projections (symlinks, FUSE)
- Import watchers (Downloads, email)
- Email/scanner integrations
- Richer LLM workflows
- Advanced querying and history browsing
- restic integration for backup/dedup

## 12. Implementation Notes

### 12.1 Technology Stack

- **Language:** Python
- **CLI:** Click
- **Database:** SQLite
- **Storage:** Local filesystem (content-addressed)
- **Package manager:** uv

### 12.2 Database Schema

See `src/dwh/src/dwh/db.py` for current implementation:

- `blobs` - Content-addressed storage records
- `documents` - Document records with state
- `classifications` - Semantic metadata
- `placements` - Filesystem projection mapping
- `imports` - Drop/import transaction records
- `import_files` - Links documents to import transactions

### 12.3 Key Design Decisions

1. **Import requires a message** - Every import must have provenance context
2. **Recursive scanning** - Directories are scanned recursively by default
3. **Blob-level deduplication** - Same content stored once, multiple entries
4. **Transactional imports** - All files in one import share a transaction ID

## 13. Design Rationale

### Why not just folders?

Folders conflate identity with placement. Renaming changes meaning. Cross-cutting queries are impossible.

### Why not just a database?

Databases are opaque. Recovery requires specific tools. LLMs can't browse directly.

### Why drops instead of file-by-file?

Provenance is about intent. "I imported these tax documents together in March" is meaningful. Individual file timestamps are not.

### Why copy, not move?

Users often want to keep files where they are. Archival should not require reorganization of source systems.
