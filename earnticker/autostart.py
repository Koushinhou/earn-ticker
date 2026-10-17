"""Optional autostart via a shortcut in the user's Startup folder.

No registry writes and no admin rights: the shortcut (.lnk) is created with
the WScript.Shell COM object through a short PowerShell call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "EarnTicker.lnk"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def is_enabled() -> bool:
    return shortcut_path().exists()


def launch_target(config_dir: Path | None = None) -> tuple[str, str, str]:
    """Return ``(target, arguments, working_dir)`` for the shortcut."""
    args: list[str] = []
    if getattr(sys, "frozen", False):
        target = sys.executable
        workdir = str(Path(sys.executable).parent)
    else:
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        target = str(pythonw if pythonw.exists() else exe)
        args += ["-m", "earnticker"]
        workdir = str(Path(__file__).resolve().parent.parent)
    if config_dir is not None:
        args += ["--config-dir", f'"{config_dir}"']
    return target, " ".join(args), workdir


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def enable(config_dir: Path | None = None) -> bool:
    target, args, workdir = launch_target(config_dir)
    link = shortcut_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(link))});"
        f"$s.TargetPath={_ps_quote(target)};$s.Arguments={_ps_quote(args)};$s.WorkingDirectory={_ps_quote(workdir)};"
        "$s.Description='EarnTicker';$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return link.exists()


def disable() -> bool:
    try:
        shortcut_path().unlink(missing_ok=True)
    except OSError:
        return False
    return True


def apply(enabled: bool, config_dir: Path | None = None) -> bool:
    """Create or remove the shortcut. Returns True on success."""
    return enable(config_dir) if enabled else disable()
