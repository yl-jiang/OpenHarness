#!/usr/bin/env python3
"""Cron runner script for wolo feed digest."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))


async def _main(app: str, workspace: str | None, domain: str | None, push: bool) -> int:
    from openharness.utils.log import configure_logging, get_logger

    configure_logging("INFO")
    logger = get_logger(__name__)

    try:
        from wolo.feed_digest import run_feed_digest

        report = await run_feed_digest(workspace=workspace, domain_name=domain)
        logger.info(
            "Feed digest complete app=%s id=%s is_empty=%s",
            app,
            report.id,
            (report.metadata or {}).get("is_empty"),
        )

        if push and report.content:
            await _push_to_im(workspace, report.content)

        meta = report.metadata or {}
        print(
            f"Feed digest done: domain={meta.get('domain')} date={meta.get('date')} "
            f"selected={meta.get('selected_count', 0)} is_empty={meta.get('is_empty')}"
        )
        return 0
    except Exception as exc:
        logger.error("Feed digest failed: %s", exc, exc_info=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


async def _push_to_im(workspace: str | None, content: str) -> None:
    from openharness.utils.log import get_logger

    logger = get_logger(__name__)
    try:
        from wolo.config import load_config
        from wolo.core.session import list_conversations
        from wolo.core.workspace import get_workspace_root
        from wolo.gateway.cron_scheduler import _send_feishu_dm

        config = load_config(workspace)
        channel_configs = config.channel_configs or {}
        feishu_cfg = channel_configs.get("feishu", {})
        user_open_id = feishu_cfg.get("owner_open_id") or feishu_cfg.get("user_open_id")
        if not user_open_id:
            root = get_workspace_root(workspace)
            for item in list_conversations(root, limit=20):
                key = str(item.get("session_key") or "")
                if ":" in key:
                    channel, chat_id = key.split(":", 1)
                    if channel == "feishu" and chat_id:
                        user_open_id = chat_id
                        break
        if not user_open_id:
            logger.warning("No IM push target found; digest archived but not pushed")
            return
        await _send_feishu_dm(user_open_id=user_open_id, content=content, workspace=workspace)
        logger.info("Pushed wolo feed digest to feishu DM")
    except Exception as exc:
        logger.warning("IM push failed (digest still archived): %s", exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", default="wolo")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--domain", default=None, help="Single domain to run; defaults to all enable_domains")
    parser.add_argument("--no-push", dest="push", action="store_false")
    parser.set_defaults(push=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.app, args.workspace, args.domain, args.push)))
