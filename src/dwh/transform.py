"""Transformation operations for DWH."""

import getpass
import hashlib
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dwh import drop as drop_module


class TransformationError(Exception):
    """Base exception for transformation operations."""

    pass


@dataclass
class TransformationState:
    """Active transformation state."""

    transformation_id: str
    input_spec: str
    started_at: datetime


def generate_transformation_id() -> str:
    """Generate transformation ID: t_YYYYMMDD_HHMMSS_hash8."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    # Add random hash suffix for uniqueness
    random_data = f"{now.isoformat()}_{id(object())}".encode()
    hash_suffix = hashlib.sha256(random_data).hexdigest()[:8]

    return f"t_{timestamp}_{hash_suffix}"


def get_transformation_state(conn: sqlite3.Connection) -> TransformationState | None:
    """Get current transformation state if exists."""
    row = conn.execute("SELECT * FROM transformation_state WHERE id = 1").fetchone()
    if not row:
        return None

    return TransformationState(
        transformation_id=row["transformation_id"],
        input_spec=row["input_spec"],
        started_at=datetime.fromisoformat(row["started_at"]),
    )


def clear_transformation_state(conn: sqlite3.Connection) -> None:
    """Clear transformation state."""
    conn.execute("DELETE FROM transformation_state WHERE id = 1")
    conn.commit()


def start_transformation(
    input_spec: str,
    warehouse_root: Path,
    conn: sqlite3.Connection,
) -> TransformationState:
    """Start a transformation by populating _input/ directory.

    Args:
        input_spec: Input specification (e.g., "drop:d_20260329_120000_abc123")
        warehouse_root: Warehouse root directory
        conn: Database connection

    Returns:
        TransformationState

    Raises:
        TransformationError: If transformation already active or input invalid
    """
    # Check if transformation already active
    existing = get_transformation_state(conn)
    if existing:
        raise TransformationError(
            f"Transformation already active: {existing.transformation_id}"
        )

    # Parse input spec
    if not input_spec.startswith("drop:"):
        raise TransformationError(
            f"Only drop: queries supported in this version. Got: {input_spec}"
        )

    drop_id = input_spec[5:]  # Remove "drop:" prefix

    # Verify drop exists
    drop_row = conn.execute("SELECT * FROM drops WHERE id = ?", (drop_id,)).fetchone()
    if not drop_row:
        raise TransformationError(f"Drop not found: {drop_id}")

    # Get entries for this drop
    entries = conn.execute(
        "SELECT * FROM entries WHERE drop_id = ?", (drop_id,)
    ).fetchall()

    if not entries:
        raise TransformationError(f"Drop has no entries: {drop_id}")

    # Create _input/ and _output/ directories
    input_dir = warehouse_root / "_input"
    output_dir = warehouse_root / "_output"

    # Clean up existing directories if they exist
    if input_dir.exists():
        shutil.rmtree(input_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Populate _input/ from drop's history tree
    # Find drop directory in history
    history_dir = warehouse_root / "_history"
    drop_tree_dir = None

    for item in history_dir.iterdir():
        if item.is_dir() and f"_drop_{drop_id}" in item.name:
            drop_tree_dir = item / "tree"
            break

    if not drop_tree_dir or not drop_tree_dir.exists():
        raise TransformationError(f"Drop tree not found for: {drop_id}")

    # Copy all files from drop tree to _input/
    for file in drop_tree_dir.rglob("*"):
        if file.is_file():
            rel_path = file.relative_to(drop_tree_dir)
            dest = input_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, dest)

    # Generate transformation ID
    transformation_id = generate_transformation_id()

    # Record transformation state
    conn.execute(
        "INSERT INTO transformation_state (id, transformation_id, input_spec) VALUES (?, ?, ?)",
        (1, transformation_id, input_spec),
    )
    conn.commit()

    # Get the recorded state
    state = get_transformation_state(conn)
    if not state:
        raise TransformationError("Failed to record transformation state")

    return state


def import_transformation(
    message: str,
    warehouse_root: Path,
    history_dir: Path,
    conn: sqlite3.Connection,
) -> dict:
    """Import transformation outputs as a new drop with provenance.

    Args:
        message: Transformation message
        warehouse_root: Warehouse root directory
        history_dir: History directory
        conn: Database connection

    Returns:
        Dictionary with transformation_id, drop_id, and file count

    Raises:
        TransformationError: If no transformation active or _output/ empty
    """
    # Check transformation state
    state = get_transformation_state(conn)
    if not state:
        raise TransformationError("No transformation active. Run 'dwh transform start' first.")

    # Check _output/ directory
    output_dir = warehouse_root / "_output"
    if not output_dir.exists():
        raise TransformationError("_output/ directory not found")

    # Collect output files
    output_files = list(output_dir.rglob("*"))
    output_files = [f for f in output_files if f.is_file()]

    if not output_files:
        raise TransformationError("_output/ directory is empty. Nothing to import.")

    # Import _output/ as a drop
    actor = getpass.getuser()
    drop_result = drop_module.drop_import(
        paths=[output_dir],
        message=message,
        warehouse_root=warehouse_root,
        history_dir=history_dir,
        conn=conn,
    )

    # Get input entries for provenance
    # Parse input spec to get the source drop_id
    if state.input_spec.startswith("drop:"):
        source_drop_id = state.input_spec[5:]  # Remove "drop:" prefix
        input_entries = conn.execute(
            "SELECT id, relative_path, filename FROM entries WHERE drop_id = ?",
            (source_drop_id,)
        ).fetchall()
    else:
        input_entries = []

    # Record transformation
    conn.execute(
        """INSERT INTO transformations
           (id, message, actor, input_spec, result_drop_id, result_type)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            state.transformation_id,
            message,
            actor,
            state.input_spec,
            drop_result.id,  # Access Drop.id attribute
            "import",
        ),
    )

    # Record transformation inputs
    input_dir = warehouse_root / "_input"
    for entry in input_entries:
        input_path = entry["relative_path"] or entry["filename"]
        conn.execute(
            """INSERT INTO transformation_inputs
               (transformation_id, entry_id, input_path)
               VALUES (?, ?, ?)""",
            (state.transformation_id, entry["id"], input_path),
        )

    conn.commit()

    # Clean up _input/ and _output/
    if input_dir.exists():
        shutil.rmtree(input_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)

    # Clear transformation state
    clear_transformation_state(conn)

    return {
        "transformation_id": state.transformation_id,
        "drop_id": drop_result.id,
        "files": len(output_files),
    }


def get_transformation_status(
    warehouse_root: Path,
    conn: sqlite3.Connection,
) -> dict:
    """Get status of current transformation.

    Returns:
        Dictionary with state, input files, output files, or None if no transformation active
    """
    state = get_transformation_state(conn)
    if not state:
        return {"active": False}

    input_dir = warehouse_root / "_input"
    output_dir = warehouse_root / "_output"

    # Count files in _input/
    input_files = []
    if input_dir.exists():
        for f in input_dir.rglob("*"):
            if f.is_file():
                rel_path = f.relative_to(input_dir)
                size = f.stat().st_size
                input_files.append({"path": str(rel_path), "size": size})

    # Count files in _output/
    output_files = []
    if output_dir.exists():
        for f in output_dir.rglob("*"):
            if f.is_file():
                rel_path = f.relative_to(output_dir)
                size = f.stat().st_size
                output_files.append({"path": str(rel_path), "size": size})

    return {
        "active": True,
        "transformation_id": state.transformation_id,
        "input_spec": state.input_spec,
        "started_at": state.started_at,
        "input_files": input_files,
        "output_files": output_files,
    }


def abort_transformation(
    warehouse_root: Path,
    conn: sqlite3.Connection,
) -> dict:
    """Abort current transformation and clean up working directories.

    Returns:
        Dictionary with cleanup stats

    Raises:
        TransformationError: If no transformation active
    """
    state = get_transformation_state(conn)
    if not state:
        raise TransformationError("No transformation active")

    input_dir = warehouse_root / "_input"
    output_dir = warehouse_root / "_output"

    # Count files before cleanup
    input_count = len([f for f in input_dir.rglob("*") if f.is_file()]) if input_dir.exists() else 0
    output_count = len([f for f in output_dir.rglob("*") if f.is_file()]) if output_dir.exists() else 0

    # Remove directories
    if input_dir.exists():
        shutil.rmtree(input_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)

    # Clear state
    clear_transformation_state(conn)

    return {
        "transformation_id": state.transformation_id,
        "input_files_removed": input_count,
        "output_files_removed": output_count,
    }
