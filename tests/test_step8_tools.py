"""Step 8: 新增工具测试 — grep_content, list_directory, replace_in_file, project_structure, ts_check, diff_file"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


# ── grep_content ───────────────────────────────────────────────

class TestGrepContent:
    """grep_content_tool 测试"""

    def test_grep_single_file(self, tmp_path: Path) -> None:
        """在单个文件中搜索"""
        from agent_system.tools.grep_content import grep_content_tool

        f = tmp_path / "test.ts"
        f.write_text(
            "import { Vec2 } from 'cc';\n"
            "export class Player {\n"
            "  public name: string;\n"
            "  public move(dir: Vec2): void {}\n"
            "}\n",
            encoding="utf-8",
        )

        results = grep_content_tool(str(f), r"export class")
        assert len(results) == 1
        assert results[0]["line"] == 2
        assert "Player" in results[0]["content"]

    def test_grep_no_match(self, tmp_path: Path) -> None:
        """无匹配时返回空列表"""
        from agent_system.tools.grep_content import grep_content_tool

        f = tmp_path / "test.ts"
        f.write_text("const x = 1;", encoding="utf-8")

        results = grep_content_tool(str(f), r"class Foo")
        assert results == []

    def test_grep_nonexistent(self) -> None:
        """文件不存在时返回错误信息"""
        from agent_system.tools.grep_content import grep_content_tool

        results = grep_content_tool("/nonexistent/file.ts", r"test")
        assert len(results) == 1
        assert "不存在" in results[0]["content"]

    def test_grep_dir(self, tmp_path: Path) -> None:
        """在目录中搜索"""
        from agent_system.tools.grep_content import grep_dir_tool

        (tmp_path / "a.ts").write_text("export class Foo {}", encoding="utf-8")
        (tmp_path / "b.ts").write_text("export class Bar {}", encoding="utf-8")
        (tmp_path / "c.lua").write_text("function Baz()", encoding="utf-8")

        results = grep_dir_tool(str(tmp_path), r"export class", file_pattern="*.ts")
        assert len(results) == 2

    def test_grep_max_matches(self, tmp_path: Path) -> None:
        """max_matches 限制"""
        from agent_system.tools.grep_content import grep_content_tool

        lines = [f"match line {i}" for i in range(100)]
        f = tmp_path / "big.txt"
        f.write_text("\n".join(lines), encoding="utf-8")

        results = grep_content_tool(str(f), r"match", max_matches=5)
        assert len(results) == 5


# ── list_directory ─────────────────────────────────────────────

class TestListDirectory:
    """list_directory_tool 测试"""

    def test_basic_tree(self, tmp_path: Path) -> None:
        """基本目录树"""
        from agent_system.tools.list_directory import list_directory_tool

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text("", encoding="utf-8")
        (tmp_path / "src" / "utils").mkdir()
        (tmp_path / "src" / "utils" / "helper.ts").write_text("", encoding="utf-8")

        result = list_directory_tool(str(tmp_path), max_depth=3)
        assert "src/" in result
        assert "main.ts" in result
        assert "utils/" in result
        assert "helper.ts" in result

    def test_ignores_node_modules(self, tmp_path: Path) -> None:
        """忽略 node_modules"""
        from agent_system.tools.list_directory import list_directory_tool

        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("", encoding="utf-8")
        (tmp_path / "src").mkdir()

        result = list_directory_tool(str(tmp_path))
        lines = result.splitlines()
        # 子目录行中不应出现 node_modules/
        child_lines = lines[1:]  # 跳过根目录行
        assert not any("node_modules/" in ln for ln in child_lines)
        assert "src/" in result

    def test_max_depth(self, tmp_path: Path) -> None:
        """深度限制"""
        from agent_system.tools.list_directory import list_directory_tool

        (tmp_path / "a" / "b" / "c" / "d").mkdir(parents=True)
        (tmp_path / "a" / "b" / "c" / "d" / "deep.ts").write_text("", encoding="utf-8")

        result = list_directory_tool(str(tmp_path), max_depth=2)
        assert "deep.ts" not in result

    def test_nonexistent(self) -> None:
        """目录不存在"""
        from agent_system.tools.list_directory import list_directory_tool

        result = list_directory_tool("/nonexistent/dir")
        assert "不存在" in result

    def test_dirs_only(self, tmp_path: Path) -> None:
        """只显示目录"""
        from agent_system.tools.list_directory import list_directory_tool

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text("", encoding="utf-8")

        result = list_directory_tool(str(tmp_path), include_files=False)
        assert "src/" in result
        assert "main.ts" not in result

    def test_gitignore_dir(self, tmp_path: Path) -> None:
        """通过 .gitignore 忽略目录"""
        from agent_system.tools.list_directory import list_directory_tool

        (tmp_path / ".gitignore").write_text("output\nlogs\n", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text("", encoding="utf-8")
        (tmp_path / "output").mkdir()
        (tmp_path / "output" / "bundle.js").write_text("", encoding="utf-8")
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "debug.log").write_text("", encoding="utf-8")

        result = list_directory_tool(str(tmp_path))
        assert "src/" in result
        assert "main.ts" in result
        assert "output" not in result
        assert "logs" not in result
        assert "bundle.js" not in result
        assert "debug.log" not in result

    def test_gitignore_wildcard(self, tmp_path: Path) -> None:
        """通过 .gitignore 通配符忽略文件"""
        from agent_system.tools.list_directory import list_directory_tool

        (tmp_path / ".gitignore").write_text("*.log\n*.tmp\n", encoding="utf-8")
        (tmp_path / "app.ts").write_text("", encoding="utf-8")
        (tmp_path / "debug.log").write_text("", encoding="utf-8")
        (tmp_path / "cache.tmp").write_text("", encoding="utf-8")

        result = list_directory_tool(str(tmp_path))
        assert "app.ts" in result
        assert "debug.log" not in result
        assert "cache.tmp" not in result

    def test_gitignore_comment_and_empty(self, tmp_path: Path) -> None:
        """.gitignore 中的注释和空行被跳过"""
        from agent_system.tools.list_directory import list_directory_tool

        (tmp_path / ".gitignore").write_text(
            "# this is a comment\n\ndist\n\n# another comment\n",
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "out.js").write_text("", encoding="utf-8")

        result = list_directory_tool(str(tmp_path))
        assert "src/" in result
        assert "dist" not in result

    def test_no_gitignore(self, tmp_path: Path) -> None:
        """没有 .gitignore 时正常工作"""
        from agent_system.tools.list_directory import list_directory_tool

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text("", encoding="utf-8")

        result = list_directory_tool(str(tmp_path))
        assert "src/" in result
        assert "main.ts" in result


# ── replace_in_file ────────────────────────────────────────────

class TestReplaceInFile:
    """replace_in_file_tool 测试"""

    def test_basic_replace(self, tmp_path: Path) -> None:
        """基本替换"""
        from agent_system.tools.replace_in_file import replace_in_file_tool

        f = tmp_path / "test.ts"
        f.write_text("const x = 1;\nconst y = 2;\n", encoding="utf-8")

        result = replace_in_file_tool(str(f), "const x = 1;", "const x = 42;")
        assert "成功" in result
        assert f.read_text(encoding="utf-8") == "const x = 42;\nconst y = 2;\n"

    def test_no_match(self, tmp_path: Path) -> None:
        """无匹配时报错"""
        from agent_system.tools.replace_in_file import replace_in_file_tool

        f = tmp_path / "test.ts"
        f.write_text("hello", encoding="utf-8")

        result = replace_in_file_tool(str(f), "world", "earth")
        assert "错误" in result
        assert "未找到" in result

    def test_multiple_matches(self, tmp_path: Path) -> None:
        """多处匹配时报错"""
        from agent_system.tools.replace_in_file import replace_in_file_tool

        f = tmp_path / "test.ts"
        f.write_text("aaa\naaa\n", encoding="utf-8")

        result = replace_in_file_tool(str(f), "aaa", "bbb")
        assert "错误" in result
        assert "2" in result

    def test_nonexistent_file(self) -> None:
        """文件不存在"""
        from agent_system.tools.replace_in_file import replace_in_file_tool

        result = replace_in_file_tool("/no/file.ts", "old", "new")
        assert "错误" in result


# ── project_structure ──────────────────────────────────────────

class TestProjectStructure:
    """get_project_structure_tool 测试"""

    def test_basic_structure(self, tmp_path: Path) -> None:
        """基本结构扫描"""
        from agent_system.tools.project_structure import get_project_structure_tool

        src = tmp_path / "src"
        src.mkdir()
        (src / "main.ts").write_text("export class Main {}", encoding="utf-8")
        (src / "utils.ts").write_text("export interface IUtil {}", encoding="utf-8")

        result = json.loads(get_project_structure_tool(str(tmp_path)))

        assert result["total_source_files"] >= 2
        assert len(result["exports"]) >= 2

    def test_nonexistent_dir(self) -> None:
        """目录不存在"""
        from agent_system.tools.project_structure import get_project_structure_tool

        result = json.loads(get_project_structure_tool("/nonexistent"))
        assert "error" in result

    def test_source_dirs_filter(self, tmp_path: Path) -> None:
        """限定子目录扫描"""
        from agent_system.tools.project_structure import get_project_structure_tool

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.ts").write_text("export class A {}", encoding="utf-8")
        (tmp_path / "test").mkdir()
        (tmp_path / "test" / "b.ts").write_text("export class B {}", encoding="utf-8")

        result = json.loads(get_project_structure_tool(
            str(tmp_path), source_dirs=["src"]
        ))
        # 只扫描 src
        has_src = any("src" in k for k in result["directories"])
        has_test = any("test" in k for k in result["directories"])
        assert has_src
        assert not has_test


# ── ts_check ───────────────────────────────────────────────────

class TestTsCheck:
    """ts_check_tool 测试"""

    def test_parse_errors(self) -> None:
        """解析 tsc 错误行格式"""
        from agent_system.tools.ts_check import _TSC_ERROR_RE

        line = "src/main.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'."
        m = _TSC_ERROR_RE.match(line)
        assert m is not None
        assert m.group(1) == "src/main.ts"
        assert m.group(2) == "10"
        assert m.group(4) == "TS2322"

    def test_result_structure(self) -> None:
        """TsCheckResult 结构"""
        from agent_system.tools.ts_check import TsCheckResult, TsError

        result = TsCheckResult(
            success=False,
            error_count=1,
            errors=[TsError(
                file="test.ts", line=1, column=1,
                code="TS2304", message="Cannot find name 'x'",
            )],
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["error_count"] == 1
        assert d["errors"][0]["code"] == "TS2304"


# ── diff_file ──────────────────────────────────────────────────

class TestDiffFile:
    """diff_file_tool 测试"""

    def test_diff_different_files(self, tmp_path: Path) -> None:
        """有差异时返回 diff"""
        from agent_system.tools.diff_file import diff_file_tool

        fa = tmp_path / "a.txt"
        fb = tmp_path / "b.txt"
        fa.write_text("line1\nline2\nline3\n", encoding="utf-8")
        fb.write_text("line1\nmodified\nline3\n", encoding="utf-8")

        result = diff_file_tool(str(fa), str(fb))
        assert "-line2" in result
        assert "+modified" in result

    def test_diff_identical_files(self, tmp_path: Path) -> None:
        """无差异"""
        from agent_system.tools.diff_file import diff_file_tool

        f = tmp_path / "same.txt"
        f.write_text("hello\n", encoding="utf-8")

        result = diff_file_tool(str(f), str(f))
        assert "无差异" in result

    def test_diff_nonexistent(self) -> None:
        """文件不存在"""
        from agent_system.tools.diff_file import diff_file_tool

        result = diff_file_tool("/no/a.txt", "/no/b.txt")
        assert "不存在" in result

    def test_diff_content(self) -> None:
        """内容对比"""
        from agent_system.tools.diff_file import diff_content_tool

        result = diff_content_tool("hello\nworld\n", "hello\nearth\n", label="test")
        assert "-world" in result
        assert "+earth" in result
