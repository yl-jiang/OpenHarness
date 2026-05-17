"""Configuration IO for the standalone solo app."""

from __future__ import annotations

from pathlib import Path

from openharness.config.schema import Config

from solo.models import SoloConfig
from solo.workspace import get_config_path


def load_config(workspace: str | Path | None = None) -> SoloConfig:
    path = get_config_path(workspace)
    if path.exists():
        return SoloConfig.model_validate_json(path.read_text(encoding="utf-8"))
    return SoloConfig()


def save_config(config: SoloConfig, workspace: str | Path | None = None) -> Path:
    path = get_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def build_channel_manager_config(config: SoloConfig) -> Config:
    root = Config()
    root.channels.send_progress = config.send_progress
    root.channels.send_tool_hints = config.send_tool_hints
    for name in config.enabled_channels:
        if not hasattr(root.channels, name):
            continue
        channel_config = getattr(root.channels, name).model_copy(
            update={"enabled": True, **config.channel_configs.get(name, {})}
        )
        setattr(root.channels, name, channel_config)
    return root
