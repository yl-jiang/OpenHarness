"""Tests for browser-launching behavior in authentication flows."""

from __future__ import annotations

from typing import Any

import pytest

from openharness.auth.flows import DeviceCodeFlow


class _FakeProc:
    returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def popen_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _capture(*args: Any, **kwargs: Any) -> _FakeProc:
        calls.append((args, kwargs))
        return _FakeProc()

    monkeypatch.setattr("openharness.auth.flows.subprocess.Popen", _capture)
    return calls


@pytest.fixture
def startfile_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        "openharness.auth.flows.os.startfile",
        lambda url: calls.append(url),
        raising=False,
    )
    return calls


def test_open_browser_windows_uses_startfile_not_shell(
    monkeypatch: pytest.MonkeyPatch,
    popen_calls: list[tuple[tuple[Any, ...], dict[str, Any]]],
    startfile_calls: list[str],
) -> None:
    monkeypatch.setattr("openharness.auth.flows.platform.system", lambda: "Windows")

    url = "https://github.com/login/device&calc.exe"
    opened = DeviceCodeFlow._try_open_browser(url)

    assert opened is True
    assert startfile_calls == [url]
    assert all(not kwargs.get("shell") for _, kwargs in popen_calls)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://example.com/payload",
        "",
        "calc.exe",
    ],
)
def test_open_browser_rejects_non_http_scheme(
    monkeypatch: pytest.MonkeyPatch,
    popen_calls: list[tuple[tuple[Any, ...], dict[str, Any]]],
    startfile_calls: list[str],
    url: str,
) -> None:
    monkeypatch.setattr("openharness.auth.flows.platform.system", lambda: "Windows")

    assert DeviceCodeFlow._try_open_browser(url) is False
    assert startfile_calls == []
    assert popen_calls == []


def test_open_browser_macos_uses_open_argv(
    monkeypatch: pytest.MonkeyPatch,
    popen_calls: list[tuple[tuple[Any, ...], dict[str, Any]]],
) -> None:
    monkeypatch.setattr("openharness.auth.flows.platform.system", lambda: "Darwin")

    assert DeviceCodeFlow._try_open_browser("https://example.com/login") is True
    assert len(popen_calls) == 1
    args, kwargs = popen_calls[0]
    assert args[0] == ["open", "https://example.com/login"]
    assert kwargs.get("shell") is not True


def test_open_browser_linux_uses_xdg_open_argv(
    monkeypatch: pytest.MonkeyPatch,
    popen_calls: list[tuple[tuple[Any, ...], dict[str, Any]]],
) -> None:
    monkeypatch.setattr("openharness.auth.flows.platform.system", lambda: "Linux")

    assert DeviceCodeFlow._try_open_browser("https://example.com/login") is True
    assert len(popen_calls) == 1
    args, kwargs = popen_calls[0]
    assert args[0] == ["xdg-open", "https://example.com/login"]
    assert kwargs.get("shell") is not True
