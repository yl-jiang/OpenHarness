"""Tests for project template registry and service-level project creation."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from common.project_ai.templates import (
    SOLO_TEMPLATES,
    WOLO_TEMPLATES,
    ProjectTemplate,
    get_template,
    list_templates,
)
from solo.core.models import Milestone, Project
from solo.core.store import SoloStore
from solo.core.utils import _now


# ── Template registry tests ──────────────────────────────────────


class TestListTemplates:
    def test_list_templates_solo(self) -> None:
        result = list_templates("solo")
        assert len(result) == 4
        assert all(isinstance(t, ProjectTemplate) for t in result)
        ids = {t.id for t in result}
        assert ids == {"solo_goal", "solo_learning", "solo_health", "solo_creative"}

    def test_list_templates_wolo(self) -> None:
        result = list_templates("wolo")
        assert len(result) == 4
        assert all(isinstance(t, ProjectTemplate) for t in result)
        ids = {t.id for t in result}
        assert ids == {"wolo_deliverable", "wolo_initiative", "wolo_research", "wolo_ops"}

    def test_list_templates_all(self) -> None:
        result = list_templates()
        assert len(result) == 8


class TestGetTemplate:
    def test_get_template_found(self) -> None:
        tpl = get_template("solo_goal")
        assert tpl is not None
        assert tpl.id == "solo_goal"
        assert tpl.label == "个人目标"
        assert tpl.priority == "high"
        assert len(tpl.milestones) == 4

    def test_get_template_not_found(self) -> None:
        assert get_template("nonexistent") is None


class TestTemplateToDict:
    def test_template_to_dict(self) -> None:
        tpl = get_template("solo_goal")
        assert tpl is not None
        d = tpl.to_dict()
        assert set(d.keys()) == {"id", "label", "description", "priority", "tags", "milestones"}
        assert d["id"] == "solo_goal"
        assert isinstance(d["milestones"], list)
        assert len(d["milestones"]) == 4
        assert d["priority"] == "high"


# ── Service integration tests (store-level) ──────────────────────


def _create_project_with_template(
    store: SoloStore, template_id: str, title: str = "Test Project"
) -> str:
    """Mimic SoloService.create_project template logic."""
    tpl = get_template(template_id)
    now = _now()
    project = Project(
        id=str(uuid4()),
        title=title,
        description=tpl.description if tpl else "",
        priority=tpl.priority if tpl else "medium",
        tags=tpl.tags if tpl else "",
        created_at=now,
        updated_at=now,
    )
    store.create_project(project)

    if tpl and tpl.milestones:
        for ms_title in tpl.milestones:
            ms = Milestone(
                id=str(uuid4()),
                project_id=project.id,
                title=ms_title,
                created_at=now,
                updated_at=now,
            )
            store.create_milestone(ms)

    return project.id


class TestCreateProjectWithTemplate:
    def test_create_project_with_template_solo(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        pid = _create_project_with_template(store, "solo_goal")

        milestones = store.list_milestones(pid)
        tpl = get_template("solo_goal")
        assert tpl is not None
        assert len(milestones) == len(tpl.milestones)
        ms_titles = [m.title for m in milestones]
        for expected_title in tpl.milestones:
            assert expected_title in ms_titles

        project = store.get_project(pid)
        assert project is not None
        assert project.description == tpl.description
        assert project.priority == tpl.priority
        assert project.tags == tpl.tags

    def test_create_project_without_template_solo(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        now = _now()
        project = Project(
            id=str(uuid4()),
            title="No Template",
            created_at=now,
            updated_at=now,
        )
        store.create_project(project)

        milestones = store.list_milestones(project.id)
        assert len(milestones) == 0
