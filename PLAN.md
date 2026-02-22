# Agent 自动化编码系统 — 项目文档

## 一、系统概述

Agent System 是一个通用的**单线程 Agent 自动化编码系统**，基于 Anthropic Claude API（兼容阿里云 DashScope 等兼容接口）实现，按照预定义的任务清单自动完成代码生成与修改工作。

系统本身**不包含**任何特定项目的任务数据，通过外部项目配置文件（`project.json`）注入项目信息、初始任务清单、编码规范等。

### 核心理念

- **任务驱动**：所有工作拆解为原子任务，存储在任务队列中
- **依赖感知**：每个任务声明前置依赖，执行时自动检测、自动生成缺失依赖
- **单线程顺序执行**：一次只执行一个任务，避免竞态和冲突
- **可中断恢复**：任务状态持久化到 JSON 文件，中断后可从断点继续
- **项目无关**：系统框架与具体项目解耦，通过配置文件适配不同项目

---

## 二、系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI 入口                                 │
│              (python -m agent_system)                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                   Orchestrator                                  │
│          (主循环：领取→检查依赖→分派→回写)                        │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────────┘
       │          │          │          │          │
  ┌────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼────┐ ┌───▼─────┐
  │Planner │ │Analyst │ │ Coder  │ │Reviewer│ │Supervisor│
  │        │ │        │ │        │ │        │ │         │
  └────────┘ └────────┘ └────────┘ └────────┘ └──────────┘
                              │
                    ┌─────────┴─────────┐
                    │   Reflector       │
                    │   (反思记录)       │
                    └───────────────────┘
```

### 2.1 Orchestrator（调度器）

**职责**：流程控制，不做具体工作。

- 加载项目配置文件（`project.json`），初始化上下文
- 从任务队列取优先级最高的 pending 任务
- 调用 Planner 检查前置依赖
- 依次调用 Analyst → Coder → Reviewer → Supervisor
- 根据结果更新任务状态、执行 git commit
- 处理重试和失败、预算控制

**不使用 LLM**，纯 Python 逻辑。

### 2.2 Planner（规划 Agent）

**职责**：任务规划与依赖管理。

- 初始化时从项目配置加载任务清单
- 运行时判断当前任务的前置依赖是否满足
- 当发现缺失依赖时动态生成新任务并插入队列
- 任务搁置与优先级调整
- 循环依赖检测

### 2.3 Analyst（分析 Agent）

**职责**：代码分析，为 Coder 提供精确规格。

- 读取参考代码（路径来自项目配置 `reference_roots`），提取接口、数据结构、事件流
- 读取目标项目代码（路径来自项目配置 `project_root`），识别已有实现和缺口
- 输出结构化分析报告（接口定义、方法签名、事件列表、文件清单）
- 支持从分析阶段拆解子任务

### 2.4 Coder（编码 Agent）

**职责**：代码生成与修改。

- 根据 Analyst 报告生成/修改代码文件
- 严格遵循项目配置中的 `coding_conventions` 编码规范
- 输出精确的文件变更（创建或修改）
- 支持 Supervisor 介入修复

### 2.5 Reviewer（审查 Agent）

**职责**：验证 Coder 产出物。

- 执行项目配置中声明的 `review_commands`（如编译检查）
- 根据 `review_checklist` 逐项检查代码质量
- 失败时生成修复建议，支持重试

### 2.6 Supervisor（监督 Agent）

**职责**：高级修复介入。

- 当 Coder/Reviewer 多次失败时介入
- 分析失败原因，提供修复提示词
- 维护必改文件清单

### 2.7 Reflector（反思 Agent）

**职责**：记录执行反思。

- 任务完成后记录反思日志
- 保存到 `reflections/` 目录

---

## 三、数据结构

### 3.1 项目配置（ProjectConfig）

| 字段 | 类型 | 说明 |
|------|------|------|
| `project_name` | str | 项目名称 |
| `project_description` | str | 项目概述 |
| `project_root` | str | 目标项目根目录（绝对路径） |
| `reference_roots` | list[str] | 参考代码根目录列表 |
| `git_branch` | str | 自动创建的 git 分支名 |
| `coding_conventions` | str | 编码规范文本 |
| `pattern_mappings` | list[dict] | 跨语言/框架模式映射 |
| `review_checklist` | list[str] | Reviewer 检查项列表 |
| `review_commands` | list[str] | 编译/lint 命令列表 |
| `task_categories` | list[str] | 任务分类枚举 |
| `initial_tasks` | list[dict] | 初始任务种子列表 |

### 3.2 任务（Task）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | str | 唯一标识，如 "T0.1" |
| `title` | str | 简短标题 |
| `description` | str | 详细描述 |
| `status` | TaskStatus | 任务状态 |
| `dependencies` | list[str] | 前置任务 id 列表 |
| `priority` | int | 数字越小优先级越高 |
| `phase` | int | 所属阶段 |
| `category` | str | 任务分类 |
| `analysis_cache` | str | Analyst 输出缓存 |
| `coder_output` | str | Coder 最近输出 |
| `review_result` | ReviewResult | 审查结果 |
| `retry_count` | int | 重试次数 |
| `max_retries` | int | 最大重试次数（默认 20） |
| `error` | str | 失败原因 |
| `commit_hash` | str | 成功后的 git commit hash |
| `supervisor_hint` | str | Supervisor 提示词 |
| `supervisor_plan` | str | Supervisor 修复计划 |
| `supervisor_must_change_files` | list[str] | 必改文件清单 |
| `modified_files` | list[str] | 累计修改的文件列表 |

### 3.3 任务状态（TaskStatus）

```
PENDING      → 等待执行
IN_PROGRESS → 正在执行
BLOCKED     → 被依赖阻塞
DONE        → 已完成
FAILED      → 失败
SKIPPED     → 跳过
```

---

## 四、运行方式

```bash
# 首次运行（加载项目配置 + 开始执行）
python -m agent_system --project projects/my-project.json --init

# 继续执行（从断点恢复）
python -m agent_system --project projects/my-project.json --resume

# 干跑模式（不实际写文件，仅输出计划）
python -m agent_system --project projects/my-project.json --dry-run

# 只执行某个任务
python -m agent_system --project projects/my-project.json --init --task T0.1

# 查看状态
python -m agent_system --project projects/my-project.json --status

# 恢复时重置 failed 任务
python -m agent_system --project projects/my-project.json --resume --retry-failed
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--project` | 项目配置文件路径 |
| `--init` | 首次运行 |
| `--resume` | 从断点恢复 |
| `--dry-run` | 干跑模式 |
| `--task` | 只执行指定任务 ID |
| `--status` | 查看任务队列状态 |
| `--api-key` | Anthropic API key |
| `--base-url` | Anthropic API base URL |
| `--model` | LLM 模型名称 |
| `--budget` | Token 预算上限（0=不限制） |
| `--call-limit` | API 调用次数上限（0=不限制） |
| `--verbose` | 输出详细日志 |
| `--retry-failed` | 恢复时将 failed 任务重置为 pending |

---

## 五、目录结构

```
agent-system/
├── PLAN.md                  ← 项目文档
├── PROJECT.md               ← 项目特定配置说明
├── pyproject.toml           ← 项目配置 + 依赖声明
├── requirements.txt         ← pip 依赖
├── run.bat                  ← Windows 快速运行脚本
│
├── agent_system/            ← Python 包
│   ├── __init__.py
│   ├── __main__.py          ← 入口：python -m agent_system
│   ├── cli.py               ← 命令行参数解析
│   ├── orchestrator.py      ← 主循环调度
│   ├── task_wizard.py       ← 无参数启动的任务列表对话系统
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py          ← Agent 抽象基类
│   │   ├── planner.py       ← Planner Agent
│   │   ├── analyst.py       ← Analyst Agent
│   │   ├── coder.py         ← Coder Agent
│   │   ├── reviewer.py      ← Reviewer Agent
│   │   ├── supervisor.py    ← Supervisor Agent
│   │   └── reflector.py     ← Reflector Agent
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── task.py          ← Task / TaskStatus / ReviewResult
│   │   ├── context.py       ← AgentContext / AgentConfig
│   │   └── project_config.py ← ProjectConfig 定义 + 加载
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── llm.py           ← Anthropic API 封装（含 token 计数）
│   │   ├── file_service.py  ← 文件读写
│   │   ├── git_service.py   ← Git 操作
│   │   ├── state_store.py   ← 状态持久化（JSON）
│   │   ├── conversation_logger.py ← 对话日志
│   │   ├── logging_formatter.py   ← 日志格式化
│   │   └── path_guard.py    ← 路径守卫
│   │
│   ├── prompts/
│   │   ├── planner.md       ← Planner system prompt
│   │   ├── analyst.md       ← Analyst system prompt
│   │   ├── coder.md         ← Coder system prompt
│   │   ├── reviewer.md      ← Reviewer system prompt
│   │   ├── supervisor.md    ← Supervisor system prompt
│   │   └── reflector.md     ← Reflector system prompt
│   │
│   └── tools/
│       ├── __init__.py
│       ├── read_file.py     ← 文件读取 tool
│       ├── write_file.py    ← 文件写入 tool
│       ├── search_file.py   ← 文件搜索 tool (glob)
│       └── run_command.py   ← 命令执行 tool
│
├── projects/                 ← 项目配置目录
│   └── h5-widget-replication.json ← 示例：H5 Widget 迁移配置
│
├── tests/                    ← 测试目录
│   ├── fixtures/            ← 测试用固定数据
│   ├── test_step2.py       ← 数据模型测试
│   ├── test_step3.py       ← Planner 测试
│   ├── test_step4.py       ← Analyst 测试
│   ├── test_step5.py       ← Coder 测试
│   ├── test_step6.py       ← Reviewer 测试
│   ├── test_step7.py       ← Orchestrator 测试
│   ├── test_step8_tools.py ← Tools 测试
│   ├── test_step9_reflector.py ← Reflector 测试
│   ├── test_llm_retry.py   ← LLM 重试测试
│   ├── test_pause_on_failure.py ← 失败暂停测试
│   └── test_supervisor.py  ← Supervisor 测试
│
├── state/                    ← 运行状态（可选）
└── agent-system/             ← 每个项目的运行时目录
    ├── state/               ← 任务状态持久化
    ├── reflections/         ← 反思日志
    └── conversations/       ← 对话日志
```

---

## 六、技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 运行时 | Python 3.11+ | 同步模型天然契合顺序执行架构 |
| LLM API | Anthropic Claude (Messages API) | 支持 tool_use、长上下文 |
| 模型 | claude-sonnet-4-20250514（可配置） | 编码任务性价比优 |
| 兼容 API | 阿里云 DashScope | 可替换的 API 端点 |
| 类型/数据建模 | dataclass + type hints | 轻量、标准库内置 |
| 状态持久化 | JSON 文件 | 简单可靠，可人工检查 |
| 文件操作 | pathlib | 跨平台、面向对象 |
| Git | subprocess 调用 git CLI | 无需额外依赖 |
| 依赖管理 | pip / pyproject.toml | 最小依赖：anthropic |
| 测试框架 | pytest | 简洁、社区标准 |

---

## 七、Prompt 模板注入机制

每个 Agent 的 system prompt 由**通用模板** + **项目配置注入**组成：

```
┌────────────────────────────────────────────┐
│          prompts/coder.md                  │
│  ┌──────────────────────────────────────┐  │
│  │  通用指令（文件输出格式、工具用法）   │  │
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │  {{codingConventions}}               │  │  ← 运行时注入
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │  {{patternMappings}}                │  │  ← 运行时注入
│  └──────────────────────────────────────┘  │
└────────────────────────────────────────────┘
```

模板变量使用 `{{variableName}}` 语法，Orchestrator 在调用 Agent 前替换。

---

## 八、示例项目配置

项目 `h5-widget-replication.json` 演示了如何配置一个真实项目：

- **目标**：将 Lua 客户端（CSClient）的 Widget/UI 系统移植到 H5 客户端（H5Client）
- **技术栈**：Cocos Creator 3.8.7 + TypeScript
- **参考代码**：CSClient 的 Lua 源码和 UI 资源
- **任务分类**：infrastructure, model, widget-enhance, widget-new, ui-panel, integration

---

## 九、运行示例

```bash
# Windows 快速运行
.\run.bat init
.\run.bat resume
.\run.bat status
.\run.bat retry

# 或手动运行
python -m agent_system --project projects/h5-widget-replication.json --init --verbose
```

---

## 十、版本信息

- **当前版本**：0.1.0
- **Python 版本**：3.11+
- **依赖**：anthropic

