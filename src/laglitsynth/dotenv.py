"""Minimal .env reader for CLI fallback values.

Reads KEY=VALUE pairs from a .env file when the corresponding CLI flag was not
passed.  Does NOT mutate os.environ — callers receive the value and decide what
to do with it.
"""

from __future__ import annotations

from pathlib import Path


def load_env_var(
    key: str,
    *,
    env_path: Path = Path(".env"),
) -> str | None:
    """Read a single KEY=VALUE pair from a .env file.

    Returns the value string if the key is present, else None.  Does not
    raise if the file is absent.  Comments (lines starting with ``#``) and
    blank lines are ignored.  Surrounding single or double quotes on the
    value are stripped.

    Does NOT set os.environ; the caller decides what to do with the value.
    """
    try:
        text = env_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if k != key:
            continue
        # Strip surrounding quotes (single or double).
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        return v

    return None
