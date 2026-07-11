"""Shared helpers: URL normalize, atomic JSON I/O, safe coercions."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("utils")


def normalize_url(url: str) -> str:
    """
    Stable dedup key: lower host, drop query/fragment, trailing slash,
    normalize scheme to https when missing, strip www.
    """
    if not url:
        return ""
    raw = url.strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    if scheme not in {"http", "https"}:
        return ""
    # Treat http and https as the same article for dedup
    scheme = "https"
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = (parsed.path or "").rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON via temp file + os.replace (crash-safe)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_json_file(path: Path, default: Any) -> Any:
    """Load JSON; on missing file return default; on corrupt raise by default_policy."""
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_json_safe(path: Path, default: Any) -> Any:
    """Load JSON; missing or corrupt → default + log."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read %s: %s — using default", path, exc)
        return default


def load_history_strict(path: Path) -> dict[str, Any]:
    """
    Load posted history. Missing → empty.
    Corrupt → raise (fail closed — do not wipe dedup).
    """
    path = Path(path)
    if not path.exists():
        return {"posted": [], "last_updated": None}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"Corrupt history file {path}: {exc}. "
            "Fix or restore from git — refusing to start empty (would allow reposts)."
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"History {path} is not a JSON object")
    if not isinstance(data.get("posted"), list):
        data["posted"] = []
    return data


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value is False:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def coerce_index(value: Any, max_n: int) -> int | None:
    """Coerce 1-based index from int/float/str. None if invalid."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, float):
            if not value.is_integer():
                return None
            idx = int(value)
        else:
            idx = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= idx <= max_n:
        return idx
    return None
