# Agent System

通用单线程 Agent 自动化编码系统，基于 Anthropic Claude API（兼容可替换 Base URL）实现。系统通过 `project.json` 注入项目上下文，可用于不同技术栈的代码迁移、增量开发和自动化审查。

## 核心能力

- 任务驱动：原子任务队列 + 依赖感知
- 顺序执行：单线程主循环，避免并发冲突
- 可恢复：任务状态持久化，支持断点续跑
- 可审查：支持全局构建/测试命令 + 规则化审查
- 可泛化：项目特有 Prompt 通过 `prompt_overrides` 配置，不再硬编码在模板内

## 架构

执行链路：

`Planner -> Analyst -> Coder -> Reviewer -> Supervisor -> Reflector`

- `Planner`：依赖检查、缺失任务动态生成
- `Analyst`：读取目标/参考代码，输出结构化分析报告
- `Coder`：根据分析与规则生成修改
- `Reviewer`：执行审查命令并给出 `pass/fail`
- `Supervisor`：重试耗尽后介入，决定继续或暂停
- `Reflector`：记录任务执行反思，沉淀经验

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

# 仅查看状态
python -m agent_system --project projects/h5-widget-replication.json --status
```

Windows 可用：

```bat
run.bat init
run.bat resume
run.bat status
run.bat retry
```

## 生成最小项目模板

CLI 内置通用 `project.json` 模板输出能力：

```bash
python -m agent_system --project-template
```

可直接重定向保存：

```bash
python -m agent_system --project-template > projects/my-project.json
```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--project-template` | 输出最小 `project.json` 模板（含 `prompt_overrides` 示例） |
| `--project` | 项目配置文件路径 |
| `--init` | 首次运行 |
| `--resume` | 从断点恢复 |
| `--dry-run` | 干跑模式（不写文件） |
| `--task` | 只执行指定任务 ID |
| `--status` | 查看任务队列状态 |
| `--retry-failed` | 恢复时将 failed 重置为 pending |
| `--api-key` | Anthropic API Key |
| `--base-url` | API Base URL（支持兼容网关） |
| `--model` | 模型名称 |
| `--budget` | Token 预算上限（0 不限制） |
| `--call-limit` | 调用次数上限（0 不限制） |
| `--verbose` | 输出详细日志 |

## `project.json` 关键字段

最小必需字段：

- `project_name`
- `project_description`
- `project_root`
- `reference_roots`
- `git_branch`
- `coding_conventions`
- `review_checklist`
- `review_commands`
- `task_categories`
- `initial_tasks`

### `prompt_overrides`（推荐）

用于注入项目特有策略，避免修改通用模板：

```json
{
  "prompt_overrides": {
    "planner": "项目特定规划约束",
    "analyst": "项目特定分析约束",
    "coder": "项目特定实现约束",
    "reviewer": "项目特定审查策略",
    "supervisor": "项目特定监督约束"
  }
}
```

### `email_approval_config_file`（Supervisor 暂停邮件审批，可选）

当任务被 `Supervisor` 判定为 `halt`（进入 `BLOCKED`）时，可通过邮件通知人工决策：

- 回复 `CONTINUE: <提示词>`：继续执行并将提示词注入下一轮修复
- 回复 `STOP`：保持暂停

建议将邮件配置放到独立文件（避免把邮箱配置与主项目配置混在一起）：

```json
{
  "email_approval_config_file": "./config/email_approval.local.json"
}
```

`email_approval.local.json` 示例：

```json
{
  "enabled": true,
  "smtp_host": "smtp.example.com",
  "smtp_port": 465,
  "smtp_user": "bot@example.com",
  "smtp_password_env": "AGENT_SMTP_PASSWORD",
  "imap_host": "imap.example.com",
  "imap_port": 993,
  "imap_user": "bot@example.com",
  "imap_password_env": "AGENT_IMAP_PASSWORD",
  "notify_to": "owner@example.com",
  "notify_from": "bot@example.com",
  "approval_sender": "owner@example.com",
  "subject_prefix": "[AgentSystem]",
  "poll_interval_sec": 15,
  "max_wait_sec": 1800
}
```

## 常见工作流

```bash
# 1) 初始化运行
python -m agent_system --project projects/my-project.json --init --verbose

# 2) 失败后恢复并允许 failed 重试
python -m agent_system --project projects/my-project.json --resume --retry-failed

# 3) 只跑单个任务
python -m agent_system --project projects/my-project.json --resume --task T0.1
```

## 目录结构

```
agent-system/
├── agent_system/
│   ├── agents/
│   ├── models/
│   ├── services/
│   ├── prompts/
│   └── tools/
├── projects/
├── tests/
└── run.bat
```

## 版本信息

- 当前版本：`0.1.0`
- Python：`3.11+`
- 主要依赖：`anthropic`
