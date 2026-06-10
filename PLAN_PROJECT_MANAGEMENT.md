# 项目管理模块 — 阶段实施 / 编码计划

## 背景

`solo` / `wolo` 已经有结构化记录、待办、报告、决策、高亮和实验，但项目仍主要停留在字符串层：

- `solo` 待办使用 `category`
- `wolo` 待办、决策、高亮、实验使用 `project`
- onboard 目前能展示 records / todos / reports / decisions / highlights，但没有一等项目页面

根据 `DESIGN_PROJECT_MANAGEMENT.md` 的最新设计，V1 不做完整 AI 项目经理，而是先交付 **结构化项目容器 + 可追溯关联层**。这样可以先把项目作为可信数据结构落地，再逐步引入 AI 候选关联、停滞提醒和报告集成。

---

## 目标与非目标

### V1 目标

用户可以在 `solo` / `wolo` 中：

1. 创建、查看、更新、完成、归档、重新激活、删除项目。
2. 为项目添加、更新、完成、删除里程碑。
3. 将 records / todos / decisions / highlights / experiments 通过稳定关联层挂到项目上。
4. 在 CLI 和 onboard Web UI 中查看项目的完成度、活跃度和风险，而不是只看单一百分比。
5. 手动生成项目回顾报告；项目完成时在 UI / CLI 中提示生成回顾。

### V1.1 目标

1. 记录处理时生成高置信 / 中置信项目关联建议。
2. 高置信关联自动写入并提供撤销入口；中置信关联进入确认流。
3. 支持批量关联历史记录的 review 流程。
4. 增加项目停滞提醒、延期建议，以及周报 / 月报 / 年报中的项目摘要。

### V2 目标

1. 轻量只读时间线视图。
2. 项目模板、导出、统计图表、分享。
3. Wolo 团队协作与权限。

### 明确不做

- V1 不引入多人协作、权限管理、项目依赖关系。
- V1 不引入精确时间追踪，不要求用户输入小时数。
- V1 不接入 Jira / Asana / Linear 等外部同步。
- V1 不让 AI 静默创建、完成、归档、删除项目。
- V1 不引入完整甘特图库或新增 Node 依赖。

---

## 关键设计决策

### 1. 使用关联层，不把项目塞进每张业务表

不在 V1 中扩展 `todos.project_id` / `todos.milestone_id` 作为主路径。项目关联统一通过 `project_links` 表表达：

```text
records / todos / decisions / highlights / experiments
              │
              ▼
        project_links
              │
              ▼
          projects ─── milestones
```

理由：

- 可兼容旧 `solo.category` / `wolo.project` 字符串字段。
- 项目重命名不破坏历史数据。
- 删除项目时只删除项目容器和关联关系，不删除原始记录、待办、决策、高亮、实验。
- 后续 AI 候选关联可以用 `pending` / `rejected` 状态表达，不污染源数据。

### 2. 完成度、活跃度、风险分离

V1 不提供任意百分比手动编辑：

- **完成度**：有里程碑时按里程碑完成率；无里程碑但有关联待办时按待办完成率；都没有时返回 `null`。
- **活跃度**：按最近 7 / 30 天关联记录、待办、决策、高亮、实验数量聚合。
- **风险**：由目标日期、未完成里程碑、阻塞高亮、无活动天数计算，不直接影响完成度。

### 3. Solo / Wolo 共用主体模型，保留差异字段

`Project` / `Milestone` / `ProjectLink` / `ProjectAlias` 在 solo 和 wolo 中保持字段一致；wolo 项目额外支持 `stakeholders` 和 `success_criteria`。

### 4. V1 不做 AI 自动项目经理

V1 的 `link` / `unlink` 是人工可控能力。AI 只在 V1.1 进入，并且所有生命周期动作仍需要用户确认。

### 5. 回滚以数据安全为第一优先级

V1 只新增表和索引，不改写 records / todos / decisions / highlights / experiments 原表语义。即使回滚项目功能，原始日志和工作资产仍保持可用。

---

## 数据模型

### Python dataclass

在 `solo/core/models.py` 和 `wolo/core/models.py` 中新增：

```python
@dataclass(frozen=True)
class Project:
    id: str
    title: str
    description: str = ""
    status: str = "active"  # active | completed | archived
    priority: str = "medium"  # high | medium | low
    start_date: str = ""
    target_date: str = ""
    completed_at: str = ""
    archived_at: str = ""
    archive_reason: str = ""
    tags: str = ""
    created_at: str = ""
    updated_at: str = ""
    # wolo only
    stakeholders: str = ""
    success_criteria: str = ""
```

```python
@dataclass(frozen=True)
class Milestone:
    id: str
    project_id: str
    title: str
    description: str = ""
    status: str = "pending"  # pending | completed
    target_date: str = ""
    completed_at: str = ""
    created_at: str = ""
    updated_at: str = ""
```

```python
@dataclass(frozen=True)
class ProjectLink:
    id: str
    project_id: str
    entity_type: str  # record | todo | decision | highlight | experiment
    entity_id: str
    source: str = "user"  # user | ai_high_confidence | ai_candidate | migration
    confidence: str = ""  # high | medium | low | empty for user
    status: str = "active"  # active | pending | rejected
    created_at: str = ""
    updated_at: str = ""
```

```python
@dataclass(frozen=True)
class ProjectAlias:
    id: str
    project_id: str
    alias: str
    source: str = "user"  # user | migration | ai
    created_at: str = ""
```

### SQLite schema

在 `solo/core/store.py` 和 `wolo/core/store.py` 中新增表。`wolo` 的 `projects` 表比 `solo` 多 `stakeholders` 和 `success_criteria` 两列。

```sql
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    priority TEXT NOT NULL DEFAULT 'medium',
    start_date TEXT NOT NULL DEFAULT '',
    target_date TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT '',
    archive_reason TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_target_date ON projects(target_date);
CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at);
```

```sql
CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    target_date TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_milestones_project_id ON milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status);
```

```sql
CREATE TABLE IF NOT EXISTS project_links (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    confidence TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_project_links_project_id ON project_links(project_id);
CREATE INDEX IF NOT EXISTS idx_project_links_entity ON project_links(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_project_links_status ON project_links(status);
```

```sql
CREATE TABLE IF NOT EXISTS project_aliases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_project_aliases_project_id ON project_aliases(project_id);
CREATE INDEX IF NOT EXISTS idx_project_aliases_alias ON project_aliases(alias);
```

### 派生字段

Store 或 service 层返回项目时补充：

```text
completion_pct: number | null
completion_source: "milestones" | "todos" | "none"
milestone_count: number
completed_milestone_count: number
linked_record_count: number
linked_todo_count: number
completed_linked_todo_count: number
activity_7d: number
activity_30d: number
last_activity_at: string
risk_status: "normal" | "attention" | "at_risk"
open_blocker_count: number
```

风险规则：

1. 目标日期已过且项目未完成：`at_risk`
2. 目标日期 7 天内且完成度低于 80%：`attention`
3. 30 天无任何 active 关联活动：`attention`
4. 有未解决 blocker 高亮：`at_risk`
5. 其余：`normal`

---

## API 契约

### Store 方法

在 `SoloStore` / `WoloStore` 中新增：

```text
create_project(project)
update_project(project_id, **fields)
delete_project(project_id)
complete_project(project_id)
archive_project(project_id, reason="")
reactivate_project(project_id)
get_project(project_id)
list_projects(status=None, limit=None, offset=0)

create_milestone(milestone)
update_milestone(milestone_id, **fields)
complete_milestone(milestone_id)
delete_milestone(milestone_id)
list_milestones(project_id)

create_project_link(link)
update_project_link(link_id, **fields)
delete_project_link(link_id)
list_project_links(project_id=None, entity_type=None, entity_id=None, status=None)
accept_project_link(link_id)
reject_project_link(link_id)

create_project_alias(alias)
delete_project_alias(alias_id)
list_project_aliases(project_id)
```

### REST endpoints

在 `onboard/api/solo_routes.py` 和 `onboard/api/wolo_routes.py` 中新增：

```text
GET    /api/{app}/projects
POST   /api/{app}/projects
GET    /api/{app}/projects/{project_id}
PUT    /api/{app}/projects/{project_id}
DELETE /api/{app}/projects/{project_id}
PUT    /api/{app}/projects/{project_id}/complete
PUT    /api/{app}/projects/{project_id}/archive
PUT    /api/{app}/projects/{project_id}/reactivate

POST   /api/{app}/projects/{project_id}/milestones
PUT    /api/{app}/milestones/{milestone_id}
DELETE /api/{app}/milestones/{milestone_id}
PUT    /api/{app}/milestones/{milestone_id}/complete

GET    /api/{app}/projects/{project_id}/links
POST   /api/{app}/projects/{project_id}/links
DELETE /api/{app}/project-links/{link_id}
PUT    /api/{app}/project-links/{link_id}/accept
PUT    /api/{app}/project-links/{link_id}/reject

POST   /api/{app}/projects/{project_id}/review
```

`POST /review` 生成 `report_type = "project_review"` 的报告，并通过现有 reports 存储和查看链路展示。

### TypeScript 类型

在 `onboard/frontend/src/api/types.ts` 中新增：

```typescript
export type ProjectStatus = 'active' | 'completed' | 'archived';
export type ProjectRiskStatus = 'normal' | 'attention' | 'at_risk';
export type ProjectCompletionSource = 'milestones' | 'todos' | 'none';
export type ProjectLinkStatus = 'active' | 'pending' | 'rejected';
export type ProjectLinkSource = 'user' | 'ai_high_confidence' | 'ai_candidate' | 'migration';
export type ProjectEntityType = 'record' | 'todo' | 'decision' | 'highlight' | 'experiment';

export interface Project {
  id: string;
  title: string;
  description: string;
  status: ProjectStatus;
  priority: TodoPriority;
  start_date: string;
  target_date: string;
  completed_at: string;
  archived_at: string;
  archive_reason: string;
  tags: string;
  created_at: string;
  updated_at: string;
  stakeholders?: string;
  success_criteria?: string;
  completion_pct: number | null;
  completion_source: ProjectCompletionSource;
  milestone_count: number;
  completed_milestone_count: number;
  linked_record_count: number;
  linked_todo_count: number;
  completed_linked_todo_count: number;
  activity_7d: number;
  activity_30d: number;
  last_activity_at: string;
  risk_status: ProjectRiskStatus;
  open_blocker_count: number;
}
```

---

## 关键文件

本计划会触及超过 8 个文件，必须分阶段提交和验证。

### 后端：solo / wolo

- `solo/core/models.py`
- `solo/core/store.py`
- `solo/cli.py`
- `solo/processor.py`（V1.1）
- `solo/prompts.py`（V1.1）
- `wolo/core/models.py`
- `wolo/core/store.py`
- `wolo/cli.py`
- `wolo/processor.py`（V1.1）
- `wolo/prompts.py`（V1.1）

### 后端：onboard

- `onboard/services/solo_service.py`
- `onboard/services/wolo_service.py`
- `onboard/api/solo_routes.py`
- `onboard/api/wolo_routes.py`

### 前端：onboard

- `onboard/frontend/src/api/types.ts`
- `onboard/frontend/src/api/client.ts`
- `onboard/frontend/src/pages/Projects.tsx`
- `onboard/frontend/src/pages/ProjectDetail.tsx`
- `onboard/frontend/src/components/ProjectCard.tsx`
- `onboard/frontend/src/components/ProjectStatusBadge.tsx`
- `onboard/frontend/src/components/ProjectCompletionBar.tsx`
- `onboard/frontend/src/components/MilestoneList.tsx`
- `onboard/frontend/src/App.tsx`
- `onboard/frontend/src/components/Sidebar.tsx`
- `onboard/frontend/src/pages/Dashboard.tsx`
- `onboard/frontend/src/pages/Reports.tsx`

### 测试

- `tests/test_solo/test_store.py`
- `tests/test_wolo/test_store.py`
- `tests/test_solo/test_cli.py`
- `tests/test_wolo/test_cli.py`
- `tests/test_solo/test_onboard.py`
- `tests/test_wolo/test_onboard.py`（如不存在则新增）

---

## 阶段 0：基线和契约测试

**目标**：在动实现前固定预期，避免迁移和进度口径变形。

**步骤**：

1. 在 solo / wolo store 测试中新增项目空状态测试：初始化 workspace 后 `list_projects()` 返回空列表。
2. 增加 schema 迁移测试：旧数据库只包含现有表时，初始化 store 后新增项目表和索引。
3. 增加计算口径测试用例：
   - 有 2 个里程碑，完成 1 个，完成度为 50。
   - 无里程碑但有 4 个关联待办，完成 1 个，完成度为 25。
   - 无里程碑且无待办，完成度为 `None`。
   - 目标日期过期只改变风险，不改变完成度。

**验证**：

```bash
uv run pytest -q tests/test_solo/test_store.py tests/test_wolo/test_store.py
```

---

## 阶段 1：后端数据模型与 Store

**目标**：完成 Project / Milestone / ProjectLink / ProjectAlias 的持久化、迁移和派生统计。

**步骤**：

1. 在 `solo/core/models.py` 新增 `Project`, `Milestone`, `ProjectLink`, `ProjectAlias`。
2. 在 `wolo/core/models.py` 新增同名 dataclass，并在 `Project` 中启用 `stakeholders`, `success_criteria`。
3. 在 `solo/core/store.py` 增加 schema，提升 `_SCHEMA_VERSION`。
4. 在 `wolo/core/store.py` 增加 schema，提升 `_SCHEMA_VERSION`。
5. 实现项目 CRUD、状态切换、里程碑 CRUD、关联 CRUD、别名 CRUD。
6. 实现项目列表和详情的派生字段计算。
7. 确保 `delete_project` 只删除 projects / milestones / project_links / project_aliases，不删除源业务实体。
8. 不从历史 `solo.category` / `wolo.project` 字符串自动创建项目。
9. 当用户创建项目或手动 link 时，如果旧字符串与项目标题或别名匹配，写入 `ProjectAlias(source="migration")` 或 `ProjectLink(source="migration", status="pending")`，由用户确认后再变为 active link。

**验证**：

```bash
uv run pytest -q tests/test_solo/test_store.py tests/test_wolo/test_store.py
uv run ruff check solo wolo tests/test_solo tests/test_wolo
```

---

## 阶段 2：CLI 工作流

**目标**：提供完整人工可控项目管理路径。

**步骤**：

1. 在 `solo/cli.py` 新增 `project_app = typer.Typer(name="project")`。
2. 在 `wolo/cli.py` 新增同等命令组。
3. 实现命令：
   - `project list`
   - `project create <title>`
   - `project show <project>`
   - `project update <project>`
   - `project complete <project>`
   - `project archive <project> --reason`
   - `project reactivate <project>`
   - `project delete <project>`
   - `project milestone add <project> <title>`
   - `project milestone complete <milestone>`
   - `project milestone delete <milestone>`
   - `project link <project> <entity_type> <entity_id>`
   - `project unlink <link_id>`
4. `project list` 显示完成度、风险、里程碑数、最近活动。
5. `project show` 显示项目详情、里程碑、关联实体统计和最近关联活动。
6. 删除项目时输出明确提示：源记录、待办、决策、高亮、实验不会被删除。

**验证**：

```bash
uv run solo project --help
uv run wolo project --help
uv run solo project create "学习 Rust"
uv run solo project milestone add "学习 Rust" "读完官方教程"
uv run solo project show "学习 Rust"
uv run pytest -q tests/test_solo/test_cli.py tests/test_wolo/test_cli.py
```

---

## 阶段 3：Onboard 后端 API

**目标**：把 Store 能力暴露给 Web UI，并保证输入校验一致。

**步骤**：

1. 在 `onboard/services/solo_service.py` 增加项目服务方法。
2. 在 `onboard/services/wolo_service.py` 增加同等服务方法。
3. 服务层统一校验：
   - `status` 必须为 `active | completed | archived`
   - `priority` 必须为 `high | medium | low`
   - `entity_type` 必须为 `record | todo | decision | highlight | experiment`
   - 日期字段允许空字符串；非空时必须为 `YYYY-MM-DD`
4. 在 `onboard/api/solo_routes.py` 添加 REST endpoints。
5. 在 `onboard/api/wolo_routes.py` 添加 REST endpoints。
6. `GET /projects` 返回带派生字段的项目列表。
7. `GET /projects/{id}` 返回项目、里程碑、active links、pending links、最近活动。
8. API 删除项目时返回 `{ "deleted": true }`，不返回成功形状掩盖未找到错误。

**验证**：

```bash
uv run pytest -q tests/test_solo/test_onboard.py tests/test_wolo/test_onboard.py
uv run ruff check onboard tests/test_solo tests/test_wolo
```

手动 smoke：

```bash
uv run onboard run --port 8090
curl http://127.0.0.1:8090/api/solo/projects
curl -X POST http://127.0.0.1:8090/api/solo/projects \
  -H "Content-Type: application/json" \
  -d '{"title":"测试项目","target_date":"2026-12-31"}'
```

---

## 阶段 4：Onboard 前端 V1 页面

**目标**：交付看板、列表、详情页，不引入时间线依赖。

**步骤**：

1. 在 `onboard/frontend/src/api/types.ts` 添加 Project / Milestone / ProjectLink 类型。
2. 在 `onboard/frontend/src/api/client.ts` 添加项目 API 方法。
3. 新增组件：
   - `ProjectStatusBadge.tsx`
   - `ProjectCompletionBar.tsx`
   - `ProjectCard.tsx`
   - `MilestoneList.tsx`
4. 新增 `Projects.tsx`：
   - 看板：Active / Completed / Archived 三列
   - 列表：复用 `DataTable`
   - 支持搜索、状态筛选、风险筛选
   - 支持新建、完成、归档、重新激活、删除
5. 新增 `ProjectDetail.tsx`：
   - 概览：描述、完成度、活跃度、风险
   - 里程碑：增删改和完成
   - 活动：显示关联 records / todos / decisions / highlights / experiments 摘要
   - 统计：里程碑数、关联实体数、最近活动、阻塞数
6. 更新 `App.tsx` 注册 `/projects` 和 `/projects/:id`。
7. 更新 `Sidebar.tsx` 增加 Projects 导航。
8. 空状态文案强调"项目可以从一条标题开始"。

**验证**：

```bash
cd onboard/frontend && npx tsc --noEmit
```

手动 smoke：

```bash
uv run onboard run --port 8090
# 打开 /projects
# 创建项目 → 添加里程碑 → 完成里程碑 → 归档 → 重新激活 → 删除
```

---

## 阶段 5：V1 集成与项目回顾

**目标**：让项目出现在现有工作流中，并生成项目回顾报告。

**步骤**：

1. Dashboard 增加 Active Projects 摘要：项目数、风险项目数、最近活动项目。
2. Todos 页面展示已关联项目名称；没有关联时仍保留原 `category` / `project` 字段展示。
3. Records 页面支持按项目筛选：通过 `project_links` 反查关联 record。
4. Reports 页面支持展示 `project_review` 类型报告。
5. `solo project review <project>` 和 `wolo project review <project>` 生成项目回顾报告。
6. `POST /api/{app}/projects/{id}/review` 调用同一生成路径。
7. 项目完成后 CLI / UI 提示可生成回顾，但不强制生成。
8. 更新 `CHANGELOG.md` `[Unreleased]`，说明项目管理 V1 能力。

**验证**：

```bash
uv run pytest -q tests/test_solo tests/test_wolo
cd onboard/frontend && npx tsc --noEmit
uv run ruff check solo wolo onboard tests
```

完整手动流：

```bash
uv run solo project create "学习 Rust"
uv run solo project milestone add "学习 Rust" "读完官方教程"
uv run solo project link "学习 Rust" record <record_id>
uv run solo project complete "学习 Rust"
uv run solo project review "学习 Rust"
```

---

## 阶段 6：V1.1 AI 辅助关联

**目标**：让 AI 提供建议，但不越权。

**步骤**：

1. 在 `solo/prompts.py` / `wolo/prompts.py` 中增加项目关联输出要求。
2. 在 `solo/processor.py` / `wolo/processor.py` 解析项目候选：
   - 高置信：写入 `project_links(status="active", source="ai_high_confidence", confidence="high")`
   - 中置信：写入 `project_links(status="pending", source="ai_candidate", confidence="medium")`
   - 低置信：不落库
3. 记录处理结果中展示高置信关联和撤销命令。
4. CLI 增加：
   - `project links --pending`
   - `project link accept <link_id>`
   - `project link reject <link_id>`
   - `project link --keyword <text> --days <n> --review`
5. Onboard 项目详情页展示 pending links，支持 accept / reject。
6. 增加项目停滞检测：默认 30 天无 active 关联活动时生成提醒。
7. 周报 / 月报 / 年报 prompt 加入项目摘要、风险、完成项目回顾亮点。

**验证**：

```bash
uv run pytest -q tests/test_solo tests/test_wolo
uv run ruff check solo wolo tests
```

手动验证路径：

1. 创建项目别名。
2. 处理一条明确提到项目的记录，确认高置信自动关联。
3. 撤销该关联，确认源记录仍存在。
4. 处理一条模糊记录，确认进入 pending links。
5. accept / reject 后确认项目详情页同步变化。

---

## 阶段 7：V2 增强能力

**目标**：在 V1 数据模型被验证后，再扩展高级视图和复用能力。

**步骤**：

1. 轻量时间线视图：先用现有 CSS 和数据表实现只读时间轴，不新增甘特图库。
2. 项目模板：保存里程碑结构和默认字段，不保存具体记录关联。
3. 项目导出：CSV / JSON / Markdown。
4. 项目统计图表：活跃趋势、完成趋势、项目分布。
5. 项目回顾分享：Markdown 优先，图片导出最后考虑。
6. Wolo 协作：独立设计权限模型后再实现，不复用单人 workspace 假设。

**验证**：

```bash
uv run pytest -q
cd onboard/frontend && npx tsc --noEmit
```

---

## 测试矩阵

| 路径 | Happy path | Error path | Edge case |
|------|------------|------------|-----------|
| Project CRUD | 创建、更新、完成、归档、重激活、删除 | 未找到项目、非法状态、非法日期 | 空 target_date、重复标题、删除后源实体仍存在 |
| Milestone | 添加、完成、删除 | 未找到里程碑、跨项目访问 | 无里程碑项目完成度为 `null` 或按待办计算 |
| ProjectLink | 手动关联、解除关联 | 非法 entity_type、重复关联、未找到实体 | 同一实体关联多个项目 |
| Completion | 里程碑完成率、待办完成率 | 无可计算来源 | target_date 过期不改变完成度 |
| Risk | 过期、阻塞、停滞 | 无 target_date | archived / completed 项目不显示 active 风险 |
| CLI | list / show / link / unlink | 参数缺失、非法 ID | 用标题或 ID 查找项目 |
| API | REST CRUD 和状态切换 | 404 / 422 | workspace 参数切换 |
| Frontend | 看板、列表、详情页 | API 错误 toast | 空状态、长标题、无完成度 |
| Review | 生成 project_review 报告 | 模型失败或空内容 | 无关联记录的项目也能生成基础回顾 |
| AI links | 高置信自动关联、中置信待确认 | reject 后不再污染 active 数据 | 撤销后源记录保留 |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Schema 迁移破坏现有数据 | 只新增表和索引；不改写源业务表；迁移前后跑 store 测试 |
| `solo.category` / `wolo.project` 与新项目重复 | 通过 `project_aliases` 兼容；历史数据只生成候选，不强制改写 |
| 完成度口径误导用户 | 完成度、活跃度、风险分开展示；无来源时显示未量化 |
| AI 误关联污染长期记忆 | V1 不做 AI；V1.1 使用 pending / rejected 状态和撤销入口 |
| 大数据量下项目列表慢 | 索引 `project_links(project_id)`、`project_links(entity_type, entity_id)`；先查询项目再批量聚合关联实体 |
| 前端范围过大 | V1 只做看板、列表、详情页；时间线和模板后移 |
| 删除项目造成误删 | `delete_project` 只删除项目表和关联表；测试覆盖源实体保留 |
| 报告生成失败 | 项目本体和关联数据仍可用；错误直接显示，不写入成功形状报告 |

---

## 依赖

### Python

无需新增 Python 依赖，继续使用现有 `typer`、`rich`、`sqlite3`、FastAPI 和现有模型调用链。

### Node.js

V1 无需新增 Node 依赖。V2 时间线先使用现有 React + CSS 实现只读视图，除非真实交互需求证明需要额外库。

### 外部服务 / API Key

不新增外部服务或第三方账号。项目回顾和 AI 关联复用现有 OpenHarness provider profile。

---

## 最小可回滚方案

如果 V1 上线后方向不对：

1. 隐藏 onboard Projects 导航。
2. 禁用 CLI `project` 命令组入口。
3. 保留新增表，不删除数据。
4. records / todos / reports / decisions / highlights / experiments 不受影响。
5. 后续可通过迁移脚本导出 `projects` / `project_links` 为 Markdown 或 JSON。
