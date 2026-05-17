"""Platform-specific service management (macOS LaunchAgents, Linux systemd, etc.)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from openharness.utils.log import get_logger

logger = get_logger(__name__)


def install_service(
    label: str,
    args: list[str],
    workspace_root: Path,
    description: str = "OpenHarness Background Service",
) -> bool:
    """Install a system-level background service for the current user."""
    if sys.platform == "darwin":
        return _install_macos(label, args, workspace_root)
    if sys.platform == "linux":
        return _install_linux(label, args, workspace_root, description)
    logger.warning("System-level service install not yet supported on %s", sys.platform)
    return False


def uninstall_service(label: str) -> bool:
    """Uninstall a previously installed service."""
    if sys.platform == "darwin":
        return _uninstall_macos(label)
    if sys.platform == "linux":
        return _uninstall_linux(label)
    return False


def is_service_installed(label: str) -> bool:
    """Check if the service configuration exists."""
    if sys.platform == "darwin":
        return _get_macos_plist_path(label).exists()
    if sys.platform == "linux":
        return _get_linux_unit_path(label).exists()
    return False


# ---------------------------------------------------------------------------
# macOS (LaunchAgents)
# ---------------------------------------------------------------------------

def _get_macos_plist_path(label: str) -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{label}.plist"


def _install_macos(label: str, args: list[str], workspace_root: Path) -> bool:
    plist_path = _get_macos_plist_path(label)
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the Plist
    exec_path = sys.executable
    arg_xml = "\n        ".join(f"<string>{a}</string>" for a in args)
    
    # Logs go to the workspace logs dir
    log_dir = workspace_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = log_dir / "service.log"
    stderr_log = log_dir / "service.error.log"

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{exec_path}</string>
        {arg_xml}
    </array>
    
    <key>WorkingDirectory</key>
    <string>{workspace_root}</string>
    
    <key>RunAtLoad</key>
    <true/>
    
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    
    <key>StandardOutPath</key>
    <string>{stdout_log}</string>
    
    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>
</dict>
</plist>
"""
    try:
        plist_path.write_text(content, encoding="utf-8")
        # Try to bootstrap/load it immediately
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True, check=False)
        subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=True)
        return True
    except Exception as exc:
        logger.error("Failed to install macOS service %s: %s", label, exc)
        return False


def _uninstall_macos(label: str) -> bool:
    plist_path = _get_macos_plist_path(label)
    if not plist_path.exists():
        return False
    try:
        subprocess.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True, check=False)
        plist_path.unlink()
        return True
    except Exception as exc:
        logger.error("Failed to uninstall macOS service %s: %s", label, exc)
        return False


# ---------------------------------------------------------------------------
# Linux (systemd --user)
# ---------------------------------------------------------------------------

def _get_linux_unit_path(label: str) -> Path:
    return Path("~/.config/systemd/user").expanduser() / f"{label}.service"


def _install_linux(label: str, args: list[str], workspace_root: Path, description: str) -> bool:
    unit_path = _get_linux_unit_path(label)
    unit_path.parent.mkdir(parents=True, exist_ok=True)

    exec_path = sys.executable
    full_cmd = " ".join([exec_path] + args)
    
    content = f"""[Unit]
Description={description}
After=network.target

[Service]
Type=simple
ExecStart={full_cmd}
WorkingDirectory={workspace_root}
Restart=always
RestartSec=10
StandardOutput=append:{workspace_root}/logs/service.log
StandardError=append:{workspace_root}/logs/service.error.log

[Install]
WantedBy=default.target
"""
    try:
        unit_path.write_text(content, encoding="utf-8")
        (workspace_root / "logs").mkdir(parents=True, exist_ok=True)
        
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", label], check=True)
        return True
    except Exception as exc:
        logger.error("Failed to install Linux service %s: %s", label, exc)
        return False


def _uninstall_linux(label: str) -> bool:
    unit_path = _get_linux_unit_path(label)
    if not unit_path.exists():
        return False
    try:
        subprocess.run(["systemctl", "--user", "disable", "--now", label], capture_output=True, check=False)
        unit_path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        return True
    except Exception as exc:
        logger.error("Failed to uninstall Linux service %s: %s", label, exc)
        return False
