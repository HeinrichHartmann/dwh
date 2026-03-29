# ADR-001: Drop-Based Archival with Provenance

**Status:** Accepted
**Date:** 2026-03-29

## Context

Document archives typically model storage as "files with metadata attached." This creates problems:

1. **No import context** - Files exist, but why? When did they arrive? As part of what?
2. **Silent duplicates** - Same file imported twice looks identical. Which import matters?
3. **Lost provenance** - Source paths, import reasons, and actor information are not captured.
4. **Blanket imports** - Bulk operations leave no trace of intent.

The fundamental question an archive must answer is not just "what do we have?" but "why is this here?"

## Decision

**The primary archival unit is the drop, not the file.**

A drop (also called import package or deposit) is a first-class object representing one import event. Every file enters the warehouse as part of a drop.

### Drop Structure

```
Drop
├── drop_id         (stable identifier)
├── timestamp       (when)
├── actor           (who)
├── message         (why)
├── source_paths    (from where)
└── entries[]       (what)
    ├── blob_id
    ├── original_filename
    ├── relative_path
    └── source_path
```

### Key Properties

1. **Every import creates a drop** - No file enters without an associated import event.

2. **Drops require a message** - The `-m` flag is mandatory, forcing the user to state intent.
   ```bash
   dwh import -m "Bank statements Q1 2025" ~/Downloads/statements/
   ```

3. **Drops are immutable** - Once recorded, a drop's contents and metadata cannot be changed.

4. **Files are entries within drops** - A file's existence is always tied to its arrival context.

5. **Same content, multiple entries** - Importing the same file twice creates two entries in two drops. Both are recorded. Neither is silently skipped.

### Receipt Contract

Every successful import returns a receipt:

```
Drop: d_2026-03-29_143211_a1b2
Time: 2026-03-29T14:32:11+01:00
Actor: hhartmann
Message: Bank statements Q1 2025
Sources:
  - ~/Downloads/statements/january.pdf
  - ~/Downloads/statements/february.pdf
  - ~/Downloads/statements/march.pdf
Imported: 3 files
```

This receipt is the user's claim ticket. It answers "what happened?" for every import.

### Export Contract

Any drop can be reconstructed:

```bash
dwh export d_2026-03-29_143211_a1b2 ./restore/
```

This is the real trust mechanism: what you import, you can later export. The drop ID is both a receipt and a retrieval key.

### Data Model Implication

The entity hierarchy is:

```
Blob (content)  ◀──  Entry (occurrence)  ──▶  Drop (import event)
```

- **Blob**: Raw bytes, content-addressed by hash. Metadata-poor.
- **Entry**: One occurrence of a blob within a drop. Carries filename, paths, classification.
- **Drop**: The import event. Carries timestamp, actor, message, provenance.

Most metadata belongs to **entries**, not blobs. The same PDF appearing in three drops has three entries with potentially different contexts.

## Consequences

### Positive

- **Full provenance** - Every file can answer: when, who, why, from where.
- **Audit trail** - Import history is explicit and queryable.
- **Duplicate handling** - Same content imported twice is two events, both recorded.
- **Intent capture** - Import messages document purpose at the moment of archival.
- **Retrieval by import** - "Show me everything from that tax import" is a valid query.

### Negative

- **Mandatory friction** - Users must provide a message for every import. This is intentional.
- **Storage overhead** - Entry records exist even for duplicate content.
- **No silent dedup** - System tracks all occurrences, not just unique content.

### Trade-offs

The design prioritizes **provenance over convenience**. A blanket `dwh import .` without context is deliberately unsupported. This friction is a feature: it ensures the archive remains self-documenting.

## Implementation

```python
# CLI requires message
@click.option("-m", "--message", required=True)
def import_cmd(message, paths):
    ...

# Database schema
CREATE TABLE imports (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    username TEXT NOT NULL,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE import_files (
    import_id TEXT REFERENCES imports(id),
    document_id TEXT REFERENCES documents(id),
    original_path TEXT NOT NULL,
    PRIMARY KEY (import_id, document_id)
);
```

## References

- Design rationale: `adr/DWH-DESIGN.md` Section 5 (Data Model)
- Comparison to Git commits: Git's commit object similarly groups changes with message and author
- Comparison to restic snapshots: restic's snapshot object groups backed-up files with timestamp and metadata
