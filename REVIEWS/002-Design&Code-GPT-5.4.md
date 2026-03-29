# Design and Code Review: Triage CLI WIP

**Reviewer:** GPT-5.4
**Date:** 2026-03-29
**Scope:** Working tree changes in `src/dwh/cli.py`, `tests/e2e/test_triage.py`, `Makefile`, and `pyproject.toml`
**Mode:** Static review only; no commands or tests executed for this review

## Overview

This review covers the current work-in-progress change set around the triage CLI.

The implementation appears to be moving from:

- `dwh triage`
- `dwh triage <drop_id>`
- `dwh triage sync`

to an explicit subcommand model:

- `dwh triage checkout`
- `dwh triage checkout <drop_id>`
- `dwh triage sync`

That can be a reasonable direction, but the current branch is only partially migrated. The result is an inconsistent contract across code, tests, and design docs.

## Findings

### 1. High: The CLI implementation no longer matches the documented triage workflow

In [src/dwh/cli.py](/Users/hhartmann/DocumentWarehouse/src/dwh/src/dwh/cli.py), the triage command changed from an `invoke_without_command` group with an optional `drop_id` argument to a plain command group plus a `checkout` subcommand.

Current behavior implied by the code:

- `dwh triage` now resolves to the group itself, not checkout
- `dwh triage <drop_id>` is treated as a subcommand lookup and will fail
- only `dwh triage checkout [drop_id]` performs the checkout

That is a user-visible contract change, but the written design still describes the old behavior:

- [README.md](/Users/hhartmann/DocumentWarehouse/src/dwh/README.md) uses `dwh triage` and `dwh triage <drop_id>`
- [DESIGN.md](/Users/hhartmann/DocumentWarehouse/src/dwh/DESIGN.md) defines the triage flow in the same shape

Why this matters:

- the repo currently has two conflicting stories about the intended UX
- for a workflow command, discoverability and muscle memory matter more than for an internal refactor
- even in WIP, this should be called out as an intentional breaking change or kept backward compatible during migration

Recommendation:

- choose one contract and make it explicit
- if the long-term direction is `checkout`, keep `dwh triage [drop_id]` as a compatibility path until docs and tests are fully migrated
- if the old UX is preferred, keep the previous default behavior and optionally add `checkout` as an alias

### 2. Medium: The tests are only partially migrated, so they no longer define a single expected interface

The triage test file mixes both command shapes:

- old interface: `["triage"]`, `["triage", drop_id]`
- new interface: `["triage", "checkout"]`

That means the test suite is currently encoding incompatible expectations instead of acting as a stable spec.

Why this matters:

- partial migration makes failures noisy and harder to interpret
- reviewers cannot tell whether the branch intends a breaking CLI change or simply has incomplete edits
- future refactors will inherit ambiguity about the supported UX

Recommendation:

- migrate the triage tests in one direction only
- if backward compatibility is intended, add explicit tests for both forms and document one as the preferred interface
- otherwise, update all tests and docs in the same change set

## Secondary Notes

The `Makefile` and `pyproject.toml` changes look directionally good for developer workflow:

- `dev` extras make test and lint tooling explicit
- `lint` target improves local verification

Those changes are low risk compared with the CLI contract change.

## Recommendations

### Must Resolve Before Calling This Stable

1. Decide whether `dwh triage` remains a default checkout command.
2. Bring code, tests, and docs to the same CLI contract.
3. If the command shape is intentionally changing, state that clearly in the docs and add a transition path if user experience matters.

### Good WIP Path

1. Keep the explicit `checkout` subcommand if it improves clarity.
2. Preserve `dwh triage [drop_id]` as a thin compatibility layer for now.
3. Remove the compatibility path only after the docs and tests are fully migrated.

## Summary

The main issue is not the underlying triage implementation. It is the command contract around that implementation.

Right now the branch reads like a halfway migration: the code has moved, but the design narrative and part of the test suite still assume the old interface. For a WIP branch, that is acceptable as an intermediate state, but it should be treated as the central review issue before the change is finalized.
