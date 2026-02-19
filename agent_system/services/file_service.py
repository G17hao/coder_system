"""文件读写服务"""

from __future__ import annotations

from pathlib import Path


class FileService:
    """文件操作服务 — 基于 pathlib"""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir).resolve()

    @property
    def base_dir(self) -> Path:
        return self._base

    def read(self, rel_path: str | Path) -> str:
        """读取文件内容

        Args:
            rel_path: 相对于 base_dir 的路径

        Returns:
            文件内容字符串

        Raises:
            FileNotFoundError: 文件不存在
        """
        full_path = self._resolve(rel_path)
        if not full_path.exists():
            raise FileNotFoundError(f"文件不存在: {full_path}")
        return full_path.read_text(encoding="utf-8")

    def read_lines(
        self, rel_path: str | Path, start: int = 1, end: int | None = None
    ) -> str:
        """读取文件指定行范围

        Args:
            rel_path: 相对路径
            start: 起始行号（1-based，含）
            end: 结束行号（1-based，含）；None 表示到文件末尾

        Returns:
            指定行范围的文本
        """
        content = self.read(rel_path)
        lines = content.splitlines(keepends=True)
        start_idx = max(0, start - 1)
        end_idx = end if end is not None else len(lines)
        return "".join(lines[start_idx:end_idx])

    def write(self, rel_path: str | Path, content: str) -> Path:
        """写入文件（自动创建中间目录）

        Args:
            rel_path: 相对路径
            content: 文件内容

        Returns:
            写入的文件绝对路径
        """
        full_path = self._resolve(rel_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return full_path

    def exists(self, rel_path: str | Path) -> bool:
        """文件是否存在"""
        return self._resolve(rel_path).exists()

    def list_files(self, rel_dir: str | Path = ".", pattern: str = "*") -> list[Path]:
        """列出目录下匹配模式的文件（相对路径）

        Args:
            rel_dir: 相对目录
            pattern: glob 模式

        Returns:
            相对于 base_dir 的文件路径列表
        """
        full_dir = self._resolve(rel_dir)
        if not full_dir.is_dir():
            return []
        return [
            p.relative_to(self._base)
            for p in full_dir.rglob(pattern)
            if p.is_file()
        ]

    def _resolve(self, rel_path: str | Path) -> Path:
        """解析相对路径为绝对路径"""
        return (self._base / rel_path).resolve()
