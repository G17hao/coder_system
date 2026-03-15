"""项目配置对话向导（用于创建 project.json）"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


def _ask_non_empty(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("输入不能为空，请重试。")


def _ask_optional(prompt: str, default: str = "") -> str:
    value = input(prompt).strip()
    if value:
        return value
    return default


def _ask_csv(prompt: str) -> list[str]:
    raw = input(prompt).strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_file_stem(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip().lower())
    normalized = normalized.strip("-._")
    return normalized or "my-project"


def _build_project_payload() -> dict[str, object]:
    print("\n[项目助手] 进入项目配置创建向导（输入 q 可随时退出）")

    project_name = _ask_non_empty("1) 项目名称: ")
    if project_name.lower() == "q":
        raise KeyboardInterrupt

    project_description = _ask_non_empty("2) 项目描述: ")
    if project_description.lower() == "q":
        raise KeyboardInterrupt

    project_root = _ask_non_empty("3) 目标项目根目录: ")
    if project_root.lower() == "q":
        raise KeyboardInterrupt

    reference_roots = _ask_csv("4) 参考代码目录（逗号分隔，可留空）: ")
    git_branch = _ask_optional("5) 默认工作分支（默认 feat/agent-auto）: ", default="feat/agent-auto")
    if git_branch.lower() == "q":
        raise KeyboardInterrupt

    conventions_file = _ask_optional(
        "6) 项目约定文件相对路径（默认 docs/project-conventions.md）: ",
        default="docs/project-conventions.md",
    )
    if conventions_file.lower() == "q":
        raise KeyboardInterrupt

    coding_conventions = _ask_optional("7) 编码规范摘要（可留空）: ", default="")
    if coding_conventions.lower() == "q":
        raise KeyboardInterrupt

    review_checklist = _ask_csv("8) Review 检查项（逗号分隔，可留空）: ")
    review_commands = _ask_csv("9) Review 命令（逗号分隔，可留空）: ")
    task_categories = _ask_csv("10) 任务分类（逗号分隔，可留空）: ")

    print("11) 项目特定提示覆盖（可留空）")
    task_wizard_prompt = _ask_optional("  task_wizard: ", default="")
    planner_prompt = _ask_optional("  planner: ", default="")
    analyst_prompt = _ask_optional("  analyst: ", default="")
    coder_prompt = _ask_optional("  coder: ", default="")
    reviewer_prompt = _ask_optional("  reviewer: ", default="")
    supervisor_prompt = _ask_optional("  supervisor: ", default="")

    prompt_overrides = {
        "task_wizard": task_wizard_prompt,
        "planner": planner_prompt,
        "analyst": analyst_prompt,
        "coder": coder_prompt,
        "reviewer": reviewer_prompt,
        "supervisor": supervisor_prompt,
    }

    return {
        "project_name": project_name,
        "project_description": project_description,
        "project_root": project_root,
        "reference_roots": reference_roots,
        "git_branch": git_branch,
        "coding_conventions": coding_conventions,
        "conventions_file": conventions_file,
        "pattern_mappings": [],
        "review_checklist": review_checklist,
        "review_commands": review_commands,
        "prompt_overrides": prompt_overrides,
        "email_approval": {"enabled": False},
        "task_categories": task_categories,
        "initial_tasks": [],
    }


def _render_preview(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _save_payload(payload: dict[str, object]) -> Path:
    project_name = str(payload.get("project_name", "my-project"))
    default_path = Path("projects") / f"{_normalize_file_stem(project_name)}.json"
    target = _ask_optional(
        f"\n保存路径（默认 {default_path.as_posix()}）: ",
        default=default_path.as_posix(),
    )
    target_path = Path(target)
    if target_path.suffix != ".json":
        target_path = target_path.with_suffix(".json")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    save_payload = dict(payload)
    save_payload["created_at"] = datetime.now().isoformat(timespec="seconds")
    target_path.write_text(json.dumps(save_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target_path


def run_project_wizard() -> int:
    """运行项目配置创建向导"""
    try:
        payload = _build_project_payload()
        print("\n=== project.json 草案 ===")
        print(_render_preview(payload))
        confirm = _ask_optional("\n确认保存项目配置？(Y/n): ", default="y").lower()
        if confirm not in ("y", "yes"):
            print("已取消保存。")
            return 0
        saved_path = _save_payload(payload)
        print(f"已保存项目配置: {saved_path.as_posix()}")
        print("下一步可使用: python -m agent_system --task-wizard --project <project.json>")
        return 0
    except KeyboardInterrupt:
        print("\n已退出项目配置创建向导。")
        return 0