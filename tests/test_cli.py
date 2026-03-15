"""CLI 行为测试"""

from __future__ import annotations

import json
from pathlib import Path


def test_main_without_args_enters_project_wizard(monkeypatch) -> None:
    """无参数启动时进入项目向导"""
    from agent_system import cli

    called = {"value": False}

    def _fake_wizard() -> int:
        called["value"] = True
        return 7

    monkeypatch.setattr(cli, "_run_project_wizard", _fake_wizard)

    exit_code = cli.main([])

    assert called["value"] is True
    assert exit_code == 7


def test_main_with_args_does_not_enter_task_wizard(monkeypatch) -> None:
    """有参数时保持原流程，不进入项目向导"""
    from agent_system import cli

    called = {"value": False}

    def _fake_wizard() -> int:
        called["value"] = True
        return 0

    monkeypatch.setattr(cli, "_run_project_wizard", _fake_wizard)

    exit_code = cli.main(["--status"])

    assert called["value"] is False
    assert exit_code == 1


def test_main_project_template_output(monkeypatch, capsys) -> None:
    """--project-template 输出最小 project.json 模板"""
    from agent_system import cli

    called = {"value": False}

    def _fake_wizard() -> int:
        called["value"] = True
        return 0

    monkeypatch.setattr(cli, "_run_project_wizard", _fake_wizard)

    exit_code = cli.main(["--project-template"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert called["value"] is False
    assert exit_code == 0
    assert payload["project_name"] == "my-project"
    assert payload["conventions_file"] == "docs/project-conventions.md"
    assert "prompt_overrides" in payload
    assert payload["prompt_overrides"]["task_wizard"] != ""
    assert payload["prompt_overrides"]["coder"] != ""


def test_main_task_wizard_with_project_passes_project_path(monkeypatch) -> None:
    """--task-wizard 可配合 --project 进入项目感知任务向导"""
    from agent_system import cli

    captured = {"project": None}

    def _fake_wizard(project_config_file: str = "") -> int:
        captured["project"] = project_config_file
        return 3

    monkeypatch.setattr(cli, "_run_task_wizard", _fake_wizard)

    exit_code = cli.main(["--task-wizard", "--project", "sample.json"])

    assert exit_code == 3
    assert captured["project"] == "sample.json"


def test_main_wizard_enters_project_wizard(monkeypatch) -> None:
    """--wizard 显式进入项目配置向导"""
    from agent_system import cli

    called = {"value": False}

    def _fake_wizard() -> int:
        called["value"] = True
        return 5

    monkeypatch.setattr(cli, "_run_project_wizard", _fake_wizard)

    exit_code = cli.main(["--wizard"])

    assert called["value"] is True
    assert exit_code == 5


def test_main_returns_error_when_git_check_fails(monkeypatch, capsys) -> None:
    """启动阶段 Git 校验失败时，CLI 应返回 1 并输出明确错误"""
    from agent_system import cli
    from agent_system.services.git_service import GitError

    class _FakeOrchestrator:
        def __init__(self, config):
            self.config = config

        def initialize(self) -> None:
            raise GitError("dirty worktree")

    import agent_system.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "Orchestrator", _FakeOrchestrator)

    exit_code = cli.main(["--project", "sample.json", "--status"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Git 启动校验失败" in captured.out


def test_main_loads_summary_settings_from_ini(monkeypatch, tmp_path: Path, capsys) -> None:
    """CLI 应支持从 ini 配置读取摘要参数。"""
    from agent_system import cli

    ini_path = tmp_path / "agent-system.ini"
    ini_path.write_text(
        "[agent]\n"
        "project = sample.json\n"
        "\n"
        "[summary]\n"
        "trigger_bytes = 4567890\n"
        "keep_recent_messages = 9\n"
        "keep_recent_log_entries = 7\n",
        encoding="utf-8",
    )

    captured_config = {"value": None}

    class _FakeOrchestrator:
        def __init__(self, config):
            captured_config["value"] = config

        def initialize(self) -> None:
            return None

        def resume_tasks(self) -> None:
            return None

        def get_status_report(self) -> str:
            return "ok"

    import agent_system.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "Orchestrator", _FakeOrchestrator)

    exit_code = cli.main(["--status", "--ini-config", str(ini_path)])
    captured = capsys.readouterr()

    config = captured_config["value"]
    assert exit_code == 0
    assert captured.out.strip() == "ok"
    assert config is not None
    assert config.project_config_file == "sample.json"
    assert config.summary_trigger_bytes == 4567890
    assert config.summary_keep_recent_messages == 9
    assert config.summary_keep_recent_log_entries == 7
