"""Shared test fixtures and helpers."""

import os
import hashlib
import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from dwh.cli import main


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Isolate global config for each test.

    This ensures each test gets its own global config directory,
    preventing warehouse registrations from leaking between tests.
    Uses DWH_CONFIG_DIR environment variable to sandbox the config.
    """
    test_config_dir = tmp_path / ".dwh_test_config"
    test_config_dir.mkdir(parents=True, exist_ok=True)

    # Override DWH_CONFIG_DIR to point to test directory
    monkeypatch.setenv("DWH_CONFIG_DIR", str(test_config_dir))

    return test_config_dir


@pytest.fixture
def runner():
    """Provide Click test runner."""
    return CliRunner()


@pytest.fixture
def tmp_warehouse(runner, tmp_path):
    """Create a temporary warehouse for testing."""
    original_dir = os.getcwd()
    os.chdir(tmp_path)

    # Initialize warehouse
    result = runner.invoke(main, ["init", "."])
    assert result.exit_code == 0, f"Init failed: {result.output}"

    yield tmp_path

    os.chdir(original_dir)


@pytest.fixture
def sample_files(tmp_path):
    """Create sample test files with known content.

    Creates files outside the warehouse directory to avoid
    conflicts with triage scanning logic.
    """
    # Create samples in parent directory, outside warehouse
    sample_dir = tmp_path.parent / f"samples_{tmp_path.name}"
    sample_dir.mkdir(exist_ok=True)

    # Create various test files
    (sample_dir / "file1.txt").write_text("Content of file 1\n")
    (sample_dir / "file2.txt").write_text("Content of file 2\n")

    # Create nested structure
    subdir = sample_dir / "subdir"
    subdir.mkdir(exist_ok=True)
    (subdir / "nested.txt").write_text("Nested file content\n")

    deeper = subdir / "deeper"
    deeper.mkdir(exist_ok=True)
    (deeper / "deep.txt").write_text("Deep nested content\n")

    return sample_dir


@pytest.fixture
def single_file(tmp_path):
    """Create a single test file outside warehouse."""
    # Create in parent directory to avoid warehouse scanning
    file_path = tmp_path.parent / f"single_{tmp_path.name}.txt"
    file_path.write_text("Single file content\n")
    return file_path


@pytest.fixture
def empty_dir(tmp_path):
    """Create an empty directory outside warehouse."""
    empty = tmp_path.parent / f"empty_{tmp_path.name}"
    empty.mkdir(exist_ok=True)
    return empty


# Test helpers


def run_cli(runner: CliRunner, args: list[str], input: str | None = None) -> Any:
    """Run dwh CLI and return result."""
    return runner.invoke(main, args, input=input)


def extract_drop_id(output: str) -> str:
    """Extract drop_id from CLI output.

    Looks for 'Drop ID: <id>' to avoid matching drop IDs
    from duplicate warnings or other messages.
    """
    match = re.search(r"Drop ID: (d_\d{8}_\d{6}_[a-f0-9]{8})", output)
    if not match:
        raise ValueError(f"No drop_id found in output: {output}")
    return match.group(1)


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def collect_files(directory: Path) -> dict[str, bytes]:
    """Collect all files in directory as {relative_path: content}."""
    files = {}
    for file in directory.rglob("*"):
        if file.is_file():
            rel_path = file.relative_to(directory)
            files[str(rel_path)] = file.read_bytes()
    return files


def files_identical(dir1: Path, dir2: Path) -> bool:
    """Check if two directories have identical file content."""
    files1 = collect_files(dir1)
    files2 = collect_files(dir2)

    if set(files1.keys()) != set(files2.keys()):
        return False

    for path in files1:
        if files1[path] != files2[path]:
            return False

    return True


def assert_files_match(dir1: Path, dir2: Path, msg: str = ""):
    """Assert two directories have identical content with helpful error message."""
    files1 = collect_files(dir1)
    files2 = collect_files(dir2)

    missing_in_dir2 = set(files1.keys()) - set(files2.keys())
    missing_in_dir1 = set(files2.keys()) - set(files1.keys())

    error_msg = []
    if missing_in_dir2:
        error_msg.append(f"Missing in {dir2}: {missing_in_dir2}")
    if missing_in_dir1:
        error_msg.append(f"Extra in {dir2}: {missing_in_dir1}")

    content_diffs = []
    for path in set(files1.keys()) & set(files2.keys()):
        if files1[path] != files2[path]:
            content_diffs.append(path)

    if content_diffs:
        error_msg.append(f"Content differs: {content_diffs}")

    if error_msg:
        full_msg = msg + "\n" + "\n".join(error_msg) if msg else "\n".join(error_msg)
        raise AssertionError(full_msg)


def count_files(directory: Path) -> int:
    """Count files in directory recursively."""
    return sum(1 for f in directory.rglob("*") if f.is_file())
