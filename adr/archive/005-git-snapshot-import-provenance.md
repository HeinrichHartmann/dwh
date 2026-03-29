# ADR-005: Git Snapshot Provenance for Imports

**Status:** Accepted
**Date:** 2026-03-27
**Deciders:** Repository maintainer

## Context

Documents do not originate inside this repository. They arrive as external files and are then classified into final archive locations under `data/`.

This import step is not a pure transformation:

- files start outside Git
- staging may exist, but it is not the provenance record
- the main operation is classification and promotion into the archive
- the classification is done collaboratively by the human and the LLM, guided by existing examples and local README files

The provenance requirement is still the same:

- For every archived file, it must be possible to determine where it came from.

Git history can still serve as the provenance record if the import event snapshots the archive state that was created by the classification step and records the source context in a structured way.

## Decision

We will use **structured import commits plus committed import manifests** as the provenance record for imports of original source files.

### Import Protocol

All imports must follow these rules:

1. Source files may first be inspected in `staging/` or any other scratch location.
2. Scratch locations are not the provenance record and should remain out of Git.
3. Import classification is performed collaboratively by the human and the LLM.
4. The human or the LLM-assisted workflow places the files into their final archive paths under `data/` before the import helper is run.
5. Imports happen on a clean or import-focused worktree.
6. The import commit may contain only:
   - archive files under `data/`
   - one committed import manifest
7. The import commit must not contain unrelated edits.
8. `scripts/import.py commit` validates the declared archive paths, writes the manifest, stages the validated files, and creates the Git commit.
9. `scripts/import.py` does not move, copy, or rename files.

### Manifest Requirement

Each import commit must reference a committed manifest file.

Default location:

```text
metadata/imports/<date>-<slug>.yaml
```

The manifest captures the import context for the archive snapshot:

- the declared archive paths that were imported
- the full inventory of visible files under those paths
- checksums and sizes for those archived files
- optional source context such as a staging drop name or external export note
- the classification basis used by the human and LLM

### Required Commit Message Structure

Each import commit must use:

- a short subject line
- a blank line
- a YAML document in the body

Recommended subject line format:

```text
import: <short description>
```

Required YAML fields:

```yaml
type: import
mode: snapshot
tool: scripts/import.py
paths:
  - data/finance/Sparkasse-2927-Hartmann-IT
manifest: metadata/imports/2026-03-27-kontoauszuege-hartmannit-sparda.yaml
sources:
  - staging/Kontoauszüge-HartmannIT-Sparda
notes: Promote monthly Hartmann IT bank statements into the finance archive.
```

Optional YAML fields:

```yaml
sources:
  - existing local archive before git initialization
```

### Helper Command

The helper command for this protocol is:

```text
python3 scripts/import.py commit ...
```

It is responsible for:

- generating the import manifest
- rendering the YAML commit message body
- validating that only declared archive paths plus the manifest are changed or newly added
- rejecting unrelated worktree changes
- staging the declared archive paths and the manifest
- creating the Git commit

Typical usage:

```text
python3 scripts/import.py commit \
  --path data/finance/Sparkasse-2927-Hartmann-IT \
  --source staging/Kontoauszüge-HartmannIT-Sparda \
  --basis "bank name verified via pdftotext" \
  --basis "folder naming chosen from existing finance source conventions" \
  --notes "Promote Sparkasse Hartmann IT bank statements from staging into the archive."
```

This command does not perform the import itself. It is run after the files have already been classified and placed into their archive paths.

### Manifest Shape

The committed manifest must include:

```yaml
type: import-manifest
generated_by: scripts/import.py
mode: snapshot
actor_mode: human-llm
paths:
  - data/finance/Sparkasse-2927-Hartmann-IT
sources:
  - staging/Kontoauszüge-HartmannIT-Sparda
classification_basis:
  - bank name verified via pdftotext
files:
  - path: data/finance/Sparkasse-2927-Hartmann-IT/Kontoauszuege/2025/Konto_0097002927-Auszug_2025_0001.PDF
    sha256: ...
    size: 12345
notes: Promote Sparkasse Hartmann IT bank statements from staging into the archive.
```

### Provenance Lookup Protocol

To determine where an imported file came from:

1. Start with the archived file path.
2. Run `git log --follow -- <path>`.
3. Find the import commit that introduced the file.
4. Read the YAML body of that commit message.
5. Open the referenced manifest.
6. Inspect `paths`, `files`, `sources`, and `classification_basis`.

This establishes provenance for original archived files even when their pre-import existence was outside Git.

## Consequences

### Positive

- Import provenance is explicit even when source files start outside Git.
- The collaborative human-LLM classification step is recorded.
- The helper separates file placement from provenance capture.
- Large imports remain reviewable because the detailed file inventory lives in a committed manifest instead of only the commit body.

### Negative

- Imports now require both a structured commit and a manifest file.
- The workflow becomes stricter because import commits must stay atomic.
- Source context is only as good as what is written into the manifest and commit body.
- There is a small amount of metadata overhead for each import.
