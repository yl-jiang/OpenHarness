# 项目管理模块 — 实施方案

## 背景

**问题**：solo/wolo 目前在 Todo 项上有非结构化的 `project`（wolo）或 `category`（solo）字符串字段，但没有专门的项目实体来支持生命周期管理（里程碑、状态、时间线、进度追踪）。管理复杂工作/个人项目的用户需要超越扁平待办列表的层级组织。

**目标**：为 solo（个人项目）和 wolo（工作项目）增加结构化的项目管理模块，在 onboard Web 仪表盘中提供专门的项目页面，支持看板/列表/时间线视图，参考 Linear 和 Things 3 的设计。

**预期成果**：用户可以创建和管理带里程碑和任务的项目，跟踪进度，并在丰富的 Web UI 中查看项目，与现有日志、待办和报告集成。

---

## 方案

### 1. 数据模型 — 项目实体

在 solo 和 wolo 中新增 `Project` dataclass 和 SQLite 表：

```python
# solo/core/models.py & wolo/core/models.py
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
    tags: str = ""  # 逗号分隔
    created_at: str = ""
    updated_at: str = ""
    # 仅 wolo 字段
    stakeholders: str = ""  # 逗号分隔的姓名/角色
    success_criteria: str = ""

    # 派生字段（从关联的 todos/records 计算）
    # - progress_pct: 已完成 todos 百分比
    # - milestone_count: 里程碑数量
    # - last_activity: 最近的记录/待办日期
```

新增 `Milestone` dataclass：

```python
@dataclass(frozen=True)
class Milestone:
    id: str
    project_id: str
    title: str
    description: str = ""
    status: str = "pending"  # pending | in_progress | completed
    target_date: str = ""
    completed_at: str = ""
    created_at: str = ""
```

**关键设计决策**：
- **扁平层级**：Project → Milestone → Todo（复用现有 Todo 模型，增加 `milestone_id` 字段）
- **Markdown 友好**：项目描述存为 Markdown，在 onboard UI 中渲染
- **AI 集成**：项目可从日志条目自动创建（类似 todos/experiments）
- **Solo vs Wolo**：Solo 项目侧重个人目标/习惯；Wolo 项目侧重交付物/干系人

### 2. 存储 — SQLite Schema

扩展 `solo/core/store.py` 和 `wolo/core/store.py`：

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
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_target_date ON projects(target_date);

-- 仅 wolo 新增列
ALTER TABLE projects ADD COLUMN stakeholders TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN success_criteria TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    target_date TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_milestones_project_id ON milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status);

-- 扩展现有 todos 表
ALTER TABLE todos ADD COLUMN project_id TEXT DEFAULT '';
ALTER TABLE todos ADD COLUMN milestone_id TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_todos_project_id ON todos(project_id);
CREATE INDEX IF NOT EXISTS idx_todos_milestone_id ON todos(milestone_id);
```

为 `SoloStore` / `WoloStore` 新增 CRUD 方法：
- `create_project(project)`, `update_project(id, **fields)`, `delete_project(id)`
- `list_projects(status=None, limit, offset)` → 返回带计算进度的项目列表
- `get_project(id)` → 返回项目 + 里程碑 + 关联的 todos
- `create_milestone(milestone)`, `update_milestone(id, **fields)`, `delete_milestone(id)`
- `list_milestones(project_id)` → 返回项目的里程碑列表

### 3. CLI 命令

新增 `solo project` 和 `wolo project` 子命令组：

```python
# solo/cli.py & wolo/cli.py
project_app = typer.Typer(name="project", help="管理项目")
app.add_typer(project_app)

@project_app.command("list")
def project_list(status: str = "active", limit: int = 20):
    """列出项目及进度摘要"""
    # 表格输出：title | status | progress | target_date | milestones

@project_app.command("create")
def project_create(title: str, description: str = "", target_date: str = ""):
    """创建新项目"""

@project_app.command("show")
def project_show(project_id: str):
    """查看项目详情、里程碑和关联的待办"""

@project_app.command("update")
def project_update(project_id: str, status: str = None, target_date: str = None):
    """更新项目字段"""

@project_app.command("delete")
def project_delete(project_id: str):
    """删除项目（解耦待办，保留待办本身）"""

@project_app.command("milestone")
def milestone_add(project_id: str, title: str, target_date: str = ""):
    """为项目添加里程碑"""
```

### 4. Onboard 后端 API

在 `onboard/api/solo_routes.py` 和 `onboard/api/wolo_routes.py` 中新增项目端点：

```python
# GET /api/{app}/projects?status=&limit=&offset=
#   → 列出项目及进度统计
# GET /api/{app}/projects/:id
#   → 项目详情（含里程碑和关联待办）
# POST /api/{app}/projects
#   → 创建项目 { title, description, target_date, ... }
# PUT /api/{app}/projects/:id
#   → 更新项目字段
# DELETE /api/{app}/projects/:id
#   → 删除项目
# POST /api/{app}/projects/:id/milestones
#   → 添加里程碑 { title, target_date }
# PUT /api/{app}/milestones/:id
#   → 更新里程碑
# DELETE /api/{app}/milestones/:id
#   → 删除里程碑
# GET /api/{app}/projects/:id/stats
#   → 聚合统计：完成百分比、耗时、关联记录数
```

在 `onboard/services/solo_service.py` 和 `wolo_service.py` 中新增服务层：
- 封装 store 方法，添加计算字段（进度 %、最近活动）
- 校验输入（状态枚举、日期格式）

### 5. Onboard 前端 — 项目页面

新增 `Projects.tsx` 页面，支持三种视图模式：

#### 5.1 看板视图（默认）
- **列**：Active | Completed | Archived
- **卡片**：项目卡片显示标题、进度条、里程碑数量、目标日期、优先级徽标
- **拖拽**：在状态列之间移动项目（复用 Todos.tsx 中的 `@dnd-kit/core`）
- **点击卡片**：导航到项目详情页

#### 5.2 列表视图
- **表格**：Title | Status | Progress | Milestones | Target Date | Priority
- **可排序列**，按状态/优先级筛选
- **行内操作**：完成、归档、删除

#### 5.3 时间线视图（甘特图风格）
- **横条**：每个项目为从 start_date 到 target_date 的横条
- **里程碑**：时间线上的菱形标记
- **库**：使用 `vis-timeline` 或 `react-calendar-timeline`（轻量、React 友好）
- **缩放**：日/周/月粒度

#### 5.4 项目详情页（`ProjectDetail.tsx`）
- **头部**：标题、状态徽标、进度条、目标日期、操作按钮（编辑、完成、归档）
- **标签页**：
  - **概览**：描述（Markdown）、里程碑列表、关联待办摘要
  - **里程碑**：可编辑列表，支持状态切换、增删
  - **任务**：关联的待办看板视图（按 project_id 筛选）
  - **活动**：关联的日志记录（按匹配项目标题的 tags 筛选）
- **侧栏**：统计（完成 %、剩余天数、里程碑进度）

### 6. UI 组件（可复用）

在 `onboard/frontend/src/components/` 中创建：
- `ProgressBar.tsx`：带百分比标签的水平进度条
- `ProjectCard.tsx`：项目看板卡片（标题、进度、里程碑、目标日期）
- `MilestoneList.tsx`：可编辑的里程碑列表，支持状态切换
- `Timeline.tsx`：甘特时间线组件（vis-timeline 封装）

### 7. 路由与导航

更新 `onboard/frontend/src/App.tsx`：
```tsx
const Projects = lazy(() => import('./pages/Projects').then(m => ({ default: m.Projects })));
const ProjectDetail = lazy(() => import('./pages/ProjectDetail').then(m => ({ default: m.ProjectDetail })));

// 新增路由
<Route path="projects" element={<Projects appName={appName} />} />
<Route path="projects/:id" element={<ProjectDetail appName={appName} />} />
```

更新 `Sidebar.tsx`：
- 在 "Todos" 和 "Reports" 之间添加 "Projects" 导航项
- 图标：公文包或文件夹

### 8. TypeScript 类型

扩展 `onboard/frontend/src/api/types.ts`：

```typescript
export type ProjectStatus = "active" | "completed" | "archived";

export interface Project {
  id: string;
  title: string;
  description: string;
  status: ProjectStatus;
  priority: TodoPriority;
  start_date: string;
  target_date: string;
  completed_at: string;
  tags: string;
  created_at: string;
  updated_at: string;
  // 计算字段
  progress_pct: number;  // 0-100
  milestone_count: number;
  todo_count: number;
  completed_todo_count: number;
  last_activity: string;
  // 仅 Wolo
  stakeholders?: string;
  success_criteria?: string;
}

export interface Milestone {
  id: string;
  project_id: string;
  title: string;
  description: string;
  status: "pending" | "in_progress" | "completed";
  target_date: string;
  completed_at: string;
  created_at: string;
}

// 扩展 Todo 接口
export interface Todo {
  // ... 现有字段
  project_id?: string;
  milestone_id?: string;
}
```

### 9. AI 集成（未来）

项目可从日志条目自动创建：
- Processor 检测项目级主题（跨条目目标、重复话题）
- 自动建议创建项目并关联待办
- 年度报告集成："本年度完成的项目"章节

---

## 关键文件

### 后端（solo/wolo）
- `solo/core/models.py` — 新增 Project、Milestone dataclass
- `solo/core/store.py` — 新增 SQLite 表 + CRUD 方法
- `solo/cli.py` — 新增 `project` 子命令组
- `wolo/core/models.py` — 同 solo + stakeholders/success_criteria 字段
- `wolo/core/store.py` — 同 solo + wolo 特定字段
- `wolo/cli.py` — 同 solo

### 后端（onboard）
- `onboard/api/solo_routes.py` — 新增项目 CRUD 端点
- `onboard/api/wolo_routes.py` — 同 solo
- `onboard/services/solo_service.py` — 封装 store 方法，添加计算字段
- `onboard/services/wolo_service.py` — 同 solo

### 前端（onboard）
- `onboard/frontend/src/pages/Projects.tsx` — 主项目页面（看板/列表/时间线视图）
- `onboard/frontend/src/pages/ProjectDetail.tsx` — 项目详情页（标签页）
- `onboard/frontend/src/components/ProgressBar.tsx` — 可复用进度条
- `onboard/frontend/src/components/ProjectCard.tsx` — 看板卡片
- `onboard/frontend/src/components/MilestoneList.tsx` — 可编辑里程碑列表
- `onboard/frontend/src/components/Timeline.tsx` — 甘特时间线封装
- `onboard/frontend/src/api/types.ts` — 新增 Project、Milestone 类型
- `onboard/frontend/src/api/client.ts` — 新增项目 API 方法
- `onboard/frontend/src/App.tsx` — 新增路由
- `onboard/frontend/src/components/Sidebar.tsx` — 新增 "Projects" 导航项

---

## 实施阶段

### 阶段 1：后端数据模型与存储（solo + wolo）
**目标**：为 solo 和 wolo 添加 Project/Milestone 实体及完整 CRUD

**步骤**：
1. 在 `solo/core/models.py` 中添加 `Project` 和 `Milestone` dataclass
2. 在 `solo/core/store.py` 中添加 SQLite schema（projects、milestones 表 + todos.project_id/milestone_id）
3. 添加 CRUD 方法：`create_project`, `update_project`, `delete_project`, `list_projects`, `get_project`, `create_milestone`, `update_milestone`, `delete_milestone`, `list_milestones`
4. 添加迁移逻辑（schema 版本号升级，ALTER TABLE 扩展现有 todos）
5. 对 `wolo/core/models.py` 和 `wolo/core/store.py` 重复以上操作（增加 stakeholders、success_criteria 字段）
6. 运行 `uv run pytest tests/test_solo tests/test_wolo` 验证无回归

**验证**：
```bash
uv run solo project list  # 空列表
uv run solo project create "学习 Rust" --target-date 2026-12-31
uv run solo project list  # 显示新项目
uv run solo project show <id>  # 显示详情
```

### 阶段 2：CLI 命令（solo + wolo）
**目标**：添加 `solo project` 和 `wolo project` 子命令组

**步骤**：
1. 在 `solo/cli.py` 中添加 `project_app = typer.Typer(name="project")`
2. 实现命令：`list`, `create`, `show`, `update`, `delete`, `milestone`
3. 格式化输出为表格（使用 `rich` 或纯文本）
4. 对 `wolo/cli.py` 重复
5. 添加帮助文本和示例

**验证**：
```bash
uv run solo project --help
uv run solo project create "Q4 OKR" --description "..." --target-date 2026-09-30
uv run solo project milestone <project-id> "起草提案" --target-date 2026-07-15
uv run solo project show <project-id>  # 显示里程碑
```

### 阶段 3：Onboard 后端 API
**目标**：添加项目 CRUD 和统计的 REST 端点

**步骤**：
1. 在 `onboard/api/solo_routes.py` 中添加项目路由：
   - `GET /api/solo/projects`, `GET /api/solo/projects/:id`, `POST /api/solo/projects`, `PUT /api/solo/projects/:id`, `DELETE /api/solo/projects/:id`
   - `POST /api/solo/projects/:id/milestones`, `PUT /api/solo/milestones/:id`, `DELETE /api/solo/milestones/:id`
   - `GET /api/solo/projects/:id/stats`
2. 在 `onboard/services/solo_service.py` 中添加服务方法：
   - `list_projects_with_stats()` → 关联 projects 与 todo/milestone 计数
   - `get_project_detail(id)` → 返回项目 + 里程碑 + 关联的 todos
3. 对 `wolo_routes.py` 和 `wolo_service.py` 重复
4. 在 `onboard/server.py` 中注册路由
5. 使用 `curl` 或 Swagger UI（`http://localhost:8090/docs`）测试

**验证**：
```bash
uv run onboard run --port 8090
curl http://localhost:8090/api/solo/projects
curl -X POST http://localhost:8090/api/solo/projects \
  -H "Content-Type: application/json" \
  -d '{"title": "测试项目", "target_date": "2026-12-31"}'
curl http://localhost:8090/api/solo/projects/<id>
```

### 阶段 4：Onboard 前端 — 项目页面（看板 + 列表）
**目标**：添加带看板和列表视图的项目页面

**步骤**：
1. 在 `onboard/frontend/src/api/types.ts` 中添加 `Project` 和 `Milestone` 类型
2. 在 `onboard/frontend/src/api/client.ts` 中添加 API 方法：
   - `listProjects(app, status?)`, `getProject(app, id)`, `createProject(app, data)`, `updateProject(app, id, data)`, `deleteProject(app, id)`
   - `createMilestone(app, projectId, data)`, `updateMilestone(app, id, data)`, `deleteMilestone(app, id)`
3. 创建 `ProgressBar.tsx` 组件（带百分比的水平进度条）
4. 创建 `ProjectCard.tsx` 组件（标题、进度条、里程碑数量、目标日期、优先级徽标）
5. 创建 `Projects.tsx` 页面：
   - 视图切换：看板 | 列表
   - 看板：3 列（Active, Completed, Archived），使用 `@dnd-kit/core` 拖拽
   - 列表：可排序列的 DataTable
   - "新建项目"按钮 → 模态表单
6. 在 `App.tsx` 中添加路由：`<Route path="projects" element={<Projects appName={appName} />} />`
7. 在 `Sidebar.tsx` 中添加 "Projects" 导航项

**验证**：
```bash
cd onboard/frontend && npm run dev
# 访问 http://localhost:5173/projects
# 创建项目，在列之间拖拽，验证列表视图
```

### 阶段 5：Onboard 前端 — 项目详情页
**目标**：添加带标签页的项目详情页（概览、里程碑、任务、活动）

**步骤**：
1. 创建 `MilestoneList.tsx` 组件（可编辑列表，支持状态切换、增删）
2. 创建 `ProjectDetail.tsx` 页面：
   - 头部：标题、状态徽标、进度条、目标日期、操作按钮
   - 标签页：概览（Markdown 描述 + 统计）、里程碑（可编辑列表）、任务（筛选后的待办看板）、活动（关联记录）
3. 在 `App.tsx` 中添加路由：`<Route path="projects/:id" element={<ProjectDetail appName={appName} />} />`
4. 连接 API 调用：`getProject(id)`, `updateProject(id)`, `createMilestone(projectId)` 等
5. 从项目页面添加导航（点击卡片 → 详情页）

**验证**：
```bash
# 访问 http://localhost:5173/projects/<id>
# 添加里程碑，切换状态，查看关联的待办
```

### 阶段 6：Onboard 前端 — 时间线视图
**目标**：添加甘特图风格的项目时间线视图

**步骤**：
1. 安装 `vis-timeline` 或 `react-calendar-timeline`：
   ```bash
   cd onboard/frontend && npm install vis-timeline vis-data
   ```
2. 创建 `Timeline.tsx` 组件：
   - 在 React 组件中封装 vis-timeline
   - 将项目渲染为横条（start_date → target_date）
   - 将里程碑渲染为菱形标记
   - 缩放控件：日/周/月
3. 在 `Projects.tsx` 中添加 "时间线" 视图切换
4. 使用 CSS 变量匹配设计系统

**验证**：
```bash
# 访问 http://localhost:5173/projects
# 切换到时间线视图，缩放，验证项目横条和里程碑标记
```

### 阶段 7：集成与收尾
**目标**：将项目与现有功能关联（待办、记录、报告）

**步骤**：
1. 扩展 `Todos.tsx` 在卡片上显示项目名称（wolo 已有 `todo.project` 字段）
2. 在 Dashboard 中添加 "Projects" 章节："活跃项目"摘要及进度条
3. 在 Records 页面中添加项目筛选（按匹配项目标题的 tags 筛选）
4. 在年度报告生成中添加 "本年度完成的项目"（`solo/gateway/report_runner.py`）
5. 运行完整测试套件：`uv run pytest -q`
6. 运行前端类型检查：`cd onboard/frontend && npx tsc --noEmit`
7. 更新 CHANGELOG.md `[Unreleased]` 章节

**验证**：
```bash
uv run pytest -q tests/test_solo tests/test_wolo
cd onboard/frontend && npx tsc --noEmit
uv run onboard run
# 测试完整流程：创建项目 → 添加里程碑 → 关联待办 → 在 Dashboard 查看 → 生成报告
```

---

## 验证计划

### 单元测试
- `tests/test_solo/test_store.py`：测试项目/里程碑 CRUD、schema 迁移
- `tests/test_wolo/test_store.py`：同 solo + wolo 特定字段
- `tests/test_onboard/test_solo_routes.py`：测试项目 API 端点
- `tests/test_onboard/test_wolo_routes.py`：同 solo

### 集成测试
- 通过 CLI 创建项目 → 在 onboard API 验证 → 在前端验证
- 添加里程碑 → 关联待办 → 验证进度计算
- 在看板中拖拽项目 → 验证状态更新
- 生成年度报告 → 验证 "完成的项目" 章节

### 手动测试
```bash
# 1. CLI 工作流
uv run solo project create "学习 Python" --target-date 2026-12-31
uv run solo project milestone <id> "阅读文档" --target-date 2026-07-15
uv run solo project milestone <id> "构建项目" --target-date 2026-09-30
uv run solo project show <id>

# 2. Onboard API
uv run onboard run --port 8090
curl http://localhost:8090/api/solo/projects
curl http://localhost:8090/api/solo/projects/<id>/stats

# 3. 前端
cd onboard/frontend && npm run dev
# 访问 /projects，创建/编辑/删除项目，添加里程碑，切换视图
# 访问 /projects/<id>，验证标签页和关联数据
```

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| **Schema 迁移破坏现有数据** | 升级 schema 版本，ALTER TABLE 使用 DEFAULT 值，在备份上测试 |
| **时间线库过重** | 先实现看板/列表，时间线作为阶段 6（可选） |
| **AI 自动创建过多项目** | 先仅手动创建，未来阶段添加 AI 建议 |
| **Wolo stakeholders 字段未被使用** | 设为可选，添加 UI 提示"用于团队项目" |
| **大数据集下进度计算慢** | 在 projects 表中缓存 progress_pct，待办状态变更时更新 |

---

## 范围边界

**范围内**：
- 项目/里程碑 CRUD（CLI + API + UI）
- Onboard 中的看板、列表、时间线视图
- 将现有待办关联到项目/里程碑
- 进度追踪与统计
- Dashboard 集成（活跃项目摘要）
- 年度报告集成（完成的项目）

**范围外**（未来阶段）：
- 从日志条目 AI 自动创建项目
- 项目模板
- 跨项目依赖
- 时间追踪（耗时）
- 协作（多用户项目）
- 导出到外部工具（Jira、Asana）

---

## 依赖

### Python（已在 pyproject.toml 中）
- `typer` — CLI 框架（现有）
- `rich` — CLI 表格格式化（现有）

### Node.js（添加到 onboard/frontend/package.json）
- `vis-timeline` + `vis-data` — 时间线视图（阶段 6）

无需新增 Python 依赖。
