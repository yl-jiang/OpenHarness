# oh vs ohmo 命令对比

根据 `pyproject.toml` 中的入口点配置：

```toml
[project.scripts]
openharness = "openharness.cli:app"
oh = "openharness.cli:app"
ohmo = "ohmo.cli:app"
```

## 对比表

| 维度 | `oh` (OpenHarness) | `ohmo` (Ohmo) |
|------|---------------------|---------------|
| **模块路径** | `openharness.cli:app` | `ohmo.cli:app` |
| **定位** | 完整的 Agent Harness 基础设施 | 个人智能体应用 (Personal Agent App) |
| **目标用户** | 开发者、研究人员、构建者 | 个人用户、日常助手场景 |
| **工作空间** | 当前项目目录 | `~/.ohmo` |
| **工具数量** | 43+ 完整工具集 | 轻量级工具子集 |
| **多智能体** | ✅ Swarm 完整支持 | 基础支持 |
| **网关功能** | 需外部集成 | ✅ 内置 Gateway |
| **引导提示词** | 项目级 CLAUDE.md | 内置 Bootstrap Prompts |
| **渠道配置** | 需手动配置 | ✅ 内置 Channel Config Flow |

## 使用场景

### `oh` - 开发助手
```bash
# 进入项目目录，启动完整的开发助手
cd my-project
oh

# 使用场景：
# - 代码编写与重构
# - Bug 诊断与修复
# - 代码审查
# - 多文件复杂的软件工程任务
```

### `ohmo` - 个人智能体
```bash
# 直接使用，无需进入特定项目
ohmo

# 使用场景：
# - 日常问答助手
# - 个人知识管理
# - 轻量级任务处理
# - IM 渠道集成 (Slack/Discord/Telegram)
```

## 版本历史

- **v0.1.2 (2026-04-06)**: `ohmo` 作为打包应用发布，包含完整的工作空间、网关、引导提示词和渠道配置流程
- **v0.1.0 (2026-04-01)**: OpenHarness 初始开源版本

## 选择建议

| 场景 | 推荐命令 |
|------|----------|
| 软件开发、代码编辑 | `oh` |
| 项目级复杂任务 | `oh` |
| 需要多智能体协调 | `oh` |
| 个人日常助手 | `ohmo` |
| IM 渠道集成 | `ohmo` |
| 快速问答、轻量任务 | `ohmo` |

---

**总结**: `oh` 是面向软件工程的全功能开发助手，`ohmo` 是面向个人用户的轻量级智能体应用。
