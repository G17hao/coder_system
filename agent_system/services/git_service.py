"""Git 操作服务"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """Git 操作失败"""
    pass


class GitService:
    """Git CLI 封装"""

    def __init__(self, repo_dir: str | Path) -> None:
        self._repo = Path(repo_dir).resolve()

    def _run(self, *args: str, check: bool = True) -> str:
        """执行 git 命令

        Args:
            *args: git 子命令和参数
            check: 是否检查返回码

        Returns:
            命令 stdout 输出

        Raises:
            GitError: 命令执行失败
        """
        cmd = ["git", *args]
        try:
            result = subprocess.run(
                cmd,
                cwd=self._repo,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if check and result.returncode != 0:
                raise GitError(
                    f"git {' '.join(args)} 失败 (exit={result.returncode}): "
                    f"{result.stderr.strip()}"
                )
            return result.stdout.strip()
        except subprocess.TimeoutExpired as e:
            raise GitError(f"git {' '.join(args)} 超时") from e
        except FileNotFoundError as e:
            raise GitError("git 未安装或不在 PATH 中") from e

    def current_branch(self) -> str:
        """获取当前分支名"""
        return self._run("rev-parse", "--abbrev-ref", "HEAD")

    def create_branch(self, name: str) -> None:
        """创建并切换到新分支（如果不存在）"""
        current = self.current_branch()
        if current == name:
            return
        # 检查分支是否已存在
        branches = self._run("branch", "--list", name)
        if name in branches:
            self._run("checkout", name)
        else:
            self._run("checkout", "-b", name)

    def add_all(self) -> None:
        """暂存所有变更"""
        self._run("add", "-A")

    def commit(self, message: str) -> str:
        """提交变更

        Returns:
            commit hash
        """
        self._run("commit", "-m", message)
        return self._run("rev-parse", "HEAD")

    def checkout_files(self, *paths: str) -> None:
        """撤销文件变更（恢复到 HEAD 版本）"""
        if paths:
            self._run("checkout", "HEAD", "--", *paths)
        else:
            self._run("checkout", "HEAD", "--", ".")
            # 同时清理 agent 创建的未跟踪新文件
            self._run("clean", "-fd")

    def has_changes(self) -> bool:
        """是否有未提交的变更"""
        status = self._run("status", "--porcelain")
        return len(status) > 0

    def log_oneline(self, count: int = 10) -> str:
        """获取简洁提交日志"""
        return self._run("log", f"--oneline", f"-{count}")
