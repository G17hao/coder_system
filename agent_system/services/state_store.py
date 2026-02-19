"""状态持久化服务 — JSON 文件读写 + 断点恢复"""

from __future__ import annotations

import json
from pathlib import Path

from agent_system.models.task import Task


class StateStore:
    """任务状态持久化到 JSON 文件"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def save(self, tasks: list[Task]) -> None:
        """保存任务列表到 JSON 文件"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "tasks": [t.to_dict() for t in tasks],
        }
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self) -> list[Task]:
        """从 JSON 文件加载任务列表"""
        if not self._path.exists():
            return []

        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return [Task.from_dict(t) for t in data.get("tasks", [])]

    def exists(self) -> bool:
        """状态文件是否存在"""
        return self._path.exists()
