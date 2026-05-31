"""Agentic OpenCLI research layer for feed_digest."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from openharness.utils.log import get_logger

from feed_digest.config import ResearchConfig
from feed_digest.models import FeedItem, SourceStats

logger = get_logger(__name__)


@dataclass(frozen=True)
class OpenCliCommand:
    site: str
    name: str
    strategy: str = ""
    browser: bool = True
    description: str = ""
    args: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchAction:
    source: str
    site: str
    command: str
    args: list[str] = field(default_factory=list)
    reason: str = ""

    def argv(self) -> list[str]:
        return ["opencli", self.site, self.command, *self.args]


@dataclass(frozen=True)
class ResearchDecision:
    actions: list[ResearchAction] = field(default_factory=list)
    done: bool = False
    rationale: str = ""


@dataclass
class RawEvidence:
    source: str
    command: str
    content: str = ""
    error: str = ""
    elapsed_s: float = 0.0

    @property
    def failed(self) -> bool:
        return bool(self.error)


@dataclass
class ResearchResult:
    items: list[FeedItem] = field(default_factory=list)
    source_stats: list[SourceStats] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence: list[RawEvidence] = field(default_factory=list)


class RegistryProvider(Protocol):
    def load(self) -> list[OpenCliCommand]: ...


class EvidenceRunner(Protocol):
    async def run(
        self,
        action: ResearchAction,
        *,
        catalog: list[OpenCliCommand],
        timeout_seconds: int,
        max_output_chars: int,
    ) -> RawEvidence: ...


class OpenCliRegistry:
    """Loads the installed OpenCLI adapter catalog."""

    def __init__(self, *, timeout_seconds: int = 10) -> None:
        self._timeout_seconds = timeout_seconds

    def load(self) -> list[OpenCliCommand]:
        env = os.environ.copy()
        env["OPENCLI_BROWSER_COMMAND_TIMEOUT"] = str(self._timeout_seconds)
        result = subprocess.run(
            ["opencli", "list", "-f", "json"],
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout).strip()
            raise RuntimeError(stderr or f"opencli list exited with {result.returncode}")
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("opencli list did not return JSON") from exc
        if not isinstance(payload, list):
            raise RuntimeError("opencli list JSON must be an array")
        commands: list[OpenCliCommand] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            site = str(entry.get("site") or "")
            name = str(entry.get("name") or "")
            if not site or not name:
                continue
            commands.append(
                OpenCliCommand(
                    site=site,
                    name=name,
                    strategy=str(entry.get("strategy") or ""),
                    browser=bool(entry.get("browser")),
                    description=str(entry.get("description") or ""),
                    args=list(entry.get("args") or []),
                )
            )
        return commands


class OpenCliRunner:
    """Runs model-selected OpenCLI commands inside a registry allowlist."""

    async def run(
        self,
        action: ResearchAction,
        *,
        catalog: list[OpenCliCommand],
        timeout_seconds: int,
        max_output_chars: int,
    ) -> RawEvidence:
        self._validate(action, catalog)
        argv = action.argv()
        command_display = shlex.join(argv)
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(
                self._run_subprocess,
                argv,
                timeout_seconds,
            )
        except Exception as exc:
            return RawEvidence(
                source=action.source,
                command=command_display,
                error=str(exc),
                elapsed_s=time.monotonic() - started,
            )
        elapsed = time.monotonic() - started
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            return RawEvidence(
                source=action.source,
                command=command_display,
                error=stderr or stdout or f"command exited with {result.returncode}",
                elapsed_s=elapsed,
            )
        return RawEvidence(
            source=action.source,
            command=command_display,
            content=stdout[:max_output_chars],
            elapsed_s=elapsed,
        )

    @staticmethod
    def _validate(action: ResearchAction, catalog: list[OpenCliCommand]) -> None:
        if not any(cmd.site == action.site and cmd.name == action.command for cmd in catalog):
            raise ValueError(f"OpenCLI command {action.site}/{action.command} is not present in OpenCLI catalog")

    @staticmethod
    def _run_subprocess(argv: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.setdefault("OPENCLI_BROWSER_CONNECT_TIMEOUT", "10")
        env["OPENCLI_BROWSER_COMMAND_TIMEOUT"] = str(timeout_seconds)
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )


class FeedDigestResearcher:
    """Lets the AI pipeline actively plan, run, critique, and extract OpenCLI evidence."""

    def __init__(
        self,
        *,
        pipeline: Any,
        registry: RegistryProvider | None = None,
        runner: EvidenceRunner | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._registry = registry
        self._runner = runner or OpenCliRunner()

    async def collect(
        self,
        *,
        objective: str,
        domain: str,
        config: ResearchConfig,
        seed_actions: list[ResearchAction] | None = None,
    ) -> ResearchResult:
        warnings: list[str] = []
        try:
            logger.info("# Stage 1: Load OpenCLI catalog")
            registry = self._registry or OpenCliRegistry(timeout_seconds=config.budget.registry_timeout_seconds)
            catalog = registry.load()
            logger.info("Loaded OpenCLI catalog with %d commands", len(catalog))
        except Exception as exc:
            logger.warning("OpenCLI catalog discovery failed: %s", exc)
            return ResearchResult(warnings=[f"OpenCLI catalog discovery failed: {exc}"])

        catalog = self._filter_catalog(catalog, config)
        logger.info(
            "OpenCLI catalog ready: %d commands (browser_adapters=%s)",
            len(catalog),
            config.allow_browser_adapters,
        )
        if not catalog:
            return ResearchResult(warnings=["OpenCLI catalog is empty after applying environment constraints"])

        evidence: list[RawEvidence] = []

        # Run mandatory seed actions before the LLM planning loop so that
        # high-priority sources (e.g. GitHub Trending) are always collected.
        if seed_actions:
            logger.info("# Stage 2a: Running %d seed action(s)", len(seed_actions))
            seed_results = await asyncio.gather(
                *[
                    self._runner.run(
                        action,
                        catalog=catalog,
                        timeout_seconds=config.budget.command_timeout_seconds,
                        max_output_chars=config.budget.max_output_chars,
                    )
                    for action in seed_actions
                ]
            )
            evidence.extend(seed_results)
            for ev in seed_results:
                if ev.failed:
                    logger.warning("Seed action %s failed: %s", ev.command, ev.error)
                else:
                    logger.info("Seed action %s: %d chars", ev.command, len(ev.content))

        actions_used = 0
        empty_rounds = 0
        logger.info("# Stage 2: AI-driven OpenCLI research")
        for _round in range(config.budget.max_rounds):
            remaining = config.budget.max_actions - actions_used
            if remaining <= 0:
                break
            decision = await self._pipeline.plan_research_actions(
                objective=objective,
                domain=domain,
                catalog=catalog,
                evidence=evidence,
                max_actions=min(config.budget.max_actions_per_round, remaining),
            )

            actions = decision.actions[: min(config.budget.max_actions_per_round, remaining)]
            if actions:
                empty_rounds = 0
                logger.info(
                    "AI research planned %d actions (round %d): %s",
                    len(actions),
                    _round + 1,
                    [f"{a.site}/{a.command}" for a in actions],
                )
                results = await asyncio.gather(
                    *[
                        self._runner.run(
                            action,
                            catalog=catalog,
                            timeout_seconds=config.budget.command_timeout_seconds,
                            max_output_chars=config.budget.max_output_chars,
                        )
                        for action in actions
                    ]
                )
                evidence.extend(results)
                actions_used += len(actions)

            if not actions:
                logger.info("AI research returned no actions (round %d)", _round + 1)
                sources_covered = {ev.source for ev in evidence if not ev.failed}
                if len(sources_covered) < config.budget.min_unique_sources and empty_rounds < 1:
                    empty_rounds += 1
                    logger.info(
                        "Retrying: only %d/%d unique sources covered (round %d)",
                        len(sources_covered),
                        config.budget.min_unique_sources,
                        _round + 1,
                    )
                    continue
                break

            if decision.done:
                sources_covered = {ev.source for ev in evidence if not ev.failed}
                if len(sources_covered) >= config.budget.min_unique_sources:
                    logger.info(
                        "AI research done=true accepted: %d sources covered (round %d)",
                        len(sources_covered),
                        _round + 1,
                    )
                    break
                logger.info(
                    "AI research done=true overridden: only %d/%d unique sources covered (round %d)",
                    len(sources_covered),
                    config.budget.min_unique_sources,
                    _round + 1,
                )

        try:
            logger.info("# Stage 3: AI evidence extraction and item scoring")
            items = await self._pipeline.extract_items_from_evidence(
                evidence,
                domain=domain,
                objective=objective,
                max_items=config.budget.max_actions * 5,
            )
        except Exception as exc:
            logger.warning("AI evidence extraction failed: %s", exc)
            items = []
            warnings.append(f"AI evidence extraction failed: {exc}")

        return ResearchResult(
            items=items,
            source_stats=self._build_stats(evidence, items),
            warnings=warnings,
            evidence=evidence,
        )

    @staticmethod
    def _filter_catalog(catalog: list[OpenCliCommand], config: ResearchConfig) -> list[OpenCliCommand]:
        """Keep only commands compatible with the current environment."""
        return [
            command for command in catalog
            if config.allow_browser_adapters or not command.browser
        ]

    @staticmethod
    def _build_stats(
        evidence: list[RawEvidence],
        items: list[FeedItem],
    ) -> list[SourceStats]:
        sources = {ev.source for ev in evidence} | {item.source for item in items}
        stats: list[SourceStats] = []
        for source in sorted(sources):
            source_evidence = [ev for ev in evidence if ev.source == source]
            source_items = [item for item in items if item.source == source]
            errors = [ev.error for ev in source_evidence if ev.error]
            warning = "; ".join(errors)[:200]
            failed = bool(errors and not source_items)
            stats.append(
                SourceStats(
                    source=source,
                    fetched=len(source_items),
                    failed=failed,
                    warning=warning,
                )
            )
        return stats
