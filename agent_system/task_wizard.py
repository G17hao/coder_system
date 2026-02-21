"""任务列表对话向导（无参启动入口）"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class WizardTask:
    """对话向导中的任务项"""

    id: str
    title: str
    description: str
    priority: int
    dependencies: list[str] = field(default_factory=list)


@dataclass
class WizardResult:
    """对话向导输出"""

    goal: str
    scope_in: str
    scope_out: str
    constraints: str
    tasks: list[WizardTask]


def _ask_non_empty(prompt: str) -> str:
    """读取非空输入"""
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("输入不能为空，请重试。")


def _ask_optional(prompt: str, default: str = "") -> str:
    """读取可选输入"""
    value = input(prompt).strip()
    if value:
        return value
    return default


def _ask_priority(default: int = 2) -> int:
    """读取优先级（0~3）"""
    while True:
        raw = input(f"  优先级(0最高~3最低，默认{default}): ").strip()
        if not raw:
            return default
        if raw.isdigit() and 0 <= int(raw) <= 3:
            return int(raw)
        print("  输入无效，请输入 0~3 的整数。")


def _ask_dependencies(existing_ids: list[str]) -> list[str]:
    """读取依赖任务 ID 列表"""
    if not existing_ids:
        return []
    raw = input("  依赖任务ID（逗号分隔，留空表示无依赖）: ").strip()
    if not raw:
        return []
    deps = [item.strip() for item in raw.split(",") if item.strip()]
    return [dep for dep in deps if dep in existing_ids]


def _render_preview(result: WizardResult) -> str:
    """渲染任务预览文本"""
    lines: list[str] = []
    lines.append("\n=== 任务列表草案 ===")
    lines.append(f"目标: {result.goal}")
    lines.append(f"范围(包含): {result.scope_in}")
    lines.append(f"范围(不含): {result.scope_out}")
    lines.append(f"约束: {result.constraints}")
    lines.append("任务:")
    for task in result.tasks:
        deps = ", ".join(task.dependencies) if task.dependencies else "无"
        lines.append(
            f"- {task.id} | P{task.priority} | {task.title} | 依赖: {deps}\n"
            f"  {task.description}"
        )
    return "\n".join(lines)


def _build_result() -> WizardResult:
    """对话构建任务列表"""
    print("\n[任务助手] 进入任务列表创建向导（输入 q 可随时退出）")

    goal = _ask_non_empty("1) 这次要达成的目标是: ")
    if goal.lower() == "q":
        raise KeyboardInterrupt

    scope_in = _ask_optional("2) 范围（包含）: ", default="按目标默认范围")
    if scope_in.lower() == "q":
        raise KeyboardInterrupt

    scope_out = _ask_optional("3) 范围（不包含）: ", default="无")
    if scope_out.lower() == "q":
        raise KeyboardInterrupt

    constraints = _ask_optional("4) 约束（技术/时间/风险）: ", default="无")
    if constraints.lower() == "q":
        raise KeyboardInterrupt

    task_count_raw = _ask_optional("5) 先创建多少条任务（默认 5）: ", default="5")
    if task_count_raw.lower() == "q":
        raise KeyboardInterrupt
    task_count = 5
    if task_count_raw.isdigit() and int(task_count_raw) > 0:
        task_count = int(task_count_raw)

    tasks: list[WizardTask] = []
    for idx in range(1, task_count + 1):
        task_id = f"T{idx}"
        print(f"\n- 创建任务 {task_id}")
        title = _ask_non_empty("  标题: ")
        if title.lower() == "q":
            raise KeyboardInterrupt
        description = _ask_optional("  描述: ", default=title)
        if description.lower() == "q":
            raise KeyboardInterrupt
        priority = _ask_priority(default=2)
        deps = _ask_dependencies([task.id for task in tasks])
        tasks.append(
            WizardTask(
                id=task_id,
                title=title,
                description=description,
                priority=priority,
                dependencies=deps,
            )
        )

    return WizardResult(
        goal=goal,
        scope_in=scope_in,
        scope_out=scope_out,
        constraints=constraints,
        tasks=tasks,
    )


def _save_result(result: WizardResult) -> Path:
    """保存任务列表为 JSON"""
    default_path = Path("state/wizard_tasks.json")
    target = _ask_optional(
        f"\n保存路径（默认 {default_path.as_posix()}）: ",
        default=default_path.as_posix(),
    )
    target_path = Path(target)
    if target_path.suffix == "":
        target_path = target_path / "wizard_tasks.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "goal": result.goal,
        "scope_in": result.scope_in,
        "scope_out": result.scope_out,
        "constraints": result.constraints,
        "tasks": [asdict(task) for task in result.tasks],
    }
    target_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target_path


def run_task_wizard() -> int:
    """运行任务列表对话向导"""
    try:
        result = _build_result()
        print(_render_preview(result))
        confirm = _ask_optional("\n确认保存任务列表？(Y/n): ", default="y").lower()
        if confirm not in ("y", "yes"):
            print("已取消保存。")
            return 0
        saved_path = _save_result(result)
        print(f"已保存任务列表: {saved_path.as_posix()}")
        return 0
    except KeyboardInterrupt:
        print("\n已退出任务列表创建向导。")
        return 0
