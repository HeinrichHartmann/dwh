# ADR-002: Metadata is Canonical

**Status:** Accepted
**Date:** 2026-03-29

## Context

Document systems typically treat the filesystem as truth. Files live in folders; the folder structure *is* the organization.

This breaks down when you want:
- **Node-local retention** - Laptop keeps 5 years, server keeps forever
- **Replication** - Content spread across tiers (local SSD, NAS, cloud)
- **Multiple views** - Same content, different projections

If the filesystem is truth, you can't have partial local copies. Everything must be present or the "archive" is incomplete.

## Decision

**Metadata is canonical. Filesystems are projections.**

The warehouse consists of:

1. **Blob store** - Content-addressed immutable bytes
2. **Metadata store** - SQLite database with drops, entries, provenance, classification, placements
3. **Projections** - Filesystem views derived from metadata (`drops/`, `archive/`)

### What this means

**Canonical truth:**
- Metadata database
- Blob existence (by hash)

**Derived/rebuildable:**
- `archive/` directory
- `drops/` directory
- Any filesystem representation

### Projections are node-local

Each node materializes a subset based on retention policy:

```toml
[drops_projection]
retain_days = 30

[archive_projection]
retain_years = 5
```

Old content remains in metadata. Blobs may be remote. Local filesystem shows only the retained working set.

### Reconstruction, not browsing

For content beyond local retention:

```bash
dwh export d_2026-03-28_143211_8f3c ./restore/
```

This fetches blobs if needed and reconstructs the drop. The filesystem is not a guarantee of presence; `dwh export` is.

## Consequences

### Positive

- **Node-local retention works** - Laptops can have partial archives
- **Replication works** - Blobs can live anywhere; metadata knows what exists
- **Multiple projections possible** - Same content, different views
- **Clean separation** - Storage, metadata, and presentation are decoupled

### Negative

- **No guaranteed browsability** - Can't promise "open Finder, see everything"
- **Tool dependency for full access** - `dwh export` required for non-local content
- **More complex mental model** - Users must understand projections vs. canonical store

### Trade-offs

The design prioritizes **flexibility and scalability over filesystem-as-truth simplicity**.

For users who want everything locally browsable: set retention to `forever` and ensure all blobs are local. The system supports this but doesn't require it.

## Relationship to SQLite

SQLite is the metadata store because:
- Self-contained, portable file
- No server process
- Inspectable with standard tools (`sqlite3 dwh.db .schema`)
- Embeddable in nearly every platform

The database file itself is transparent and recoverable. The *content* it describes may or may not be locally present.

## References

- Design rationale: `dwh-design.md` (retention and replication discussion)
- Comparison: lakeFS, Delta Lake - metadata over object storage
- Comparison: Git - object store + metadata, not filesystem-as-truth
