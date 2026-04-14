# 我的第一个 GitHub PR：全流程记录

> 本文记录了向开源项目 [HKUDS/OpenHarness](https://github.com/HKUDS/OpenHarness) 提交 PR #66 的完整过程。  
> 对应修复：TUI 模式下并发权限 Modal 互相覆盖问题。  
> 写给未来的自己，也写给同样是第一次提 PR 的人。

---

## 一、基本概念：PR 是什么？

**PR（Pull Request）** 是你向别人的代码仓库提出"请把我的修改合并进来"的请求。

整个流程可以用一句话概括：

> **我在自己的副本里改好了代码 → 推送到我的 GitHub → 告诉原作者"请看一下我的改动，觉得好的话合进来"**

涉及几个关键角色：

| 名词 | 解释 |
|------|------|
| **上游仓库（upstream）** | 原作者的仓库，本例是 `HKUDS/OpenHarness` |
| **Fork** | 你在 GitHub 上复制出来的副本，本例是 `yl-jiang/OpenHarness` |
| **本地仓库** | 你电脑上的代码，通过 `git clone` 从 fork 下载下来 |
| **分支（branch）** | 同一个仓库里的平行时间线，修改在独立分支上进行，不影响主线 |
| **Commit** | 一次存档，记录"此刻代码长什么样"以及"改了什么" |
| **Push** | 把本地的 commit 上传到 GitHub |
| **PR** | 在 GitHub 上发起的合并请求 |

---

## 二、前期准备

### 2.1 Fork 仓库

在浏览器打开 `https://github.com/HKUDS/OpenHarness`，点击右上角 **Fork** 按钮，GitHub 会在你的账号下创建一份副本 `yl-jiang/OpenHarness`。

> 为什么要 Fork？因为你没有直接向 `HKUDS/OpenHarness` 写入的权限。Fork 出来的副本完全属于你，可以随意修改。

### 2.2 Clone 到本地

```bash
git clone https://github.com/yl-jiang/OpenHarness.git
cd OpenHarness
```

Clone 之后，本地的 `origin` 远端就指向你的 fork：

```bash
git remote -v
# origin  https://github.com/yl-jiang/OpenHarness.git (fetch)
# origin  https://github.com/yl-jiang/OpenHarness.git (push)
```

### 2.3 配置 git 身份（重要！）

**git 提交时记录的作者信息来自本机配置，与 GitHub 登录账号无关。** 如果不提前设置，commit 会用机器上别人的身份（比如本次就出现了 `ruiqi.song` 的问题）。

只影响当前仓库：

```bash
git config user.name "yl-jiang"
git config user.email "yvlinchiang@gmail.com"  # GitHub 账号绑定的邮箱
```

验证：

```bash
git config --local --list | grep user
# user.name=yl-jiang
# user.email=yvlinchiang@gmail.com
```

> **记住：每 clone 一个新仓库后，如果全局 git 配置不是你自己的，就要执行一次这个步骤。**

### 2.4 创建独立分支

**永远不要直接在 `main` 分支上改代码。** 应该为每个独立的修复/功能创建一个新分支。

```bash
git checkout -b fix/permission-lock-parallel-modal
# 等价于：先创建分支再切换过去
```

命名约定（非强制，但业界惯例）：
- `fix/xxx`：bug 修复
- `feat/xxx`：新功能
- `docs/xxx`：文档

---

## 三、理解问题与修改代码

### 3.1 本次修复的问题

**背景**：OpenHarness TUI 模式下，AI 有时会在一次回复中调用多个需要用户确认权限的工具（比如同时写两个文件）。这种情况下：

1. Python 后端用 `asyncio.gather` **并发**执行所有工具调用
2. 每个工具都会触发 `_ask_permission()`，各自向前端发一条 `modal_request` 消息
3. 前端收到 `modal_request` 时，直接用 `setModal(...)` 覆盖当前显示的弹窗
4. 结果：只有最后一个弹窗被显示，用户回答后，前面那些工具的 future（异步等待结果）永远没有响应
5. 300 秒超时后，那些工具收到 "Permission denied"——用户明明没有拒绝，但工具仍然失败

**根本原因**：多个 `_ask_permission` 并发运行，没有排队机制。

**修复方案**：加一把 `asyncio.Lock`，让 `_ask_permission` 的调用变成串行——上一个弹窗被用户响应后，下一个才弹出来。

### 3.2 代码改动（`src/openharness/ui/backend_host.py`）

**改动 1**：在 `__init__` 里加一把锁：

```python
# 修改前
self._permission_requests: dict[str, asyncio.Future[bool]] = {}
self._question_requests: dict[str, asyncio.Future[str]] = {}

# 修改后
self._permission_requests: dict[str, asyncio.Future[bool]] = {}
self._question_requests: dict[str, asyncio.Future[str]] = {}
# 串行化并发的权限弹窗，防止前端 modal 被覆盖
self._permission_lock = asyncio.Lock()
```

**改动 2**：在 `_ask_permission` 方法里用这把锁：

```python
# 修改前
async def _ask_permission(self, tool_name: str, reason: str) -> bool:
    request_id = uuid4().hex
    ...

# 修改后
async def _ask_permission(self, tool_name: str, reason: str) -> bool:
    async with self._permission_lock:   # ← 获取锁，其他调用者在此等待
        request_id = uuid4().hex
        ...
```

`async with self._permission_lock` 的作用：同一时刻只有一个 `_ask_permission` 在执行，其他并发调用者会在这里等待，直到锁被释放（即当前弹窗被响应或超时）。

### 3.3 顺手修复的上游 Bug

在测试时发现运行 `oh` 直接崩溃，报错：

```
TypeError: run_backend_host() got an unexpected keyword argument 'permission_mode'
```

这是上游最新 commit（`69c85e4`）引入的 bug：`app.py` 新增了 `permission_mode` 参数并向下传递，但中间的函数签名和数据类都漏掉了。调用链是：

```
app.py → run_backend_host() → BackendHostConfig → build_runtime()
```

每一层都没有 `permission_mode`，需要逐层补上。这与本次 PR 主题相关（都是权限模式相关代码），顺便修掉。

---

## 四、本地验证

**改完代码后，必须在本地跑通检查，再提交。** 这是对维护者的基本尊重，也是大多数开源项目的明确要求（见 `CONTRIBUTING.md`）。

本项目要求运行：

```bash
# 1. 代码风格检查（等价于语法/格式检查）
uv run ruff check src tests scripts
# 输出：All checks passed!  → 通过

# 2. 单元测试（只跑相关模块，速度快）
uv run pytest tests/test_ui/test_react_backend.py -v
# 输出：14 passed  → 通过
```

> **关于全量测试失败**：运行 `uv run pytest -q` 会看到 3 个失败，但经过验证，这 3 个测试在切回 `main` 分支后同样失败——说明是上游已有问题，与本次改动无关，不影响提交。

---

## 五、编写测试

修改了行为，就要新增测试来证明修改是正确的。本次在 `tests/test_ui/test_react_backend.py` 末尾追加了 `test_concurrent_ask_permission_are_serialised`：

- 同时发起两个 `_ask_permission` 协程（模拟并发工具调用）
- 用 resolver task 在 future 注册后立即模拟用户点击"允许"
- 断言两个调用**都**返回 `True`（没有加锁时，其中一个会超时返回 `False`）

---

## 六、提交（Commit）

### 6.1 什么是 commit

commit 是一次"存档"。它记录：
- 哪些文件有哪些变化
- 作者是谁、时间是何时
- 一条描述性的消息（commit message）

### 6.2 暂存改动

```bash
# 先查看改了哪些文件
git status

# 把要提交的文件加入"暂存区"（告诉 git：这些文件的改动要打包进下一个 commit）
git add src/openharness/ui/backend_host.py
git add tests/test_ui/test_react_backend.py
git add CHANGELOG.md

# 确认暂存内容
git diff --cached --stat
```

### 6.3 写好 commit message

好的 commit message 遵循格式：`类型(范围): 一句话摘要`，然后空一行写详细说明。

```bash
git commit -m "fix(ui): serialise concurrent permission modals with a lock

When the LLM returns multiple tool calls in one response, query.py
executes them concurrently via asyncio.gather. If more than one tool
requires user confirmation, each concurrent _ask_permission call emits
its own modal_request event. The React frontend overwrites its modal
state on every modal_request (setModal), so only the last dialog is
ever shown to the user. The earlier futures never receive a response
and time out after 300 s, causing silent \"Permission denied\" errors.

Fix: add _permission_lock (asyncio.Lock) to ReactBackendHost and wrap
_ask_permission with 'async with self._permission_lock'.

Co-authored-by: Copilot"
```

第一行（摘要）遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范：
- `fix`：bug 修复
- `(ui)`：影响范围
- 冒号后面：简短描述，用英文，动词开头

---

## 七、推送（Push）

### 7.1 网络问题：HTTPS 被墙，改用 SSH

直接 `git push` 发现 HTTPS 连接超时。SSH 默认 22 端口也不通。解决方式：让 SSH 走 443 端口（通常不被封锁）。

**第一步**：测试 SSH over 443 是否可用：

```bash
ssh -T -p 443 git@ssh.github.com
# 输出：Hi yl-jiang! You've successfully authenticated...  → 可用
```

**第二步**：在 `~/.ssh/config` 中配置 GitHub 走 443（永久生效）：

```
Host github.com
  Hostname ssh.github.com
  Port 443
  User git
```

**第三步**：将 git remote 从 HTTPS 切换为 SSH：

```bash
git remote set-url origin git@github.com:yl-jiang/OpenHarness.git
```

### 7.2 推送分支

```bash
git push origin fix/permission-lock-parallel-modal
# 输出最后一行：* [new branch] fix/... -> fix/...  → 成功
```

这一步做了什么：把本地这个分支的所有 commit 上传到你 fork 上的同名分支。

---

## 八、创建 PR

### 8.1 用 gh CLI 创建

`gh` 是 GitHub 官方命令行工具，可以直接在终端操作 GitHub。

```bash
gh pr create \
  --repo HKUDS/OpenHarness \          # 目标仓库（上游，不是你的 fork）
  --head yl-jiang:fix/permission-lock-parallel-modal \  # 你的分支
  --base main \                        # 要合并进哪个分支
  --title "fix(ui): serialise concurrent permission modals with a lock" \
  --body "..."                         # PR 描述
```

> **注意 `--repo` 和 `--head` 的区别**：`--repo` 是 PR 要提给谁，`--head` 是你的分支（需要带上你的用户名前缀 `yl-jiang:`）。

创建成功后会输出 PR 的 URL：`https://github.com/HKUDS/OpenHarness/pull/66`

### 8.2 PR 描述写什么

一个好的 PR 描述包含三部分：
1. **Problem**：解释什么问题，为什么会发生
2. **Fix**：你怎么修的
3. **Verification**：你怎么验证它是正确的

---

## 九、关联 Issue

如果你在提 PR 之前（或之后）单独提了一个 Issue 描述这个问题，可以在 PR description 里加上 `Closes #编号`，让两者关联起来。

效果：
- GitHub 侧边栏显示 PR 与 issue 的双向链接
- PR 合并后，对应 issue 自动关闭

操作（在已有 PR 的 description 末尾追加）：

```bash
gh pr edit 66 --repo HKUDS/OpenHarness \
  --body "$(gh pr view 66 --repo HKUDS/OpenHarness --json body -q .body)

Closes #69"
```

本次 PR #66 关联的是 Issue #69。

---

## 十、修正 Commit Author

### 10.1 发现问题

PR 提交后，发现 Commits tab 显示的作者是 `ruiqi.song`，而不是自己的 GitHub 账号。

**原因**：git 的 commit author 取自本机的 `user.name` / `user.email` 配置，与 GitHub 登录无关。这台机器的全局配置是别人的身份。

### 10.2 设置仓库级身份

```bash
# 只影响当前仓库，不改全局
git config user.name "yl-jiang"
git config user.email "yvlinchiang@gmail.com"
```

### 10.3 修改已提交 commit 的作者

commit 已经提交了，但 git 允许"修改历史"（rebase）。`--reset-author` 会用当前配置的 user 重新设置 author。

```bash
# 确保在 PR 分支上（不是 main！）
git checkout fix/permission-lock-parallel-modal

# 对最近 2 个 commit 执行 amend（HEAD~2 = 往前数 2 个）
git rebase HEAD~2 --exec 'git commit --amend --reset-author --no-edit'
```

### 10.4 Force Push

修改了历史，远端的 commit hash 和本地不一样了，普通 push 会被拒绝，需要强制推送：

```bash
git push origin fix/permission-lock-parallel-modal --force-with-lease
```

> `--force-with-lease` 比 `--force` 更安全：如果远端有你本地没有的新提交，它会拒绝推送，避免意外覆盖别人的工作。

### 10.5 Force Push 之后，PR 会自动更新吗？

**会，而且是自动的。** 原因在于：**PR 追踪的是"分支名"，而不是"某个具体的 commit hash"。**

GitHub 上 PR #66 的定义是：

> 把 `yl-jiang/OpenHarness` 的 `fix/permission-lock-parallel-modal` 分支，合并进 `HKUDS/OpenHarness` 的 `main`

它只记住了分支名。force push 之后，这个分支指向了新的 commit（hash 变了，但代码内容和意图完全一样），GitHub 重新读取该分支的最新状态，PR 页面自动刷新——显示新的 commit、新的 diff。

用一个比喻理解：

> PR 就像一张快递单，上面写的是"从 A 地址取货，送到 B 地址"。  
> 你在 A 地址把箱子重新打包了（force push），快递单上的地址没变，快递员下次来还是去 A 地址取——取到的自然是新包装的货。

| | 追踪分支（GitHub PR 的做法）| 追踪具体 commit |
|--|--|--|
| force push 后 | PR 自动更新，显示新内容 ✓ | PR 断开，找不到旧 hash ✗ |
| 新增 commit 后 | PR 自动包含新 commit ✓ | 不变 |

**实际意义**：PR review 期间，维护者如果要求你修改代码，只需在同一分支上继续 commit + push，PR 会自动更新，不需要关掉旧 PR 再开新的。

### 10.5 踩坑记录

第一次执行 `git rebase HEAD~2` 时忘记确认当前分支，在 `main` 上误操作了，`main` 的历史被改乱。

恢复方法：

```bash
git checkout main
git reset --hard origin/main   # 强制重置到远端的状态，本地改动全部丢弃
```

**教训：执行任何 rebase / reset 操作前，先 `git branch` 确认自己在哪个分支。**

---

## 十一、最终状态

| 项 | 值 |
|----|-----|
| PR | https://github.com/HKUDS/OpenHarness/pull/66 |
| Issue | https://github.com/HKUDS/OpenHarness/issues/69 |
| 分支 | `yl-jiang:fix/permission-lock-parallel-modal` → `HKUDS/OpenHarness:main` |
| Commit 1 | `55f7f8e` — 加锁串行化并发 modal |
| Commit 2 | `41f7998` — 补全 permission_mode 调用链 |
| Commit Author | `yl-jiang <yvlinchiang@gmail.com>` |
| 状态 | Open，等待维护者 review |

---

## 十二、完整流程速查

```
1. GitHub 上 Fork 上游仓库
2. git clone 你的 fork 到本地
3. git config user.name / user.email  ← 设置身份
4. git checkout -b fix/xxx            ← 创建新分支
5. 修改代码
6. 编写/更新测试
7. uv run ruff check ...              ← lint
8. uv run pytest ...                  ← 测试
9. git add + git commit               ← 存档
10. git push origin fix/xxx           ← 推送到 fork
11. gh pr create --repo 上游 ...      ← 创建 PR
12. gh pr edit ... Closes #Issue      ← 关联 issue（可选）
```
