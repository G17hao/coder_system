# Agent 自动化编码系统 — 架构设计

## 一、系统概述

通用的单线程 Agent 系统，使用 Anthropic Claude API，按照预定义的任务清单自动完成代码生成与修改工作。

系统本身**不包含**任何特定项目的任务数据。项目信息、初始任务清单、编码规范等通过外部**项目配置文件**（`project.json`）注入。

### 核心理念

- **任务驱动**：所有工作拆解为原子任务，存储在任务队列中
- **依赖感知**：每个任务声明前置依赖，执行时自动检测、自动生成缺失依赖
- **单线程顺序执行**：一次只执行一个任务，避免竞态和冲突
- **可中断恢复**：任务状态持久化到 JSON 文件，中断后可从断点继续
- **项目无关**：系统框架与具体项目解耦，通过配置文件适配不同项目

---

## 二、Agent 架构

```
                        ┌──────────────┐
                        │ project.json │  ← 外部项目配置
                        └──────┬───────┘
                               │ 加载
┌──────────────────────────────▼──────────────────────┐
│                   Orchestrator                       │
│          (主循环：领取→检查依赖→分派→回写)             │
└──────┬──────────┬──────────┬──────────┬──────────────┘
       │          │          │          │
  ┌────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼─────┐
  │Planner │ │Analyst │ │ Coder  │ │Reviewer │
  └────────┘ └────────┘ └────────┘ └─────────┘
```

### 2.1 Orchestrator（调度器）

**职责**：流程控制，不做具体工作。

- 加载项目配置文件（`project.json`），初始化上下文
- 从任务队列取优先级最高的 pending 任务
- 调用 Planner 检查前置依赖
- 依次调用 Analyst → Coder → Reviewer
- 根据结果更新任务状态、执行 git commit
- 处理重试和失败

**不使用 LLM**，纯 Python 逻辑。

### 2.2 Planner（规划 Agent）

**职责**：任务规划与依赖管理。

- 初始化时从项目配置加载任务清单（或由 LLM 根据项目描述生成）
- 运行时判断当前任务的前置依赖是否满足
- 当发现缺失依赖时动态生成新任务并插入队列
- 任务搁置与优先级调整

**System prompt 核心指令**：
- 根据项目配置中的 `projectDescription` 理解整体目标
- 输出格式为结构化 JSON（任务列表）

### 2.3 Analyst（分析 Agent）

**职责**：代码分析，为 Coder 提供精确规格。

- 读取参考代码（路径来自项目配置 `referenceRoots`），提取接口、数据结构、事件流
- 读取目标项目代码（路径来自项目配置 `projectRoot`），识别已有实现和缺口
- 输出结构化分析报告（接口定义、方法签名、事件列表、文件清单）

**System prompt 核心指令**：
- 只读不写，输出分析报告
- 关注接口契约而非实现细节
- 根据项目配置中的 `patternMappings` 识别跨语言模式映射

### 2.4 Coder（编码 Agent）

**职责**：代码生成与修改。

- 根据 Analyst 报告生成/修改代码文件
- 严格遵循项目配置中的 `codingConventions` 编码规范
- 输出精确的文件变更（创建或 diff）

**System prompt 核心指令**：
- 在 system prompt 中注入项目配置的 `codingConventions` 内容
- 输出完整文件内容（非 diff），供 Orchestrator 写入

### 2.5 Reviewer（审查 Agent）

**职责**：验证 Coder 产出物。

- 执行项目配置中声明的 `reviewCommands`（如编译检查）
- 根据 `reviewChecklist` 逐项检查代码质量
- 失败时生成修复建议

**System prompt 核心指令**：
- 根据 `reviewChecklist` 配置项逐条验证
- 输出 pass/fail + 具体问题列表

---

## 三、数据结构

### 3.1 项目配置（ProjectConfig）

```python
@dataclass
class PatternMapping:
    """跨语言/框架模式映射"""
    from_pattern: str   # 源模式描述，如 "View-Ctrl 分离"
    to_pattern: str     # 目标模式描述，如 "Component + Service"

@dataclass
class TaskSeed:
    """初始任务种子"""
    id: str
    title: str
    description: str
    dependencies: list[str]
    priority: int
    phase: int
    category: str

@dataclass
class ProjectConfig:
    """外部注入的项目配置，系统本身不包含任何项目特定信息"""
    project_name: str                     # 项目名称
    project_description: str              # 项目概述（注入 Planner prompt）
    project_root: str                     # 目标项目根目录（绝对路径）
    reference_roots: list[str]            # 参考代码根目录列表（可多个）
    git_branch: str                       # 自动创建的 git 分支名
    coding_conventions: str               # 编码规范文本（注入 Coder prompt）
    pattern_mappings: list[PatternMapping] # 跨语言/框架模式映射
    review_checklist: list[str]           # Reviewer 检查项列表
    review_commands: list[str]            # 编译/lint 命令列表
    task_categories: list[str]            # 任务分类枚举
    initial_tasks: list[TaskSeed]         # 初始任务种子列表
```

### 3.2 任务（Task）

```python
class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"

@dataclass
class ReviewResult:
    passed: bool
    issues: list[str]
    suggestions: list[str]

@dataclass
class Task:
    id: str                              # 唯一标识，如 "T0.1"
    title: str                           # 简短标题
    description: str                     # 详细描述
    status: TaskStatus
    dependencies: list[str]              # 前置任务 id 列表
    priority: int                        # 数字越小优先级越高
    phase: int                           # 所属阶段
    category: str                        # 来自项目配置的 task_categories
    created_by: Literal["initial", "planner"] = "initial"
    analysis_cache: str | None = None    # Analyst 输出缓存（避免重复分析）
    coder_output: str | None = None      # Coder 最近输出
    review_result: ReviewResult | None = None
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None             # 失败原因
    commit_hash: str | None = None       # 成功后的 git commit hash
```

### 3.3 Agent 上下文

```python
@dataclass
class AgentConfig:
    anthropic_api_key: str
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192
    temperature: float = 0.0
    project_config_file: str = ""       # project.json 路径
    state_file: str = "state/tasks.json" # 持久化文件路径
    git_auto_commit: bool = True
    dry_run: bool = False                # True 则不实际写文件
    max_dynamic_tasks: int = 10          # Planner 动态生成任务上限
    budget_limit: int = 500_000          # API 调用 token 预算上限

@dataclass
class AgentContext:
    project: ProjectConfig               # 通过配置文件注入
    task_queue: list[Task]
    completed_tasks: dict[str, Task]
    current_task: Task | None
    config: AgentConfig
```

---

## 四、Orchestrator 主循环

```
初始化:
  1. 读取 project.json → ProjectConfig
  2. 加载持久化状态（state/tasks.json）
     - 若状态文件不存在 → 从 ProjectConfig.initialTasks 生成初始 Task 列表
  3. 创建 git 分支（名称来自 ProjectConfig.gitBranch）

主循环:
  while (有 pending/blocked 任务) {
    1. 取优先级最高的 pending 任务 T
       - 若无 pending 但有 blocked → 调 Planner 检查是否可解锁
       - 若全部 done/failed → 退出

    2. 检查依赖
       Planner.checkDependencies(T)
       → 所有依赖 done → 继续
       → 某依赖在队列中未完成 → T 标记 blocked，取下一个
       → 某依赖不存在 → Planner.generateTask() 插入队列，T 标记 blocked

    3. T 标记 in-progress

    4. 分析阶段
       report = Analyst.analyze(T, ProjectConfig)
       T.analysisCache = report

    5. 编码阶段
       changes = Coder.generate(T, report, ProjectConfig)
       T.coderOutput = changes

    6. 写入文件
       将 changes 写入磁盘

    7. 审查阶段
       result = Reviewer.review(T, changes, ProjectConfig)
       - 执行 ProjectConfig.reviewCommands
       - 逐项检查 ProjectConfig.reviewChecklist

       if (result.passed) {
         git add + commit
         T.status = 'done'
         T.commitHash = hash
       } else {
         撤销文件变更 (git checkout)
         T.retryCount++
         if (T.retryCount >= T.maxRetries) {
           T.status = 'failed'
           T.error = result.issues.join('\n')
         } else {
           将 result.suggestions 追加到 T 上下文
           重新进入步骤 5（Coder 带修复建议重试）
         }
       }

    8. 持久化状态到 state/tasks.json
    9. 检查 token 预算，超出则暂停
  }

结束:
  输出执行报告（完成/失败任务统计、token 消耗）
```

---

## 五、目录结构

```
agent-system/
├── PLAN.md                  ← 本文件（通用架构设计）
├── pyproject.toml           ← 项目配置 + 依赖声明
├── requirements.txt         ← pip 依赖（兼容）
├── agent_system/            ← Python 包
│   ├── __init__.py
│   ├── __main__.py          ← 入口：python -m agent_system
│   ├── cli.py               ← 命令行参数解析 (argparse)
│   ├── orchestrator.py      ← 主循环调度
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base.py          ← Agent 抽象基类 (ABC)
│   │   ├── planner.py       ← Planner Agent
│   │   ├── analyst.py       ← Analyst Agent
│   │   ├── coder.py         ← Coder Agent
│   │   └── reviewer.py      ← Reviewer Agent
│   ├── models/
│   │   ├── __init__.py
│   │   ├── task.py           ← Task / TaskStatus / ReviewResult
│   │   ├── context.py        ← AgentContext / AgentConfig
│   │   └── project_config.py ← ProjectConfig 定义 + 加载/校验
│   ├── services/
│   │   ├── __init__.py
│   │   ├── llm.py            ← Anthropic API 封装（含 token 计数）
│   │   ├── file_service.py   ← 文件读写 (pathlib)
│   │   ├── git_service.py    ← Git 操作 (subprocess)
│   │   └── state_store.py    ← 状态持久化 (JSON)
│   ├── prompts/
│   │   ├── planner.md        ← Planner system prompt（通用模板）
│   │   ├── analyst.md        ← Analyst system prompt（通用模板）
│   │   ├── coder.md          ← Coder system prompt（运行时注入 coding_conventions）
│   │   └── reviewer.md       ← Reviewer system prompt（运行时注入 review_checklist）
│   └── tools/
│       ├── __init__.py
│       ├── read_file.py      ← 文件读取 tool
│       ├── write_file.py     ← 文件写入 tool
│       ├── search_file.py    ← 文件搜索 tool (glob/regex)
│       └── run_command.py    ← 命令执行 tool (subprocess)
├── projects/                 ← 项目配置目录（按项目隔离）
│   └── (project.json)       ← 具体项目配置（不属于系统本身）
├── tests/                    ← 测试目录
│   ├── fixtures/             ← 测试用 JSON/Lua 等固定数据
│   │   ├── valid_project.json
│   │   ├── invalid_project.json
│   │   ├── sample.lua
│   │   └── e2e_project.json
│   ├── test_step2.py
│   ├── test_step3.py
│   ├── test_step4.py
│   ├── test_step5.py
│   ├── test_step6.py
│   └── test_step7.py
├── state/
│   └── tasks.json            ← 运行状态持久化
└── logs/
    └── (运行日志)
```

---

## 六、技术选型

| 组件 | 选择 | 理由 |
|---|---|---|
| 运行时 | Python 3.11+ | 同步模型天然契合顺序执行架构，零编译 |
| LLM API | Anthropic Claude (Messages API) | 支持 tool_use、长上下文，Python SDK 首发特性 |
| 模型 | claude-sonnet-4-20250514（可配置） | 编码任务性价比最优 |
| 类型/数据建模 | dataclass + type hints | 轻量、标准库内置、IDE 补全友好 |
| 状态持久化 | JSON 文件 | 简单可靠，可人工检查/修改 |
| 文件操作 | pathlib | 跨平台、面向对象、标准库 |
| Git | subprocess 调用 git CLI | 无需额外依赖 |
| 依赖管理 | pip / pyproject.toml | 最小依赖：anthropic |
| 测试框架 | pytest | 简洁、社区标准、fixture 机制强大 |
| 可选增强 | pydantic | ProjectConfig schema 校验（可选替代 dataclass） |

---

## 七、实施步骤

> **原则**：每个 Step 完成后必须通过对应的验证测试，测试通过后立即 `git commit`，确保每次提交都是可工作的状态。

### Step 1: 项目骨架
- 创建 `pyproject.toml` + `requirements.txt`
- `pip install anthropic`
- 创建 `agent_system/` 包目录结构 + 全部 `__init__.py`
- 实现 `cli.py` + `__main__.py` 基础参数解析（仅 `--help`/`--version`）

**验证**：
```bash
python -m agent_system --help     # 正常输出帮助信息，退出码 0
python -m agent_system --version  # 输出版本号
```
✅ 测试通过 → `git commit -m "step1: project skeleton with CLI entry point"`

---

### Step 2: 数据模型 + 服务层
- 实现 `models/` 下 dataclass 定义（ProjectConfig / Task / AgentContext）
- 实现 `services/state_store.py`（JSON 状态读写 + 断点恢复）
- 实现 `services/llm.py`（Anthropic API 封装 + token 计数）
- 实现 `services/file_service.py` + `services/git_service.py`

**验证**（`tests/test_step2.py`）：
```python
# 1. Task 序列化/反序列化往返一致
task = Task(id="T0.1", title="test", ...)
json_str = task_to_json(task)
restored = task_from_json(json_str)
assert task == restored

# 2. StateStore 写入→读取→恢复完整任务队列
store = StateStore("state/test_tasks.json")
store.save([task1, task2])
loaded = store.load()
assert len(loaded) == 2

# 3. ProjectConfig 加载正常 JSON → 成功；缺少必填字段 → 抛 ValueError
config = ProjectConfig.from_file("tests/fixtures/valid_project.json")
assert config.project_name != ""
with pytest.raises(ValueError):
    ProjectConfig.from_file("tests/fixtures/invalid_project.json")

# 4. GitService.current_branch() 返回非空字符串
git = GitService(Path("."))
assert len(git.current_branch()) > 0
```
✅ 测试通过 → `git commit -m "step2: data models + service layer"`

---

### Step 3: Agent 基类 + Planner
- 定义 `agents/base.py` Agent 抽象基类（ABC，只暴露 `execute` 方法）
- 实现 Planner：加载 initial_tasks → 依赖检查 → 动态补充
- 编写 `prompts/planner.md` 通用模板

**验证**（`tests/test_step3.py`）：
```python
# 1. 依赖全部 done → 任务放行（返回 ready）
planner = Planner(llm=mock_llm)
task_a = Task(id="T1", dependencies=["T0"], ...)
completed = {"T0": done_task}
result = planner.check_dependencies(task_a, completed)
assert result == DependencyStatus.READY

# 2. 依赖未完成 → 任务 blocked
result = planner.check_dependencies(task_a, {})
assert result == DependencyStatus.BLOCKED

# 3. 依赖不存在 → 动态生成新任务
result = planner.check_dependencies(task_a, {}, known_ids=set())
assert result == DependencyStatus.MISSING
new_tasks = planner.generate_missing(["T0"], context)
assert len(new_tasks) > 0

# 4. 循环依赖检测 → 抛异常
task_x = Task(id="X", dependencies=["Y"])
task_y = Task(id="Y", dependencies=["X"])
with pytest.raises(CyclicDependencyError):
    planner.validate_no_cycles([task_x, task_y])
```
✅ 测试通过 → `git commit -m "step3: agent base class + planner with dependency resolution"`

---

### Step 4: Analyst Agent
- 实现 `tools/read_file.py` + `tools/search_file.py`
- 实现 Analyst Agent（从 reference_roots + project_root 读取分析）
- 编写 `prompts/analyst.md` 通用模板

**验证**（`tests/test_step4.py`）：
```python
# 1. read_file tool 读取指定文件/行范围 → 返回正确内容
result = read_file_tool("tests/fixtures/sample.lua", start=1, end=5)
assert "function" in result

# 2. search_file tool glob 匹配 → 返回文件路径列表
results = search_file_tool("tests/fixtures/", pattern="*.lua")
assert len(results) >= 1

# 3. Analyst 集成测试（使用 mock LLM）
#    给定一个小型参考文件 + 任务描述 → 输出结构化分析报告
analyst = Analyst(llm=mock_llm_with_canned_response)
report = analyst.execute(task, context)
assert "interfaces" in report or "methods" in report
```
✅ 测试通过 → `git commit -m "step4: analyst agent with file read/search tools"`

---

### Step 5: Coder Agent
- 实现 `tools/write_file.py`
- 实现 Coder Agent（运行时注入 coding_conventions）
- 编写 `prompts/coder.md` 通用模板

**验证**（`tests/test_step5.py`）：
```python
# 1. write_file tool 写入文件 → 文件存在且内容正确
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    write_file_tool(Path(tmp) / "test.ts", "const x = 1;")
    assert (Path(tmp) / "test.ts").read_text() == "const x = 1;"

# 2. write_file tool 自动创建中间目录
    write_file_tool(Path(tmp) / "a/b/c.ts", "export {}")
    assert (Path(tmp) / "a/b/c.ts").exists()

# 3. Coder prompt 模板注入验证
coder = Coder(llm=mock_llm)
prompt = coder.build_system_prompt(project_config)
assert "禁止 any 类型" in prompt  # coding_conventions 内容已注入

# 4. Coder 集成测试（mock LLM 返回预定义文件内容）
changes = coder.execute(task, analysis_report, context)
assert len(changes.files) > 0
assert all(f.path and f.content for f in changes.files)
```
✅ 测试通过 → `git commit -m "step5: coder agent with write tool and convention injection"`

---

### Step 6: Reviewer Agent
- 实现 `tools/run_command.py`（subprocess 执行 review_commands）
- 实现 Reviewer Agent（运行时注入 review_checklist）
- 编写 `prompts/reviewer.md` 通用模板

**验证**（`tests/test_step6.py`）：
```python
# 1. run_command tool 执行成功命令 → 返回 stdout + exit_code=0
result = run_command_tool("echo hello")
assert result.exit_code == 0
assert "hello" in result.stdout

# 2. run_command tool 执行失败命令 → 返回 stderr + exit_code!=0
result = run_command_tool("python -c \"raise Exception('fail')\"")
assert result.exit_code != 0

# 3. Reviewer prompt 注入 review_checklist
reviewer = Reviewer(llm=mock_llm)
prompt = reviewer.build_system_prompt(project_config)
assert "无 any 类型" in prompt  # checklist 内容已注入

# 4. Reviewer 集成测试
#    传入含 `any` 的代码 → mock LLM 返回 fail
review = reviewer.execute(task, code_changes, context)
assert review.passed is False
assert any("any" in issue for issue in review.issues)
```
✅ 测试通过 → `git commit -m "step6: reviewer agent with command runner and checklist"`

---

### Step 7: Orchestrator + CLI
- 实现 `orchestrator.py` 主循环（加载 project.json → 调度 → 持久化）
- 实现 `cli.py` + `__main__.py` 完整参数（`--init`/`--resume`/`--dry-run`/`--task`/`--status`）
- Git 自动提交逻辑
- 重试/失败处理 + token 预算控制

**验证**（`tests/test_step7.py`）：
```python
# 1. 主循环集成测试：3 个 mock 任务（T0→T1→T2 链式依赖），全部 mock Agent
#    → 按依赖顺序执行 → 全部 done
orch = Orchestrator(config, agents=mock_agents)
orch.run()
assert all(t.status == TaskStatus.DONE for t in orch.context.task_queue)

# 2. 重试测试：Reviewer 第一次 fail，第二次 pass → 任务最终 done
mock_reviewer.side_effect = [fail_result, pass_result]
orch.run_single_task(task)
assert task.status == TaskStatus.DONE
assert task.retry_count == 1

# 3. 失败上限测试：连续 3 次 fail → 任务 failed
mock_reviewer.side_effect = [fail_result] * 3
orch.run_single_task(task)
assert task.status == TaskStatus.FAILED

# 4. 断点恢复测试：运行 2 个任务后中断 → 重新加载 → 从第 3 个继续
orch.run_until(task_count=2)
orch2 = Orchestrator.from_state(config)
assert orch2.next_pending_task().id == "T2"

# 5. --dry-run 不写文件
result = subprocess.run(
    ["python", "-m", "agent_system", "--project", "tests/fixtures/project.json", "--dry-run"],
    capture_output=True
)
assert result.returncode == 0

# 6. --status 输出任务统计
result = subprocess.run(
    ["python", "-m", "agent_system", "--project", "tests/fixtures/project.json", "--status"],
    capture_output=True
)
assert "pending" in result.stdout.decode()
```
✅ 测试通过 → `git commit -m "step7: orchestrator main loop + CLI with retry/resume/dry-run"`

---

### Step 8: 端到端集成测试
- 创建一个**最小真实项目配置**（2-3 个简单任务）
- 使用真实 Anthropic API（非 mock）执行完整流程
- 调优 prompt 模板
- 调整 token 预算和重试策略

**验证**：
```bash
# 1. 端到端：用最小配置完整跑通
python -m agent_system --project tests/fixtures/e2e_project.json --init
# → 所有任务 done，git log 可见对应 commit

# 2. 中断恢复：Ctrl+C 中断后 --resume 继续
python -m agent_system --project tests/fixtures/e2e_project.json --resume
# → 从中断点继续，不重复已完成的任务

# 3. token 预算：设置极低预算 → 系统安全暂停并保存状态
python -m agent_system --project tests/fixtures/e2e_project.json --init
# （配置中 budget_limit=1000）→ 输出 "Budget exceeded, pausing..."
```
✅ 测试通过 → `git commit -m "step8: end-to-end integration verified"`

---

### 提交历史预览

```
step8: end-to-end integration verified
step7: orchestrator main loop + CLI with retry/resume/dry-run
step6: reviewer agent with command runner and checklist
step5: coder agent with write tool and convention injection
step4: analyst agent with file read/search tools
step3: agent base class + planner with dependency resolution
step2: data models + service layer
step1: project skeleton with CLI entry point
```

---

## 八、运行方式

```bash
# 首次运行（加载项目配置 + 开始执行）
python -m agent_system --project projects/my-project.json --init

# 继续执行（从断点恢复）
python -m agent_system --project projects/my-project.json --resume

# 干跑模式（不实际写文件，仅输出计划）
python -m agent_system --project projects/my-project.json --dry-run

# 只执行某个任务
python -m agent_system --project projects/my-project.json --task T0.1

# 查看状态
python -m agent_system --project projects/my-project.json --status
```

---

## 九、Prompt 模板注入机制

每个 Agent 的 system prompt 由**通用模板** + **项目配置注入**组成：

```
┌──────────────────────────────────────────┐
│          prompts/coder.md                │
│  ┌────────────────────────────────────┐  │
│  │  通用指令（文件输出格式、工具用法） │  │
│  └────────────────────────────────────┘  │
│  ┌────────────────────────────────────┐  │
│  │  {{codingConventions}}             │  │  ← 运行时从 project.json 注入
│  └────────────────────────────────────┘  │
│  ┌────────────────────────────────────┐  │
│  │  {{patternMappings}}              │  │  ← 运行时从 project.json 注入
│  └────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

模板变量使用 `{{variableName}}` 语法，Orchestrator 在调用 Agent 前替换。

---

## 十、风险与缓解

| 风险 | 缓解措施 |
|---|---|
| Coder 生成代码编译不过 | Reviewer 执行 reviewCommands + 最多 3 次重试 |
| 分析上下文超出 token 限制 | Analyst 分段读取文件，只提取接口级信息 |
| 生成的代码不符合规范 | Reviewer 根据 reviewChecklist 逐项检查 |
| 动态生成任务导致无限循环 | maxDynamicTasks 配置项限制上限 |
| API 费用失控 | token 计数 + budgetLimit 预算上限 |
| Git 冲突 | 每个任务独立 commit，支持回滚 |
| 项目配置遗漏 | ProjectConfig 加载时做 schema 校验，缺失必填项则报错退出 |
