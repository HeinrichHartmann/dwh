# ADR-001: Testing Strategy

**Status:** Accepted
**Date:** 2026-03-29

## Context

DWH makes strong promises: durability, provenance, rebuildability. We need a testing strategy that verifies these promises hold, not just that code runs.

Testing should:
1. Verify the product works as advertised in README
2. Ensure CLI structure matches specification
3. Catch regressions in core algorithms
4. Be maintainable and not overly coupled to implementation details

## Decision

We use **pytest** with three tiers of tests:

### Tier 1: End-to-End Workflow Tests (Backbone)

These are the most important tests. They exercise natural user flows and verify the properties we advertise.

**Characteristics:**
- Test complete workflows, not individual functions
- Use the CLI or public API as entry point
- Create real temporary warehouses
- Verify observable outcomes (files exist, receipts contain expected data)

**Examples:**

```python
def test_import_export_roundtrip(tmp_warehouse):
    """Import files, export by drop_id, verify identical content."""
    # Import
    result = run_cli(["drop", "import", "-m", "test", "testdata/"])
    drop_id = extract_drop_id(result.output)

    # Export to new location
    run_cli(["drop", "export", drop_id, "restored/"])

    # Verify content matches
    assert files_identical("testdata/", "restored/")

def test_durability_promise(tmp_warehouse):
    """If we have the receipt, we can get back the data."""
    # Import and get receipt
    run_cli(["drop", "import", "-m", "important", "testdata/doc.pdf"])
    receipt = read_json("drops/2026/.../receipt.json")

    # Nuke the projections (simulating corruption)
    shutil.rmtree("drops/")
    shutil.rmtree("documents/")

    # Restore from metadata
    run_cli(["restore"])

    # Verify receipt data is back
    assert Path(f"drops/2026/{receipt['drop_id']}/tree/doc.pdf").exists()

def test_provenance_chain(tmp_warehouse):
    """Every document traces back to its import event."""
    run_cli(["drop", "import", "-m", "tax docs", "invoice.pdf"])
    run_cli(["file", "stage", "drops/.../invoice.pdf", "--category", "finance"])
    run_cli(["file", "commit", "-m", "filing"])

    # Query document and verify provenance
    doc = query_document("finance/invoice.pdf")
    assert doc.entry.drop.message == "tax docs"
```

**Coverage targets:**
- Import → files appear in drops/
- Export → reconstructs original structure
- Filing → entry becomes document
- Capture → filesystem moves detected
- Restore → projections rebuilt from metadata
- Verify → corruption detected

### Tier 2: CLI Structure Tests (Generated from Spec)

These ensure all advertised commands exist and have correct argument structure.

**Characteristics:**
- Generated from DESIGN.md or a CLI spec
- Test that commands exist and accept specified arguments
- Test help text is present
- Do NOT test behavior (that's Tier 1)

**Examples:**

```python
# Generated from CLI spec
CLI_SPEC = {
    "init": {"args": ["path"], "options": ["--name"]},
    "drop import": {"args": ["paths..."], "options": ["-m/--message"]},
    "drop export": {"args": ["drop_id", "dest"]},
    "drop list": {},
    "drop inspect": {"args": ["drop_id"]},
    "file stage": {"args": ["path"], "options": ["--category"]},
    "file commit": {"options": ["-m/--message"]},
    "capture": {},
    "restore": {"args": ["path?"]},
    "verify": {"args": ["drop_id?"]},
}

@pytest.mark.parametrize("command,spec", CLI_SPEC.items())
def test_command_exists(command, spec):
    """Command exists and --help works."""
    result = run_cli(command.split() + ["--help"])
    assert result.exit_code == 0

@pytest.mark.parametrize("command,spec", CLI_SPEC.items())
def test_command_has_required_options(command, spec):
    """Command accepts specified options."""
    for opt in spec.get("options", []):
        assert opt.split("/")[-1] in result.output
```

### Tier 3: Unit Tests (Sparingly)

Only for isolated algorithms where correctness matters and E2E tests are too slow or indirect.

**Candidates:**
- `compute_hash()` - SHA-256 computation
- `resolve_collision()` - Filename collision logic
- `match_entries()` - Filing inference matching
- `parse_drop_id()` - ID format parsing

**Characteristics:**
- Test pure functions
- Fast, no I/O
- Document edge cases

**Examples:**

```python
def test_compute_hash():
    assert compute_hash(b"hello") == "2cf24dba5fb0a30e..."

def test_resolve_collision():
    existing = ["invoice.pdf", "invoice_2.pdf"]
    assert resolve_collision("invoice.pdf", existing) == "invoice_3.pdf"

def test_match_entries_unique():
    entries = [Entry(filename="a.pdf", blob_hash="abc")]
    moved = [MovedFile(filename="a.pdf", blob_hash="abc")]
    matches = match_entries(entries, moved)
    assert len(matches) == 1
    assert matches[0].is_unambiguous

def test_match_entries_ambiguous():
    entries = [
        Entry(filename="doc.pdf", blob_hash="abc"),
        Entry(filename="doc.pdf", blob_hash="def"),
    ]
    moved = [MovedFile(filename="doc.pdf", blob_hash="abc")]
    matches = match_entries(entries, moved)
    assert matches[0].is_ambiguous  # Same filename, different content
```

## Test Infrastructure

### Fixtures

```python
@pytest.fixture
def tmp_warehouse(tmp_path):
    """Create a temporary warehouse for testing."""
    os.chdir(tmp_path)
    run_cli(["init", "."])
    yield tmp_path
    os.chdir(original_dir)

@pytest.fixture
def sample_files(tmp_path):
    """Create sample test files."""
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4...")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir/nested.txt").write_text("content")
    return tmp_path
```

### Test Helpers

```python
def run_cli(args: list[str]) -> Result:
    """Run dwh CLI and return result."""
    from click.testing import CliRunner
    from dwh.cli import main
    runner = CliRunner()
    return runner.invoke(main, args)

def files_identical(dir1: Path, dir2: Path) -> bool:
    """Check two directories have identical file content."""
    ...

def extract_drop_id(output: str) -> str:
    """Extract drop_id from CLI output."""
    ...
```

## Directory Structure

```
tests/
├── conftest.py           # Shared fixtures
├── e2e/                   # Tier 1: End-to-end workflow tests
│   ├── test_import_export.py
│   ├── test_filing.py
│   ├── test_restore.py
│   └── test_verify.py
├── cli/                   # Tier 2: CLI structure tests
│   └── test_cli_structure.py
├── unit/                  # Tier 3: Unit tests
│   ├── test_hash.py
│   ├── test_collision.py
│   └── test_matching.py
└── testdata/              # Sample files for testing
    ├── sample.pdf
    └── folder/
```

## Consequences

### Positive

- **E2E backbone** ensures product promises are verified
- **CLI tests** catch interface regressions early
- **Sparse unit tests** avoid over-coupling to implementation
- **Maintainable** - tests describe behavior, not implementation

### Negative

- E2E tests are slower than unit tests
- Need test infrastructure (fixtures, helpers)
- CLI spec must be maintained alongside code

### Trade-offs

We prefer fewer, higher-confidence tests over many fragile unit tests. An E2E test that verifies "import then export produces identical files" is worth more than 20 unit tests of internal functions.

## Running Tests

```bash
# All tests
pytest

# Just E2E (slow, thorough)
pytest tests/e2e/

# Just CLI structure (fast)
pytest tests/cli/

# Just unit (fast)
pytest tests/unit/

# With coverage
pytest --cov=dwh
```

## References

- README.md - Product promises to verify
- DESIGN.md - CLI specification for structure tests
