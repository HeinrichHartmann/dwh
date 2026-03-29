# ADR-003: Data Model

**Status:** Proposed
**Date:** 2026-03-29

## Context

The warehouse needs a data model that captures:

1. **Content** - The actual bytes (deduplicated)
2. **Provenance** - Import events with context (who, when, why)
3. **Occurrences** - Each sighting of content within an import
4. **Classification** - Semantic metadata (domain, year, counterparty)
5. **Projection** - Filesystem placement in archive/

Previous iterations conflated "entry" (occurrence) with "document" (semantic grouping), leading to confusion.

## Decision

### Entity Model

```
┌─────────┐       ┌─────────┐       ┌─────────┐
│  Blob   │◀──────│  Entry  │──────▶│  Drop   │
│ (bytes) │  N:1  │(occur.) │  N:1  │(import) │
└─────────┘       └─────────┘       └─────────┘
                       │
                       │ 1:N
                       ▼
                 ┌───────────┐
                 │ Placement │
                 └───────────┘
```

### Entities

#### Blob

Content-addressed immutable bytes.

| Field | Type | Description |
|-------|------|-------------|
| `hash` | TEXT PK | SHA-256 hash (content address) |
| `size` | INTEGER | Size in bytes |
| `mime_type` | TEXT | MIME type |
| `stored_at` | TIMESTAMP | When first stored |

A blob is just bytes. It has no opinion about meaning, filename, or purpose. The same PDF imported three times is one blob.

#### Drop

An import event. The unit of provenance.

| Field | Type | Description |
|-------|------|-------------|
| `id` | TEXT PK | Drop identifier (e.g., `d_20260329_143211_a1b2`) |
| `message` | TEXT NOT NULL | Why this import happened |
| `actor` | TEXT NOT NULL | Who performed the import |
| `created_at` | TIMESTAMP | When the import occurred |

Every file enters the warehouse inside a drop. Drops are immutable once created.

#### Entry

One occurrence of a blob within a drop. **The central entity.**

| Field | Type | Description |
|-------|------|-------------|
| `id` | TEXT PK | Entry identifier |
| `drop_id` | TEXT FK | Which drop this entry belongs to |
| `blob_hash` | TEXT FK | Which blob this entry references |
| `original_filename` | TEXT | Filename at source |
| `source_path` | TEXT | Full path where file was found |
| `relative_path` | TEXT | Path relative to drop root |
| `created_at` | TIMESTAMP | When entry was created |

An entry answers: "This blob appeared in this drop, with this filename, from this location."

Importing the same file twice creates two entries (in two drops) pointing to one blob.

#### Placement

Filesystem projection in archive/.

| Field | Type | Description |
|-------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `entry_id` | TEXT FK | Which entry this places |
| `archive_path` | TEXT | Path in archive/ tree |
| `is_primary` | BOOLEAN | Primary placement flag |
| `created_at` | TIMESTAMP | When placed |

Placements are derived from classification. They can be regenerated from metadata.

### Three Path Types

These must remain distinct:

| Path | Stored In | Example | Meaning |
|------|-----------|---------|---------|
| **source_path** | `entry.source_path` | `/Users/h/Downloads/stmt.pdf` | Where file came from |
| **relative_path** | `entry.relative_path` | `finance/comdirect/stmt.pdf` | Structure within import |
| **archive_path** | `placement.archive_path` | `archive/finance/2024/stmt.pdf` | Where it lives in warehouse |

### Classification

Classification is metadata on entries, stored as columns or in a related table.

**Option A: Columns on Entry**
```sql
-- Add to entries table
domain TEXT,           -- 'finance', 'legal', 'medical'
kind TEXT,             -- 'statement', 'invoice', 'contract'
counterparty TEXT,     -- 'comdirect', 'amazon'
year INTEGER,          -- 2024
period_start DATE,
period_end DATE,
tags TEXT,             -- JSON array
```

**Option B: Separate Table** (more flexible, supports history)
```sql
CREATE TABLE classifications (
    id INTEGER PRIMARY KEY,
    entry_id TEXT REFERENCES entries(id),
    domain TEXT,
    kind TEXT,
    counterparty TEXT,
    year INTEGER,
    confidence REAL DEFAULT 1.0,
    source TEXT CHECK(source IN ('human', 'auto', 'inferred')),
    created_at TIMESTAMP
);
```

**Recommendation:** Option B. Classification can evolve; keeping history is valuable.

### What About "Document"?

**Deferred to v2.**

A Document would group entries that represent the same logical thing (e.g., multiple scans of the same invoice, or versions of a contract).

For v1, Entry is sufficient. Each import occurrence stands alone.

## Schema

```sql
-- Blobs: Content-addressed immutable bytes
CREATE TABLE blobs (
    hash TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mime_type TEXT,
    stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Drops: Import events
CREATE TABLE drops (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Entries: Occurrences of blobs within drops
CREATE TABLE entries (
    id TEXT PRIMARY KEY,
    drop_id TEXT NOT NULL REFERENCES drops(id),
    blob_hash TEXT NOT NULL REFERENCES blobs(hash),
    original_filename TEXT NOT NULL,
    source_path TEXT NOT NULL,
    relative_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Classifications: Semantic metadata on entries
CREATE TABLE classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL REFERENCES entries(id),
    domain TEXT,
    kind TEXT,
    counterparty TEXT,
    year INTEGER,
    period_start DATE,
    period_end DATE,
    tags TEXT,  -- JSON array
    confidence REAL DEFAULT 1.0 CHECK(confidence BETWEEN 0.0 AND 1.0),
    source TEXT CHECK(source IN ('human', 'auto', 'inferred')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Placements: Filesystem projection
CREATE TABLE placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL REFERENCES entries(id),
    archive_path TEXT NOT NULL,
    is_primary BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entry_id, archive_path)
);

-- Indexes
CREATE INDEX idx_entries_drop_id ON entries(drop_id);
CREATE INDEX idx_entries_blob_hash ON entries(blob_hash);
CREATE INDEX idx_classifications_entry_id ON classifications(entry_id);
CREATE INDEX idx_classifications_domain ON classifications(domain);
CREATE INDEX idx_classifications_year ON classifications(year);
CREATE INDEX idx_placements_entry_id ON placements(entry_id);
CREATE INDEX idx_placements_archive_path ON placements(archive_path);
```

## Identifier Format

| Entity | Format | Example |
|--------|--------|---------|
| Drop | `d_{date}_{time}_{hash4}` | `d_20260329_143211_a1b2` |
| Entry | `e_{hash8}` | `e_8f3c2a1b` |
| Blob | SHA-256 hash | `a1b2c3d4...` (64 chars) |

## Consequences

### Positive

- **Entry is first-class** - Queries center on occurrences, not content
- **Three paths are distinct** - No conflation of source, relative, archive
- **Drop structure preserved** - `relative_path` captures import tree structure
- **Classification history** - Changes tracked in separate table
- **Naming matches design** - Entry, Drop, Blob align with ADR-001

### Negative

- **More tables** - 5 tables instead of 3
- **More joins** - Entry-centric queries need joins to Drop and Blob
- **Migration needed** - Existing schema must be replaced

### Trade-offs

Complexity is increased, but the model correctly captures the domain. "Occurrence within an import" is the fundamental concept; the schema now reflects this.

## Migration

For a fresh start:

```bash
rm .dwh/dwh.db
dwh init .
```

For existing data: migration script needed (not in v1 scope).

## References

- ADR-001: Drop-Based Archival with Provenance
- ADR-002: Metadata is Canonical
- DWH-DESIGN.md Section 5: Data Model
