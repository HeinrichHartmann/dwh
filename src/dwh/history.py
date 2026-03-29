"""History operations for append-only log."""

from pathlib import Path


def get_next_history_number(history_dir: Path) -> int:
    """Get next sequential history number."""
    if not history_dir.exists():
        return 1

    # Find highest numbered item
    max_num = 0
    for item in history_dir.iterdir():
        name = item.name
        if "_" in name:
            try:
                num = int(name.split("_")[0])
                max_num = max(max_num, num)
            except ValueError:
                continue

    return max_num + 1


def find_drop_in_history(history_dir: Path, drop_id: str) -> Path | None:
    """Find drop folder in history by drop_id."""
    if not history_dir.exists():
        return None

    for item in history_dir.iterdir():
        if item.is_dir() and drop_id in item.name:
            return item

    return None
