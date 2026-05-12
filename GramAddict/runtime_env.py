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


def _adb_filenames() -> tuple[str, ...]:
    # Su Windows l'eseguibile e' adb.exe, su Unix e' adb (no estensione).
    return ("adb.exe", "adb") if os.name == "nt" else ("adb",)


def _candidate_adb_paths() -> Iterable[Path]:
    names = _adb_filenames()

    # 1) Variabili d'ambiente esplicite
    for env_name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        base = Path(raw).expanduser()
        for name in names:
            yield base / "platform-tools" / name
            yield base / name

    home = Path.home()

    # 2) macOS
    for name in names:
        yield home / "Library" / "Android" / "sdk" / "platform-tools" / name

    # 3) Linux / Windows user-local installation di Android Studio
    for name in names:
        # Windows: %LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            yield Path(local_appdata) / "Android" / "Sdk" / "platform-tools" / name
        # Linux/Windows generico in home: ~/Android/Sdk/platform-tools/adb(.exe)
        yield home / "Android" / "Sdk" / "platform-tools" / name
        # Variante "AppData/Local/Android/Sdk" anche per profili senza LOCALAPPDATA
        yield home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / name

    # 4) Linux/macOS system-wide
    yield Path("/opt/homebrew/bin/adb")
    yield Path("/usr/local/bin/adb")

    # 5) Windows system-wide (Android Studio o standalone platform-tools)
    if os.name == "nt":
        for name in names:
            yield Path(r"C:\Android\Sdk\platform-tools") / name
            yield Path(r"C:\Program Files\Android\Android Studio\platform-tools") / name
            yield Path(r"C:\Program Files (x86)\Android\android-sdk\platform-tools") / name

