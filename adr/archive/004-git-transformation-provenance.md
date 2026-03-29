# ADR-004: Git-Based Provenance for Transformations

**Status:** Accepted
**Date:** 2026-03-27
**Deciders:** Repository maintainer

## Context

This repository stores original financial documents and will increasingly contain derived artifacts such as extracted CSV files, normalized tables, splits, and cleaned exports.

The central provenance requirement is:

- For every file, it must be possible to determine where it came from.

The repository already uses Git for versioning. Instead of introducing a separate provenance database, provenance should be attached to the transformation event itself and recovered through Git history.

This requires a strict transformation protocol so that:

- every derived file is created in a traceable commit
- every transformation has an explicit machine-readable description
- provenance can be recovered by starting from a file path and following Git history

## Decision

We will use **Git commits as the provenance record for transformations**.

### Transformation Protocol

All transformations must follow these rules:

1. Transformations happen on a clean worktree.
2. Transformation scripts are always checked in under `./scripts`.
3. Transformation scripts must already be committed before the transformation is run.
4. Transformation outputs are committed in the same commit that records the transformation provenance.
5. A transformation commit may only contain changed or newly added output files.
6. Inputs and scripts must not be modified in a transformation commit.
7. Each transformation commit must have a standard YAML-formatted commit message body.
8. Each transformation commit must be atomic with respect to provenance:
   - one transformation step per commit
   - the commit contains only the resulting derived artifacts for that step

### Scope

This protocol applies to commits that create or materially modify derived artifacts, for example:

- extracting CSV or JSON from `Finanzreport` PDFs
- normalizing or cleaning imported CSV files
- splitting one source file into multiple output files
- converting exports into a repository-standard format

This protocol does not need to be used for simple reorganizations such as renames or folder moves that do not transform content.

Imports of original source files are out of scope for this ADR and are covered by ADR-005.

### Required Commit Message Structure

Each transformation commit must use:

- a short subject line
- a blank line
- a YAML document in the body

Recommended subject line format:

```text
transform: <short description>
```

The helper command for this protocol is:

```text
python3 scripts/transform.py commit ...
```

It is responsible for:

- rendering the YAML commit message body
- validating that only declared output files are changed or newly added
- rejecting modified inputs or modified scripts
- staging the validated outputs
- creating the Git commit

Typical usage:

```text
python3 scripts/transform.py commit \
  --step finanzreport_extract_csv \
  --script scripts/extract_finanzreport.py \
  --input data/finance/Comdirect-8862-Private/PostBox/Finanzreport/Finanzreport_Nr._12_per_31.12.2025_F5E911.pdf \
  --output data/finance/Comdirect-8862-Private/Derived/Finanzreport/2025-12-transactions.csv \
  --notes "Extract transaction table from the December 2025 Finanzreport."
```

This command does not run the transformation itself. It is used after the transformation has been performed to validate the worktree, stage the declared outputs, and create the provenance commit.

Required YAML fields:

```yaml
type: transformation
step: finanzreport_extract_csv
script: scripts/extract_finanzreport.py
inputs:
  - data/finance/Comdirect-8862-Private/PostBox/Finanzreport/Finanzreport_Nr._12_per_31.12.2025_F5E911.pdf
outputs:
  - data/finance/Comdirect-8862-Private/Derived/Finanzreport/2025-12-transactions.csv
notes: Extract transaction table from monthly financial report PDF.
```

Optional YAML fields:

```yaml
tool_version: v1
params:
  report_type: finanzreport
  account: comdirect-8862-private
source_commit: <commit-sha-if-needed>
```

### Commit Message Example

```text
transform: extract 2025-12 Finanzreport CSV

type: transformation
step: finanzreport_extract_csv
script: scripts/extract_finanzreport.py
inputs:
  - data/finance/Comdirect-8862-Private/PostBox/Finanzreport/Finanzreport_Nr._12_per_31.12.2025_F5E911.pdf
outputs:
  - data/finance/Comdirect-8862-Private/Derived/Finanzreport/2025-12-transactions.csv
notes: Extract transaction table from the December 2025 Finanzreport.
```

### Provenance Lookup Protocol

To determine where a file came from:

1. Start with the file path.
2. Run `git log --follow -- <path>`.
3. Find the commit that created the file or last materially transformed it.
4. Read the YAML body of that commit message.
5. Inspect the `inputs` field to identify the immediate source files.
6. Repeat the lookup process on each input if deeper lineage is needed.

This makes provenance recursive:

- file -> transformation commit
- transformation commit -> input files
- input files -> earlier transformation commits or original source files

### Original Source Files

Original imported files should not use the transformation schema unless they are themselves produced by a repository transformation.

Original source provenance is established by:

- the import protocol defined in ADR-005
- the import commit that introduced the file into `data/`
- the committed import manifest referenced by that commit

## Consequences

### Positive

- Provenance is attached to the transformation itself, not to an external registry.
- Every derived file can be traced backward through Git history.
- The protocol remains simple and compatible with a cold-storage repository.
- Human-readable and machine-readable provenance are stored together.
- No separate database or service is required.

### Negative

- The workflow becomes stricter: transformations must start from a clean worktree.
- Commit discipline is required; mixed-purpose commits weaken provenance.
- Scripts must be committed separately before running a transformation.
- Provenance lookup depends on consistent commit-message formatting.
- Large multi-output transformations may produce verbose commit messages.
- Manual edits to derived files outside the protocol create provenance gaps.
