"""命令行参数解析"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from agent_system import __version__


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
        "--api-key",
        type=str,
        default="",
        help="Anthropic API key（也可通过 ANTHROPIC_API_KEY 环境变量设置）",
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
        default=500_000,
        help="Token 预算上限",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出详细日志",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口函数"""
    parser = build_parser()
    args = parser.parse_args(argv)

    # 配置日志
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.project:
        if args.status:
            print("请指定 --project 参数")
            return 1
        parser.print_help()
        return 0

    # 构建 AgentConfig
    from agent_system.models.context import AgentConfig
    from agent_system.orchestrator import Orchestrator

    config = AgentConfig(
        anthropic_api_key=args.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        model=args.model,
        project_config_file=args.project,
        dry_run=args.dry_run,
        budget_limit=args.budget,
    )

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


if __name__ == "__main__":
    sys.exit(main())
