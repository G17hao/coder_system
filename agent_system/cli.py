"""命令行参数解析"""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import sys
from pathlib import Path

from agent_system import __version__
from agent_system.services.logging_formatter import ExecutorColorFormatter


def _resolve_ini_config_path(cli_path: str) -> Path | None:
    """解析 ini 配置文件路径。"""
    if cli_path.strip():
        return Path(cli_path).expanduser().resolve()

    env_path = os.environ.get("AGENT_SYSTEM_INI", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()

    default_path = Path.cwd() / "agent-system.ini"
    if default_path.exists():
        return default_path.resolve()

    return None


def _load_ini_overrides(config_path: Path | None) -> dict[str, object]:
    """从 ini 文件读取运行配置覆盖项。"""
    if config_path is None:
        return {}

    if not config_path.exists():
        raise FileNotFoundError(f"INI 配置文件不存在: {config_path}")

    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    overrides: dict[str, object] = {}

    def _get_str(section: str, option: str) -> str | None:
        if parser.has_option(section, option):
            return parser.get(section, option).strip()
        return None

    def _get_int(section: str, option: str) -> int | None:
        if parser.has_option(section, option):
            return parser.getint(section, option)
        return None

    def _get_float(section: str, option: str) -> float | None:
        if parser.has_option(section, option):
            return parser.getfloat(section, option)
        return None

    def _get_bool(section: str, option: str) -> bool | None:
        if parser.has_option(section, option):
            return parser.getboolean(section, option)
        return None

    mapping: list[tuple[str, str, str, str]] = [
        ("agent", "api_key", "anthropic_api_key", "str"),
        ("agent", "base_url", "anthropic_base_url", "str"),
        ("agent", "model", "model", "str"),
        ("agent", "project", "project_config_file", "str"),
        ("agent", "max_tokens", "max_tokens", "int"),
        ("agent", "temperature", "temperature", "float"),
        ("agent", "budget_limit", "budget_limit", "int"),
        ("agent", "call_limit", "call_limit", "int"),
        ("agent", "llm_timeout", "llm_timeout", "float"),
        ("agent", "llm_max_retries", "llm_max_retries", "int"),
        ("agent", "enable_llm_cache", "enable_llm_cache", "bool"),
        ("agent", "cache_min_tokens", "cache_min_tokens", "int"),
        ("summary", "trigger_bytes", "summary_trigger_bytes", "int"),
        ("summary", "keep_recent_messages", "summary_keep_recent_messages", "int"),
        ("summary", "keep_recent_log_entries", "summary_keep_recent_log_entries", "int"),
    ]

    getters = {
        "str": _get_str,
        "int": _get_int,
        "float": _get_float,
        "bool": _get_bool,
    }

    for section, option, target, value_type in mapping:
        value = getters[value_type](section, option)
        if value is not None and value != "":
            overrides[target] = value

    return overrides


def _build_min_project_template() -> dict[str, object]:
    """构建最小通用项目配置模板。"""
    return {
        "project_name": "my-project",
        "project_description": "项目目标描述",
        "project_root": "D:/path/to/target-project",
        "reference_roots": [
            "D:/path/to/reference-code",
        ],
        "git_branch": "feat/agent-auto",
        "coding_conventions": "在此填写项目编码规范",
        "conventions_file": "docs/project-conventions.md",
        "pattern_mappings": [
            {"from_pattern": "source pattern", "to_pattern": "target pattern"},
        ],
        "tool_generated_files": [
            {
                "pattern": "assets/resources/**/*.prefab",
                "generator": "cocos-creator / csdImporter",
                "reason": "这类资源应由编辑器或导入工具生成，不能手写维护",
            }
        ],
        "review_checklist": [
            "关键检查项1",
            "关键检查项2",
        ],
        "review_commands": [
            "在此填写构建/测试命令",
        ],
        "prompt_overrides": {
            "task_wizard": "项目特定任务拆分偏好（可选）",
            "planner": "项目特定规划约束（可选）",
            "analyst": "项目特定分析约束（可选）",
            "coder": "项目特定实现约束（可选）",
            "reviewer": "项目特定审查策略（可选）",
            "supervisor": "项目特定监督约束（可选）",
        },
        "email_approval_config_file": "./config/email_approval.local.json",
        "task_categories": ["infrastructure", "feature", "integration"],
        "initial_tasks": [
            {
                "id": "T0.1",
                "title": "初始化基础能力",
                "description": "实现第一个可验证的基础任务",
                "dependencies": [],
                "priority": 0,
                "phase": 0,
                "category": "infrastructure",
            }
        ],
    }


def _run_project_wizard() -> int:
    """运行项目配置向导"""
    from agent_system.project_wizard import run_project_wizard

    return run_project_wizard()


def _run_task_wizard(project_config_file: str = "") -> int:
    """运行任务列表对话助手"""
    from agent_system.task_wizard import run_task_wizard

    return run_task_wizard(project_config_file=project_config_file)


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        prog="agent-system",
        description="通用单线程 Agent 自动化编码系统",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--project",
        type=str,
        default="",
        help="项目配置文件路径 (project.json)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="首次运行：加载项目配置并开始执行",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从断点恢复执行",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干跑模式：不实际写文件，仅输出计划",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="",
        help="只执行指定任务 ID",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="查看任务队列状态",
    )
    parser.add_argument(
        "--project-template",
        action="store_true",
        help="输出最小 project.json 模板（含 prompt_overrides 示例）",
    )
    parser.add_argument(
        "--wizard",
        action="store_true",
        help="进入项目配置向导，生成 project.json",
    )
    parser.add_argument(
        "--task-wizard",
        action="store_true",
        help="进入任务列表向导；可配合 --project 加载项目特定约束",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default="",
        help="Anthropic API key（也可通过 ANTHROPIC_API_KEY 环境变量设置）",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="",
        help="Anthropic API base URL（也可通过 ANTHROPIC_BASE_URL 环境变量设置）",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-20250514",
        help="LLM 模型名称",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=0,
        help="Token 预算上限（0 表示不限制）",
    )
    parser.add_argument(
        "--call-limit",
        type=int,
        default=0,
        help="API 调用次数上限（0 表示不限制），按月订阅可设为每次运行的配额",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出详细日志",
    )
    parser.add_argument(
        "--ini-config",
        type=str,
        default="",
        help="运行时 ini 配置文件路径（也可通过 AGENT_SYSTEM_INI 或当前目录 agent-system.ini 自动加载）",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="恢复时将 failed 任务重置为 pending，允许重新执行",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口函数"""
    effective_argv = list(sys.argv[1:] if argv is None else argv)

    # 独立模式：无参数时进入项目配置向导
    if not effective_argv:
        return _run_project_wizard()

    parser = build_parser()
    args = parser.parse_args(effective_argv)

    # 配置日志
    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    use_color = bool(getattr(sys.stderr, "isatty", lambda: False)())
    handler = logging.StreamHandler()
    handler.setFormatter(ExecutorColorFormatter(log_format, use_color=use_color))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    root_logger.addHandler(handler)

    # 抑制第三方库的详细日志
    # httpx/httpcore 在 INFO/DEBUG 输出大量 HTTP 协议细节，anthropic 可能泄露提示词
    # 即使 --verbose 也不需要看这些底层网络日志
    for noisy_logger in ("httpx", "httpcore", "anthropic", "urllib3"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    if args.project_template:
        print(json.dumps(_build_min_project_template(), ensure_ascii=False, indent=2))
        return 0

    if args.wizard:
        return _run_project_wizard()

    if args.task_wizard:
        return _run_task_wizard(args.project)

    ini_path = _resolve_ini_config_path(args.ini_config)
    try:
        ini_overrides = _load_ini_overrides(ini_path)
    except Exception as e:
        print(f"INI 配置读取失败: {e}")
        return 1

    effective_project = args.project or str(ini_overrides.get("project_config_file", ""))

    if not effective_project:
        if args.status:
            print("请指定 --project 参数")
            return 1
        parser.print_help()
        return 0

    # 构建 AgentConfig
    from agent_system.models.context import AgentConfig
    from agent_system.orchestrator import Orchestrator
    from agent_system.services.git_service import GitError

    config_kwargs: dict[str, object] = dict(ini_overrides)
    config_kwargs.update({
        "anthropic_api_key": args.api_key or str(config_kwargs.get("anthropic_api_key", "")) or os.environ.get("ANTHROPIC_API_KEY", ""),
        "anthropic_base_url": args.base_url or str(config_kwargs.get("anthropic_base_url", "")) or os.environ.get("ANTHROPIC_BASE_URL", ""),
        "model": args.model or str(config_kwargs.get("model", "claude-sonnet-4-20250514")),
        "project_config_file": effective_project,
        "dry_run": args.dry_run,
        "budget_limit": args.budget if args.budget != 0 else int(config_kwargs.get("budget_limit", 0)),
        "call_limit": args.call_limit if args.call_limit != 0 else int(config_kwargs.get("call_limit", 0)),
    })
    config = AgentConfig(**config_kwargs)

    try:
        # --status: 查看状态
        if args.status:
            orch = Orchestrator(config=config)
            orch.initialize()
            orch.resume_tasks()
            print(orch.get_status_report())
            return 0

        # --init: 首次运行
        if args.init:
            orch = Orchestrator(config=config)
            orch.initialize()
            orch.init_tasks()

            if args.task:
                # 只执行指定任务
                target = next(
                    (t for t in orch.context.task_queue if t.id == args.task), None
                )
                if target is None:
                    print(f"任务 {args.task} 不存在")
                    return 1
                orch.run_single_task(target)
            else:
                orch.run()
            return 0

        # --resume: 断点恢复
        if args.resume:
            orch = Orchestrator.from_state(config)
            if args.retry_failed:
                orch.reset_failed_tasks()
            if args.task:
                target = next(
                    (t for t in orch.context.task_queue if t.id == args.task), None
                )
                if target is None:
                    print(f"任务 {args.task} 不存在")
                    return 1
                orch.run_single_task(target)
            else:
                orch.run()
            return 0

        # 默认: 同 --init
        orch = Orchestrator(config=config)
        orch.initialize()
        orch.init_tasks()
        orch.run()
        return 0
    except GitError as e:
        print(f"Git 启动校验失败，已停止执行: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
