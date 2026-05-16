"""Configuration IO for the standalone self-log app."""

from __future__ import annotations

from pathlib import Path

from openharness.config.schema import Config

from self_log.models import SelfLogConfig
from self_log.workspace import get_config_path


def load_config(workspace: str | Path | None = None) -> SelfLogConfig:
    path = get_config_path(workspace)
    if path.exists():
        return SelfLogConfig.model_validate_json(path.read_text(encoding="utf-8"))
    return SelfLogConfig()


def save_config(config: SelfLogConfig, workspace: str | Path | None = None) -> Path:
    path = get_config_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def build_channel_manager_config(config: SelfLogConfig) -> Config:
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
