# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for DWH.

## Active ADRs

| ADR | Title | Status |
|-----|-------|--------|
| [001](001-drop-based-archival.md) | Drop-Based Archival with Provenance | Accepted |
| [002](002-metadata-canonical.md) | Metadata is Canonical | Accepted |
| [003](003-data-model.md) | Data Model | Proposed |

## Design Documents

| Document | Purpose |
|----------|---------|
| [DWH-DESIGN](DWH-DESIGN.md) | Comprehensive design document |

## Archived ADRs

Historical ADRs from earlier design iterations are preserved in `archive/`.

These represent superseded decisions from when the system was filesystem-centric rather than metadata-centric.

## ADR Format

Each ADR follows this structure:

```markdown
# ADR-NNN: Title

**Status:** Proposed | Accepted | Deprecated | Superseded
**Date:** YYYY-MM-DD

## Context
Why is this decision needed?

## Decision
What is the decision?

## Consequences
What are the results?
```
