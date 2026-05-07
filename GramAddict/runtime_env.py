from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable


_BOOTSTRAPPED = False


def bootstrap_runtime_env() -> None:
    """Best-effort bootstrap for GUI/IDE launches.

    Goals:
      - load `.env.local` if present (without overriding existing env vars)
      - ensure `adb` is discoverable even when PyCharm starts without the
        shell PATH from `.zshrc` / `.zprofile`

    Silent by design: startup must never fail because of this helper.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    try:
        _autoload_env_local()
    except Exception:
        pass
    try:
        _ensure_adb_on_path()
    except Exception:
        pass


def _autoload_env_local() -> None:
    for env_file in _candidate_env_files():
        if env_file.is_file():
            _parse_env_file(env_file)
            return


def _candidate_env_files() -> Iterable[Path]:
    seen: set[Path] = set()
    origins = [Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent]
    for origin in origins:
        for base in (origin, *origin.parents):
            if base in seen:
                continue
            seen.add(base)
            yield base / ".env.local"


def _parse_env_file(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or os.environ.get(key):
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _ensure_adb_on_path() -> None:
    if shutil.which("adb"):
        return
    for adb_path in _candidate_adb_paths():
        if not adb_path.is_file():
            continue
        current_path = os.environ.get("PATH", "")
        adb_dir = str(adb_path.parent)
        os.environ["PATH"] = (
            adb_dir if not current_path else f"{adb_dir}{os.pathsep}{current_path}"
        )
        sdk_root = adb_path.parent.parent
        os.environ.setdefault("ANDROID_SDK_ROOT", str(sdk_root))
        os.environ.setdefault("ANDROID_HOME", str(sdk_root))
        return


def _candidate_adb_paths() -> Iterable[Path]:
    for env_name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        base = Path(raw).expanduser()
        yield base / "platform-tools" / "adb"
        yield base / "adb"

    home = Path.home()
    yield home / "Library" / "Android" / "sdk" / "platform-tools" / "adb"
    yield home / "Android" / "Sdk" / "platform-tools" / "adb"
    yield Path("/opt/homebrew/bin/adb")
    yield Path("/usr/local/bin/adb")
