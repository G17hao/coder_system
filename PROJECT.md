# H5Client Widget 复刻 — 项目配置

> 本文件是 Agent 系统的**项目特定配置**，对应 `ProjectConfig` 接口。
> 运行时由 Orchestrator 读取，注入到各 Agent 的 prompt 上下文中。
> 正式运行时应转为 `project.json` 格式。

---

## 项目基本信息

| 字段 | 值 |
|---|---|
| projectName | mir3-h5-widget-replication |
| projectRoot | `E:\work\mir3-zircon\H5Client` |
| gitBranch | feat/agent-widget-replication |

### 参考代码根目录（referenceRoots）

| 路径 | 说明 |
|---|---|
| `E:\work\CSClient\src\` | Lua 客户端主代码（Widget/Model/Panel 参考源） |
| `E:\work\CSClient\CocosStudioResource\cocosstudio\` | CSD UI 布局文件 |

---

## 项目描述（projectDescription）

将 Lua 客户端（CSClient）的 Widget/UI 系统完整移植到 H5 客户端（H5Client）。

H5Client 使用 Cocos Creator 3.8.7 + TypeScript，已有基础地图渲染、网络连接、音效系统。
Widget 层已搭建 9 个骨架组件（BottomWidget/SkillWidget/RockerWidget 等），但大部分功能为空壳——
核心问题是**网络包没有桥接到各 Model**，导致 UI 无数据驱动。

Lua 端同名 Widget 功能完整，包含聊天、技能、小地图、BUFF、队伍、快捷栏等。
此外 Lua 端还有 30+ UI 弹出面板（背包/角色/商城/公会等），H5 端尚未实现。

---

## 编码规范（codingConventions）

以下规范注入 Coder Agent 的 system prompt：

### SOLID 原则

- **SRP**：每个类/模块只负责一项职责，超过 300 行考虑拆分
- **OCP**：使用策略模式/模板方法替代 switch-case/if-else 分支
- **LSP**：子类必须能替换父类使用
- **ISP**：定义小而专一的接口，不强迫调用者依赖不需要的方法
- **DIP**：高层模块依赖抽象不依赖具体实现，禁止在业务类中直接 `new` 具体依赖

### TypeScript 规范

- **禁止** `any` 类型，必须定义明确的类型或接口
- 使用 `interface` 定义服务契约和数据结构
- 公共 API 必须有 JSDoc 注释
- 优先使用组合而非继承
- 事件名使用强类型枚举，不使用魔术字符串

### Cocos Creator 规范

- Component 类保持轻量，业务逻辑委托给独立的服务类
- 避免在 `update()` 中执行重逻辑，使用状态机模式管理更新
- 使用 `@property` 暴露的引用优于运行时 `getComponent` / `find` 查找

---

## 跨语言模式映射（patternMappings）

| 源模式（Lua） | 目标模式（H5 TypeScript） |
|---|---|
| `View` + `Ctrl` 分离 | `Component` (轻量 View) + `Service` (业务逻辑) |
| `self.n_xxx` (CSD 节点引用) | `@property` 装饰器绑定 |
| `self:xxx()` 方法 | `public xxx(): void` 方法 |
| `EventCenter:on("xxx", ...)` | `GlobalEventBus.on(EventEnum.XXX, ...)` |
| `PlayerData.xxx` 全局访问 | `PlayerModel.instance.xxx` 或接口注入 |
| `require("xxx")` | `import { xxx } from '...'` |
| `cc.exports.xxx = ...` | `export class xxx` |
| `self:scheduleOnce(fn, t)` | `this.scheduleOnce(fn, t)` 或 `setTimeout` |
| `display.newLayer()` | `new Node()` + `addComponent(UITransform)` |
| `cc.p(x, y)` | `new Vec2(x, y)` 或 `v2(x, y)` |

---

## 审查检查项（reviewChecklist）

1. TypeScript 编译零错误（`npx tsc --noEmit`）
2. 无 `any` 类型使用
3. 无直接 `new` 具体依赖（应通过构造函数注入或服务定位器）
4. 无 `XxxClass.instance` 硬引用（如必须使用单例，应通过接口访问）
5. Component 类不超过 300 行
6. 公共方法有 JSDoc 注释
7. 事件名使用枚举常量，非魔术字符串
8. 新增 .ts 文件有对应 .ts.meta 文件
9. import 路径正确（相对路径，无循环依赖）
10. 无 `console.log` 遗留（应使用项目日志工具）

### 审查命令（reviewCommands）

```json
["npx tsc --noEmit --project tsconfig.json"]
```

---

## 任务分类（taskCategories）

```json
["infrastructure", "model", "widget-enhance", "widget-new", "ui-panel", "integration"]
```

---

## 初始任务清单（initialTasks）

### 阶段 0：基础设施（阻塞所有后续）

| ID | 标题 | 描述 | 依赖 | 优先级 | 分类 |
|---|---|---|---|---|---|
| T0.1 | 网络包→Model 桥接层 | 创建 NetModelBridge 服务，将 MapModel 已监听的网络包（StatsUpdate, HealthChanged 等）转发到 PlayerModel/SkillModel/ItemModel 等，并 emit GlobalEventBus 事件 | — | 0 | infrastructure |
| T0.2 | 补全 ModelManager 注册 | 将 QuestModel/TeamModel/ActivityModel 注册到 ModelManager，确保初始化和销毁生命周期 | — | 0 | infrastructure |
| T0.3 | UIPanel 懒加载框架 | UIManager 新增 openPanel()/closePanel() 方法，支持按需加载 Prefab 面板，管理面板栈和互斥逻辑 | — | 0 | infrastructure |

### 阶段 1：核心 Widget 功能补全

| ID | 标题 | 描述 | 依赖 | 优先级 | 分类 |
|---|---|---|---|---|---|
| T1.1 | BottomWidget - 聊天输入框/频道切换 | 添加 EditBox 发送输入框、频道选择 Tab（世界/公会/私聊）、发送调用 ChatModel | T0.1 | 10 | widget-enhance |
| T1.2 | BottomWidget - NPC 对话按钮 | 靠近 NPC 时显示对话按钮，点击触发 NPCTalk 事件 | T0.1 | 11 | widget-enhance |
| T1.3 | BottomWidget - 拾取按钮 | 附近有掉落物时显示，点击发送拾取请求 | T0.1 | 11 | widget-enhance |
| T1.4 | BottomWidget - 快捷物品使用/拖拽 | 快捷栏支持拖拽配置、点击使用物品、使用后 CD 显示 | T0.1 | 12 | widget-enhance |
| T1.5 | SkillWidget - 网络数据驱动 | SkillModel 监听 NewMagic/RemoveMagic/MagicDelay 包，技能列表和 CD 从服务器同步 | T0.1 | 10 | widget-enhance |
| T1.6 | SkillWidget - 扩展功能按钮栏 | 技能栏底部添加背包/公会/邮件/商店/设置入口按钮 | T0.3 | 13 | widget-enhance |
| T1.7 | RightTopWidget - 小地图实时绘制 | 用 Graphics 绘制怪物(红)/NPC(蓝)/其他玩家(白)/自己(绿)圆点 | T0.1 | 10 | widget-enhance |
| T1.8 | RightTopWidget - 坐标/地图名/时间 | 显示当前格坐标、地图名称、服务器时间 | T0.1 | 11 | widget-enhance |
| T1.9 | RightTopWidget - 动态按钮逻辑 | ButtonService 根据等级/任务/充值条件动态显示/隐藏功能按钮 | T0.1 | 14 | widget-enhance |
| T1.10 | RoleInfoWidget - BUFF 折叠/描述 | BUFF 图标支持展开/收起列表，点击显示详细描述 | T0.1 | 13 | widget-enhance |
| T1.11 | RoleInfoWidget - 队伍成员交互 | 点击队员 → 跟随/传送菜单 | T0.1 | 14 | widget-enhance |

### 阶段 2：缺失 Widget 新增

| ID | 标题 | 描述 | 依赖 | 优先级 | 分类 |
|---|---|---|---|---|---|
| T2.1 | TopLayer 容器框架 | 创建高层面板节点，管理子组件生命周期（Tips/通知/跑马灯等的容器） | — | 15 | widget-new |
| T2.2 | 物品 Tips 弹窗 | 点击装备/物品显示详细属性，支持装备对比（需 ItemModel 字段齐全） | T2.1, T0.1 | 16 | widget-new |
| T2.3 | Paomadeng（跑马灯） | 横幅滚动系统公告，监听服务端广播包 | T2.1, T0.1 | 17 | widget-new |
| T2.4 | Notify（通知弹窗） | 组队邀请/好友申请 + 倒计时，接受/拒绝操作 | T2.1, T0.1 | 17 | widget-new |
| T2.5 | AutoEquip（自动装备） | 获得新装备后提示穿戴/学习，倒计时自动操作 | T2.1, T0.1 | 18 | widget-new |
| T2.6 | BossNotifier | BOSS 复活/活动时间提醒浮窗 | T2.1, T0.1 | 19 | widget-new |
| T2.7 | 红点系统 (RedPointHelper) | 统一管理功能按钮红点状态，条件变更时自动刷新 | T0.1 | 16 | widget-new |
| T2.8 | BottomWidget - 成长引导列表 | 根据等级/任务进度显示新手引导项 | T0.1 | 18 | widget-enhance |

### 阶段 3：UI 弹出面板

| ID | 标题 | 描述 | 依赖 | 优先级 | 分类 |
|---|---|---|---|---|---|
| T3.1 | 背包面板 (Bag) | 物品格子、装备/消耗品Tab、使用/丢弃 | T0.3, T0.1, T2.2 | 20 | ui-panel |
| T3.2 | 聊天完整面板 (Chat) | 多频道、历史记录、表情、@提及 | T0.3, T0.1 | 21 | ui-panel |
| T3.3 | 技能学习面板 (Skill) | 技能树、学习/升级、快捷栏配置 | T0.3, T0.1 | 21 | ui-panel |
| T3.4 | 设置面板 (Setting) | 画质/音效/操作设置 | T0.3 | 22 | ui-panel |
| T3.5 | 邮件面板 (Mail) | 邮件列表、附件领取、删除 | T0.3, T0.1 | 22 | ui-panel |
| T3.6 | 好友面板 (Friends) | 好友列表、添加/删除、私聊入口 | T0.3, T0.1 | 23 | ui-panel |
| T3.7 | 组队面板 (Team) | 创建队伍、邀请、踢人、解散 | T0.3, T0.1 | 23 | ui-panel |
| T3.8 | 行会面板 (Guild) | 创建/加入行会、成员管理、行会仓库 | T0.3, T0.1 | 24 | ui-panel |
| T3.9 | 商城面板 (Store) | 商品列表、购买、充值入口 | T0.3, T0.1 | 24 | ui-panel |
| T3.10 | 大地图/传送 (Map) | 世界地图、NPC 传送列表 | T0.3, T0.1 | 22 | ui-panel |
| T3.11 | 角色详情 (Information) | 属性面板、装备槽、战力详情 | T0.3, T0.1 | 21 | ui-panel |
| T3.12 | 坐骑面板 (Ride) | 坐骑列表、骑乘/下马 | T0.3, T0.1 | 25 | ui-panel |
| T3.13 | 复活面板 (Revive) | 死亡后复活选项（原地/安全区） | T0.3, T0.1 | 20 | ui-panel |
| T3.14 | NPC 对话 (NPCTalk) | NPC 对话框、选项列表、商店入口 | T0.3, T0.1 | 20 | ui-panel |
| T3.15 | 活动面板 (Activity) | 活动列表、参与/领奖 | T0.3, T0.1 | 25 | ui-panel |
| T3.16 | 排行榜 (Rank) | 等级/战力/行会排行 | T0.3, T0.1 | 26 | ui-panel |
| T3.17 | 挂机设置 (GuaJi) | 自动挂机参数配置 | T0.3, T0.1 | 23 | ui-panel |
| T3.18 | 自动出售 (AutoSell) | 自动拾取/出售规则配置 | T0.3, T0.1 | 25 | ui-panel |

---

## Lua 参考文件清单（供 Analyst 使用）

> 以下列出 CSClient/src 中与各任务直接相关的核心文件路径，Analyst Agent 分析时优先读取。

### Widget 参考

| Widget | Lua 文件路径 |
|---|---|
| BottomWidget | `src/app/widget/bottom/BottomWidgetView.lua`, `BottomWidgetCtrl.lua` |
| SkillWidget | `src/app/widget/skill/SkillWidgetView.lua`, `SkillWidgetCtrl.lua` |
| RightTopWidget | `src/app/widget/righttop/RightTopWidgetView.lua`, `RightTopWidgetCtrl.lua` |
| RoleInfoWidget | `src/app/widget/roleinfo/RoleInfoWidgetView.lua`, `RoleInfoWidgetCtrl.lua` |
| TopLayer | `src/app/widget/toplayer/TopLayerView.lua`, `TopLayerCtrl.lua` |

### Model 参考

| Model | Lua 文件路径 |
|---|---|
| PlayerData | `src/app/model/PlayerData.lua` |
| SkillData | `src/app/model/SkillData.lua` |
| ItemData | `src/app/model/ItemData.lua` |
| ChatData | `src/app/model/ChatData.lua` |
| TeamData | `src/app/model/TeamData.lua` |
| QuestData | `src/app/model/QuestData.lua` |
| GuildData | `src/app/model/GuildData.lua` |

### 网络包处理参考

| 模块 | Lua 文件路径 |
|---|---|
| 网络包注册 | `src/app/net/ServerPacketHandler.lua` |
| 网络包定义 | `src/app/net/PacketDefine.lua` |

---

## 依赖关系图（可视化）

```
T0.1 ──────┬──→ T1.1  T1.2  T1.3  T1.4  T1.5  T1.7  T1.8  T1.9  T1.10  T1.11
           │──→ T2.2  T2.3  T2.4  T2.5  T2.6  T2.7  T2.8
           │──→ T3.1  T3.2  T3.3  T3.5  T3.6  T3.7  T3.8  T3.9  T3.10  T3.11
           │    T3.12 T3.13 T3.14 T3.15 T3.16 T3.17 T3.18
           │
T0.2 ──────┤   (无直接下游，但 Model 注册是隐式前置)
           │
T0.3 ──────┼──→ T1.6
           │──→ T3.1  T3.2  T3.3  T3.4  T3.5  T3.6  T3.7  T3.8  T3.9  T3.10
           │    T3.11 T3.12 T3.13 T3.14 T3.15 T3.16 T3.17 T3.18
           │
T2.1 ──────┴──→ T2.2  T2.3  T2.4  T2.5  T2.6
           │
T2.2 ──────────→ T3.1
```
