"""CLI 行为测试"""

from __future__ import annotations


def test_main_without_args_enters_task_wizard(monkeypatch) -> None:
    """无参数启动时进入任务向导"""
    from agent_system import cli

    called = {"value": False}

    def _fake_wizard() -> int:
        called["value"] = True
        return 7

    monkeypatch.setattr(cli, "_run_task_wizard", _fake_wizard)

    exit_code = cli.main([])

    assert called["value"] is True
    assert exit_code == 7


def test_main_with_args_does_not_enter_task_wizard(monkeypatch) -> None:
    """有参数时保持原流程，不进入任务向导"""
    from agent_system import cli

    called = {"value": False}

    def _fake_wizard() -> int:
        called["value"] = True
        return 0

    monkeypatch.setattr(cli, "_run_task_wizard", _fake_wizard)

    exit_code = cli.main(["--status"])

    assert called["value"] is False
    assert exit_code == 1
