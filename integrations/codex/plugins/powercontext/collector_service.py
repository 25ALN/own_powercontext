# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Current-user lifecycle for the opt-in Codex client collector, never the Server."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from time import sleep, time
from typing import Any

from image_capture_config import any_enabled, codex_home, plugin_enabled

OWNER = "powercontext.codex-image-collector.v1:"
ROOT = Path(__file__).resolve().parent


def identifier() -> str:
    return "powercontext.codex-images-" + hashlib.sha256(os.fsencode(codex_home())).hexdigest()[:12]


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(  # noqa: S603 - fixed native manager argument arrays, no shell.
        args, capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    if check and result.returncode:
        raise ValueError("image_collector_manager_unavailable")
    return result


def _command() -> list[str]:
    python = Path(sys.executable)
    if os.name == "nt" and python.with_name("pythonw.exe").is_file():
        python = python.with_name("pythonw.exe")
    index_home = Path(os.environ.get("CODEX_SQLITE_HOME", codex_home())).expanduser().resolve()
    return [str(python), str(ROOT / "collector_service.py"), "run", str(codex_home()), str(index_home)]


def _artifact() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup" / (identifier() + ".lnk")
    if sys.platform == "darwin":
        return Path.home() / "Library/LaunchAgents" / (identifier() + ".plist")
    if sys.platform.startswith("linux"):
        return (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "systemd/user"
            / (identifier() + ".service")
        )
    raise ValueError("image_collector_platform_unsupported")


def _shortcut(action: str) -> dict[str, Any]:
    """Inspect ownership and manage a user Startup shortcut without elevation."""
    spec = {
        "action": action,
        "path": str(_artifact()),
        "description": OWNER + str(codex_home()),
        "command": _command(),
        "root": str(ROOT),
        "home": str(codex_home()),
    }
    literal = json.dumps(spec).replace("'", "''")
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $spec=ConvertFrom-Json '"
        + literal
        + "'; "
        + r"""
$shell=New-Object -ComObject WScript.Shell
$exists=Test-Path -LiteralPath $spec.path
$shortcut=$shell.CreateShortcut($spec.path)
if ($exists -and $shortcut.Description -ne $spec.description) { throw 'image_collector_registration_foreign' }
if ($spec.action -eq 'install') {
  # Restart only Python supervisors whose exact arguments belong to this
  # registration. Descendants are stopped first so they cannot retain locks.
  $arguments=@($spec.arguments)
  if ($exists -and $shortcut.Arguments.Contains('collector_service.py') -and $shortcut.Arguments.Contains($spec.home)) {
    $arguments+=@($shortcut.Arguments)
  }
  $processes=@(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'")
  $supervisors=@($processes | Where-Object {
    $command=$_.CommandLine
    $null -ne $command -and @($arguments | Where-Object {$command.EndsWith($_)}).Count -gt 0
  })
  $parents=@($supervisors.ProcessId)
  $children=@()
  for ($depth=0; $depth -lt 3; $depth++) {
    $next=@($processes | Where-Object {$_.ParentProcessId -in $parents})
    $children+=@($next)
    $parents=@($next.ProcessId)
  }
  foreach ($process in @($children | Sort-Object CreationDate -Descending) + $supervisors) {
    $current=Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)"
    if ($null -ne $current -and $current.CreationDate -eq $process.CreationDate -and $current.CommandLine -eq $process.CommandLine) {
      Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
  $shortcut.TargetPath=$spec.command[0]
  $shortcut.Arguments=$spec.arguments
  $shortcut.WorkingDirectory=$spec.root
  $shortcut.Description=$spec.description
  $shortcut.WindowStyle=7
  $shortcut.Save()
  $exists=$true
} elseif ($spec.action -eq 'uninstall' -and $exists) {
  Remove-Item -LiteralPath $spec.path
  $exists=$false
}
@{registered=$exists; target=$shortcut.TargetPath; arguments=$shortcut.Arguments} | ConvertTo-Json -Compress
"""
    )
    # Arguments use CreateProcess quoting, never PowerShell interpretation.
    spec["arguments"] = subprocess.list2cmdline(spec["command"][1:])
    script = script.replace(literal, json.dumps(spec).replace("'", "''"), 1)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = _run("powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded, check=False)
    if result.returncode:
        if b"image_collector_registration_foreign" in result.stderr:
            raise ValueError("image_collector_registration_foreign")
        raise ValueError("image_collector_manager_unavailable")
    value = json.loads(result.stdout.decode("utf-8-sig"))
    return value


def launch() -> None:
    subprocess.Popen(  # noqa: S603 - fixed packaged supervisor, never shell text.
        _command(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
        start_new_session=os.name != "nt",
    )


def install() -> None:
    """Setup alone writes login registration. Session hooks only recover the client."""
    path = _artifact()
    if path.is_symlink():
        raise ValueError("image_collector_registration_foreign")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sys.platform == "win32":
        _shortcut("install")
        launch()
        return
    if path.exists() and (OWNER + str(codex_home())).encode() not in path.read_bytes():
        raise ValueError("image_collector_registration_foreign")
    if sys.platform == "darwin":
        payload = plistlib.dumps({
            "Label": identifier(),
            "ProgramArguments": _command(),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "WorkingDirectory": str(ROOT),
            "PowerContextOwner": OWNER + str(codex_home()),
        })
    else:
        command = " ".join(json.dumps(arg).replace("%", "%%") for arg in _command())
        payload = (
            f"# {OWNER}{codex_home()}\n[Unit]\nDescription=PowerContext Codex image collector\n"
            f"[Service]\nType=simple\nExecStart={command}\nRestart=on-failure\nRestartSec=5\n"
            "[Install]\nWantedBy=default.target\n"
        ).encode()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    if sys.platform == "darwin":
        target = "gui/" + str(os.getuid())
        _run("launchctl", "bootout", target + "/" + identifier(), check=False)
        _run("launchctl", "bootstrap", target, str(path))
    else:
        _run("systemctl", "--user", "daemon-reload")
        _run("systemctl", "--user", "enable", "--now", identifier() + ".service")
        _run("systemctl", "--user", "restart", identifier() + ".service")


def uninstall() -> None:
    """Remove only our registration; staged evidence is retained and consent stops the supervisor."""
    path = _artifact()
    if path.is_symlink():
        raise ValueError("image_collector_registration_foreign")
    if sys.platform == "win32":
        _shortcut("uninstall")
        return
    if path.exists():
        if (OWNER + str(codex_home())).encode() not in path.read_bytes():
            raise ValueError("image_collector_registration_foreign")
        if sys.platform == "darwin":
            _run("launchctl", "bootout", "gui/" + str(os.getuid()) + "/" + identifier(), check=False)
        else:
            _run("systemctl", "--user", "disable", "--now", identifier() + ".service")
        path.unlink()
    if sys.platform.startswith("linux"):
        _run("systemctl", "--user", "daemon-reload")


def status() -> dict[str, Any]:
    registered = False
    error = None
    try:
        path = _artifact()
        if sys.platform == "win32":
            value = _shortcut("status")
            registered = (
                value["registered"]
                and Path(value["target"]) == Path(_command()[0])
                and value["arguments"] == subprocess.list2cmdline(_command()[1:])
            )
        elif path.exists() and not path.is_symlink() and (OWNER + str(codex_home())).encode() in path.read_bytes():
            if sys.platform == "darwin":
                registered = (
                    _run("launchctl", "print", "gui/" + str(os.getuid()) + "/" + identifier(), check=False).returncode
                    == 0
                )
            else:
                registered = (
                    _run("systemctl", "--user", "is-enabled", identifier() + ".service", check=False).returncode == 0
                )
    except (ValueError, OSError, subprocess.SubprocessError) as failure:
        error = str(failure) if str(failure).startswith("image_") else "image_collector_manager_unavailable"
    return {
        "registered": bool(registered),
        "manager": "user-startup" if sys.platform == "win32" else identifier(),
        "error": error,
    }


def run() -> None:
    """One supervisor recovers child exits and heartbeat stalls without touching Server state."""
    os.environ["CODEX_HOME"] = sys.argv[2]
    os.environ["CODEX_SQLITE_HOME"] = sys.argv[3]
    sys.path.insert(0, str(ROOT / "hooks"))
    sys.path.insert(0, str(ROOT / "scripts"))
    from diagnostics import _locked
    from image_desktop_watch import watch_status
    from settings import CodexPluginSettings

    path = codex_home() / "powercontext/collector-supervisor.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        try:
            stack.enter_context(_locked(path))
        except OSError:
            return  # An existing supervisor owns this client.
        delay = 1
        while True:
            started = time()
            try:
                if not any_enabled() or not plugin_enabled():
                    return
                settings = CodexPluginSettings()
                child = subprocess.Popen(  # noqa: S603 - fixed packaged reader.
                    [sys.executable, str(ROOT / "hooks/image_desktop_watch.py"), str(started)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    while child.poll() is None and any_enabled() and plugin_enabled():
                        sleep(1)
                        heartbeat = watch_status(settings.server_url).get("updated_at", started)
                        if time() - max(heartbeat, started) > 90:
                            break
                finally:
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=5)
            except (OSError, ValueError, subprocess.SubprocessError):
                # Pause collection while configuration or process creation is unavailable.
                # Keep the login supervisor alive so a repaired configuration can recover.
                pass
            delay = 1 if time() - started > 30 else min(30, delay * 2)
            sleep(delay)


if __name__ == "__main__":
    run()
