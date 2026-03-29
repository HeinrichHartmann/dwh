# Architecture Decision Records (ADR)

This directory contains Architecture Decision Records for the Document Warehouse project.

## What is an ADR?

An Architecture Decision Record captures an important architectural decision made along with its context and consequences.

## Format

Each ADR follows this structure:

```markdown
# ADR-NNN: Title

**Status:** [Proposed | Accepted | Deprecated | Superseded]
**Date:** YYYY-MM-DD
**Deciders:** [Names/Roles]

## Context

What is the issue we're facing? What factors influence this decision?

## Decision

What is the change we're making?

## Consequences

What becomes easier or more difficult as a result of this decision?

### Positive
- Benefit 1
- Benefit 2

### Negative
- Trade-off 1
- Trade-off 2
```

## Index

- [ADR-001](001-git-based-storage.md) - Git-Based Storage for Document Versioning
- [ADR-002](002-source-based-organization.md) - Source-Based Organization (Not Tax-Year)
- [ADR-003](003-human-llm-triage.md) - Human-LLM Collaborative Triage Workflow
- [ADR-004](004-git-transformation-provenance.md) - Git-Based Provenance for Transformations
- [ADR-005](005-git-snapshot-import-provenance.md) - Git Snapshot Provenance for Imports
- [ADR-006](006-metadata-centric-document-warehouse.md) - Metadata-Centric Document Warehouse Architecture

## References

- [ADR Template](https://github.com/joelparkerhenderson/architecture-decision-record)
- [When to write an ADR](https://adr.github.io/)
