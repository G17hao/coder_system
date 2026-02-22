# Agent System

通用单线程 Agent 自动化编码系统，基于 Anthropic Claude API 实现。

## 特性

- **任务驱动**：原子任务队列，支持依赖管理
- **单线程执行**：避免竞态和冲突
- **可中断恢复**：状态持久化到 JSON，断点续传
- **项目无关**：通过配置文件适配不同项目
- **多 Agent 协作**：Planner → Analyst → Coder → Reviewer → Supervisor → Reflector

## 安装

```bash
pip install -r requirements.txt
```

## 快速开始

```bash
# 首次运行
python -m agent_system --project projects/h5-widget-replication.json --init

# 从断点恢复
python -m agent_system --project projects/h5-widget-replication.json --resume

# 查看状态
python -m agent_system --project projects/h5-widget-replication.json --status
```

或使用 `run.bat`（Windows）：

```bat
run.bat init
run.bat resume
run.bat status
run.bat retry
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--project` | 项目配置文件路径 |
| `--init` | 首次运行 |
| `--resume` | 从断点恢复 |
| `--dry-run` | 干跑模式（不写文件） |
| `--task` | 只执行指定任务 ID |
| `--status` | 查看任务队列状态 |
| `--api-key` | Anthropic API key |
| `--base-url` | API base URL |
| `--model` | LLM 模型名称 |
| `--budget` | Token 预算上限 |
| `--call-limit` | API 调用次数上限 |
| `--verbose` | 详细日志 |
| `--retry-failed` | 恢复时重置 failed 任务 |

## 项目配置

创建 `project.json` 配置文件：

```json
{
  "project_name": "my-project",
  "project_description": "项目描述",
  "project_root": "E:\\path\\to\\project",
  "reference_roots": ["E:\\path\\to\\reference"],
  "git_branch": "feat/agent",
  "coding_conventions": "编码规范文本",
  "pattern_mappings": [
    {"from_pattern": "A", "to_pattern": "B"}
  ],
  "review_checklist": ["检查项1", "检查项2"],
  "review_commands": ["npx tsc --noEmit"],
  "task_categories": ["category1", "category2"],
  "initial_tasks": [
    {
      "id": "T0.1",
      "title": "任务标题",
      "description": "任务描述",
      "dependencies": [],
      "priority": 0,
      "phase": 0,
      "category": "category1"
    }
  ]
}
```

## 目录结构

```
agent-system/
├── agent_system/       # 核心包
│   ├── agents/         # Agent 实现
│   ├── models/         # 数据模型
│   ├── services/      # 服务层
│   ├── prompts/       # Prompt 模板
│   └── tools/         # 工具函数
├── projects/           # 项目配置
├── tests/             # 单元测试
└── run.bat            # 快速运行脚本
```

## 版本

- **当前版本**：0.1.0
- **Python**：3.11+
- **依赖**：anthropic
