"""路径约束工具：限制文件操作在允许根目录内。"""

from __future__ import annotations

from pathlib import Path


class PathGuard:
    """文件路径白名单守卫。"""

    def __init__(
        self,
        allowed_roots: list[str] | None,
        default_base_dir: str | None,
    ) -> None:
        self.allowed_roots: list[Path] = [
            Path(p).resolve() for p in (allowed_roots or []) if str(p).strip()
        ]
        self.default_base: Path | None = (
            Path(default_base_dir).resolve()
            if default_base_dir and str(default_base_dir).strip()
            else None
        )

    def resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute() and self.default_base is not None:
            candidate = self.default_base / candidate
        return candidate.resolve()

    def is_allowed(self, path: Path) -> bool:
        if not self.allowed_roots:
            return True
        candidate = path.resolve()
        return any(root == candidate or root in candidate.parents for root in self.allowed_roots)

    def validate_file(self, raw_path: str) -> tuple[str | None, str | None]:
        """验证并规范化文件路径。"""
        resolved = self.resolve_path(raw_path)
        if not self.is_allowed(resolved):
            return (None, f"[路径约束] 文件路径不在允许范围: {resolved}")
        return (str(resolved), None)

    def clamp_dir(self, raw_path: str) -> tuple[str, str | None]:
        """目录路径不合法时回退到默认根目录。"""
        resolved = self.resolve_path(raw_path)
        if self.is_allowed(resolved):
            return (str(resolved), None)
        if self.default_base is not None:
            return (
                str(self.default_base),
                f"[路径约束] 目录 {resolved} 超出允许范围，已回退到 {self.default_base}",
            )
        return (str(resolved), f"[路径约束] 目录 {resolved} 超出允许范围")
