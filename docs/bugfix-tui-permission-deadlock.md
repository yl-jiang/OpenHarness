# Bug Report & Fix: TUI 模式下权限确认失效（Permission Denied）

## 概述

**问题描述**：在 React TUI 模式下，AI Agent 请求执行写文件（`write_file`）、运行命令（`bash`）等需要权限确认的工具时，即使用户在终端界面按下 `y` 确认，工具仍然返回 "Permission denied"，无法执行。

**影响版本**：所有包含 `ReactBackendHost` 的版本  
**影响范围**：所有权限模式为 `default` 的 TUI 会话（`full_auto` 模式不受影响）  
**严重程度**：高 — 导致 TUI 模式下所有需要确认的写操作完全失效  
**修复文件**：`src/openharness/ui/backend_host.py`

---

## 调查过程

### 1. 问题现象还原

用户报告的日志：

```
Permission denied for write_file
⏺ bash mkdir -p /Users/yulin/Github/OpenHarness/docs → Permission denied for bash
⏺ write_file path=oh-vs-ohmo.md → Permission denied for write_file
```

特点：用户**已经输入了 `y`**，但权限仍然被拒绝。

### 2. 系统架构梳理

OpenHarness 的 TUI 模式采用**前后端分离架构**：

```
┌──────────────────────────────────────────────────────────┐
│  React 前端（Ink/Node.js）                                 │
│  frontend/terminal/src/                                   │
│  - App.tsx           ← 键盘输入处理、modal 渲染            │
│  - ModalHost.tsx     ← 权限确认对话框 UI                   │
│  - useBackendSession.ts ← WebSocket/IPC 通信              │
└───────────────────────┬──────────────────────────────────┘
                        │  stdin/stdout JSON-Lines 协议
                        │  前端 → 后端: {"type":"permission_response",...}
                        │  后端 → 前端: {"type":"modal_request",...}
┌───────────────────────┴──────────────────────────────────┐
│  Python 后端                                              │
│  src/openharness/ui/backend_host.py                      │
│  - ReactBackendHost  ← 协议驱动主循环                     │
│  - _read_requests()  ← 独立 Task，持续读取 stdin          │
│  - _ask_permission() ← 向前端发送权限请求，await future   │
│                                                           │
│  src/openharness/engine/query.py                         │
│  - _execute_tool()   ← 工具执行 + 权限检查               │
│                                                           │
│  src/openharness/permissions/checker.py                  │
│  - PermissionChecker.evaluate() ← 返回 requires_confirmation│
└──────────────────────────────────────────────────────────┘
```

通信协议为 `OHJSON:` 前缀的 JSON-Lines，通过进程 stdin/stdout 传输。

### 3. 权限检查流程追踪

**Step 1：工具触发权限检查**

`engine/query.py` 中 `_execute_tool()` 函数调用 `PermissionChecker.evaluate()`：

```python
# checker.py:142-146
# Default mode: require confirmation for mutating tools
return PermissionDecision(
    allowed=False,
    requires_confirmation=True,          # ← 标记需要用户确认
    reason="Mutating tools require user confirmation in default mode",
)
```

对于非只读工具，在 `default` 模式下会返回 `requires_confirmation=True`。

**Step 2：引擎调用权限确认回调**

```python
# query.py:224-232
if not decision.allowed:
    if decision.requires_confirmation and context.permission_prompt is not None:
        confirmed = await context.permission_prompt(tool_name, decision.reason)
        if not confirmed:
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=f"Permission denied for {tool_name}",
                is_error=True,
            )
```

这里 `permission_prompt` 是 `backend_host.py` 中的 `_ask_permission` 方法。

**Step 3：后端发送权限请求给前端**

```python
# backend_host.py:643-664
async def _ask_permission(self, tool_name: str, reason: str) -> bool:
    request_id = uuid4().hex
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    self._permission_requests[request_id] = future       # 注册 future
    await self._emit(BackendEvent(type="modal_request", modal={
        "kind": "permission",
        "request_id": request_id,
        ...
    }))
    try:
        return await asyncio.wait_for(future, timeout=300)  # ← 等待前端响应
    except asyncio.TimeoutError:
        log.warning("Permission request %s timed out after 300s, denying", request_id)
        return False   # ← 300秒超时后拒绝！
```

**Step 4：前端接收并展示权限对话框**

前端 `useBackendSession.ts` 收到 `modal_request` 事件后设置 `modal` state：

```typescript
// useBackendSession.ts:218-221
if (event.type === 'modal_request') {
    setModal(event.modal ?? null);
    return;
}
```

`App.tsx` 处理用户按键 `y`：

```typescript
// App.tsx:229-238
if (session.modal?.kind === 'permission') {
    if (chunk.toLowerCase() === 'y') {
        session.sendRequest({
            type: 'permission_response',
            request_id: session.modal.request_id,
            allowed: true,
        });
        session.setModal(null);
        return;
    }
```

前端逻辑**完全正确**：用户按 `y` 后，会发送正确格式的 `permission_response` 消息。

**Step 5：后端接收响应（❌ 这里出现了问题）**

原始代码的主循环：

```python
# backend_host.py（修复前）
reader = asyncio.create_task(self._read_requests())  # ← 独立 Task
try:
    while self._running:
        request = await self._request_queue.get()    # ← 主循环在这里等
        ...
        if request.type == "permission_response":    # ← 处理权限响应
            if request.request_id in self._permission_requests:
                self._permission_requests[request.request_id].set_result(...)
            continue
        ...
        # 处理 submit_line
        self._busy = True
        try:
            should_continue = await self._process_line(line)   # ← 主循环卡在这里！
        finally:
            self._busy = False
```

---

### 4. 根因分析：asyncio 死锁

问题的本质是一个**经典 asyncio 并发死锁**：

```
时间线：
T1: 用户输入 "mkdir -p docs"
T2: 主循环 get() 出队 submit_line
T3: 主循环 await self._process_line(line)  ← 主循环挂起在此
T4:   引擎开始执行，调用 bash 工具
T5:   checker.evaluate() 返回 requires_confirmation=True
T6:   引擎 await permission_prompt("bash", reason)
T7:     _ask_permission() 创建 future，发送 modal_request 给前端
T8:     _ask_permission() await asyncio.wait_for(future, 300)  ← future 挂起在此
T9: 前端收到 modal_request，显示 [y/N] 对话框
T10: 用户按 y
T11: 前端发送 permission_response 到 stdin
T12: _read_requests Task 读到 permission_response，放进 _request_queue
T13: ??? 谁来从 _request_queue 取出并 set_result(future)？

→ 主循环还卡在 T3 的 await _process_line()
→ 主循环无法到达 T13 的 _request_queue.get()
→ future 永远不被 resolve
→ 300秒后 asyncio.wait_for 超时，返回 False（拒绝）
→ "Permission denied"
```

**关键矛盾**：`permission_response` 需要由主循环来处理（resolve future），但主循环正在等待工具执行完成——而工具执行又在等待 `permission_response`，形成循环依赖。

```
主循环 ──await──→ _process_line
                   └──await──→ permission_prompt
                                └──await──→ future
                                            ↑
                                            │ set_result (需要主循环来做)
                                            │
                                    _request_queue.get()  ← 主循环被卡，永远到不了这里
```

---

## 解决方案

### 核心思路

将 `permission_response` 和 `question_response` 的处理**从主循环移到 `_read_requests` Task** 中直接处理。`_read_requests` 是一个独立运行的 asyncio Task，不受主循环阻塞影响，可以在任意时刻 resolve future。

### 修改内容

**文件**：`src/openharness/ui/backend_host.py`

**修改 `_read_requests` 方法**，在读取到 `permission_response`/`question_response` 时直接 resolve future，而不是放进请求队列：

```python
# 修复后
async def _read_requests(self) -> None:
    while True:
        raw = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not raw:
            await self._request_queue.put(FrontendRequest(type="shutdown"))
            return
        payload = raw.decode("utf-8").strip()
        if not payload:
            continue
        try:
            request = FrontendRequest.model_validate_json(payload)
        except Exception as exc:
            await self._emit(BackendEvent(type="error", message=f"Invalid request: {exc}"))
            continue
        # Resolve permission/question futures directly here instead of
        # routing through the request queue.  The main loop is blocked on
        # ``await _process_line()`` while the engine awaits the future, so
        # the queue consumer can never dequeue the response — a deadlock.
        if request.type == "permission_response":
            rid = request.request_id
            if rid and rid in self._permission_requests:
                self._permission_requests[rid].set_result(bool(request.allowed))
            continue
        if request.type == "question_response":
            rid = request.request_id
            if rid and rid in self._question_requests:
                self._question_requests[rid].set_result(request.answer or "")
            continue
        await self._request_queue.put(request)
```

**同时更新主循环**，保留防御性处理代码（异常情况 fallback）：

```python
# 主循环中的处理变为防御性 fallback（正常流程不会走到这里）
# NOTE: permission_response and question_response are now
# resolved directly in _read_requests to avoid a deadlock.
if request.type in ("permission_response", "question_response"):
    # Defensive fallback — should not happen in normal flow.
    if request.type == "permission_response" and request.request_id in self._permission_requests:
        self._permission_requests[request.request_id].set_result(bool(request.allowed))
    elif request.type == "question_response" and request.request_id in self._question_requests:
        self._question_requests[request.request_id].set_result(request.answer or "")
    continue
```

### 修复后的执行流程

```
T1-T11: 同上（用户按 y，前端发送 permission_response）
T12: _read_requests Task 读到 permission_response
T13: _read_requests 直接调用 future.set_result(True)  ← 关键修复！
T14: _ask_permission() 的 await future 得到解除，返回 True
T15: 引擎收到确认，继续执行工具
T16: bash 命令成功执行
```

`_read_requests` 与 `_ask_permission` 的 future 共享同一个 `_permission_requests` 字典，通过 `request_id` 关联，无需任何锁（asyncio 单线程事件循环保证字典操作的原子性）。

---

## 关联问题：并行工具调用时的 Modal 覆盖

前端调查代理在分析过程中发现了另一个潜在的权限失效场景，与上述死锁问题相互关联。

### 问题描述

当 LLM 在一次响应中返回**多个工具调用**时，`query.py` 通过 `asyncio.gather` 并发执行：

```python
# query.py:159-162
async def _run(tc):
    return await _execute_tool_call(context, tc.name, tc.id, tc.input)
results = await asyncio.gather(*[_run(tc) for tc in tool_calls])
```

如果多个工具都需要权限确认，每个都会独立调用 `_ask_permission()`，向前端并发发送多个 `modal_request`。而前端只会保留最后一个：

```typescript
// useBackendSession.ts:218-221
if (event.type === 'modal_request') {
    setModal(event.modal ?? null);  // 直接覆盖，没有队列！
    return;
}
```

结果：前一个工具的 `modal_request` 被覆盖，其对应的 Future 永远得不到响应，300 秒后超时，返回 "Permission denied"。

### 代码中已有的防御机制

`backend_host.py` 的 `__init__` 中已经存在 `_permission_lock`，`_ask_permission` 也已正确使用它：

```python
# backend_host.py __init__
# Serialize permission prompts so concurrent tool calls don't
# overwrite each other's modal in the frontend.
self._permission_lock = asyncio.Lock()

# _ask_permission()
async with self._permission_lock:   # ← 串行化，确保一次只显示一个 modal
    ...
    return await asyncio.wait_for(future, timeout=300)
```

这把锁保证了并发的 `_ask_permission` 调用会**排队等待**，避免了 modal 覆盖。

### 两个 Bug 的关系

然而，在死锁修复之前，`_permission_lock` 实际上**形同虚设**：

```
死锁修复前的执行路径（即使有锁也无效）：
  Tool A  → 获得 _permission_lock → 发送 modal_request → await future (300s 超时)
  Tool B  → 等待 _permission_lock（被 A 持有）
  主循环  → 卡在 await _process_line → 无法处理 permission_response
  结果：A 的 future 永远不被 resolve，300s 后 A 超时释放锁，B 再进去也超时
```

死锁修复后，两个机制协同工作：

```
死锁修复后的执行路径：
  Tool A  → 获得 _permission_lock → 发送 modal_request → await future
  _read_requests Task → 收到 permission_response → 直接 resolve A 的 future ✓
  A 的 future 返回 True → A 执行成功 → 释放 _permission_lock
  Tool B  → 获得 _permission_lock → 发送 modal_request → await future
  ... （正常处理）
```

**结论**：死锁是根本原因，并行工具的保护锁在修复后才能真正发挥作用。

---

## Bug 3：Future 状态竞争导致 `_read_requests` Task 崩溃

### 问题描述

`_read_requests` 是一个独立的 asyncio Task，负责读取所有 stdin 输入。在以下情况下会崩溃：

1. `asyncio.wait_for(future, timeout=300)` 超时 → future 被取消（状态变为 `CANCELLED`）
2. 此时若前端补发了一条迟到的 `permission_response`（网络延迟或重试）
3. `_read_requests` 对已取消的 future 调用 `set_result()` → 抛出 `asyncio.InvalidStateError`
4. **Task 崩溃，所有后续 stdin 读取停止**，会话完全失去响应

同样情形也适用于重复响应（用户快速连按两次 `y`）。

### 修复

在调用 `set_result()` 前检查 `fut.done()`：

```python
if request.type == "permission_response":
    rid = request.request_id
    if rid and rid in self._permission_requests:
        fut = self._permission_requests[rid]
        if not fut.done():                      # ← 关键保护
            fut.set_result(bool(request.allowed))
        else:
            log.debug("Permission future %s already resolved, ignoring duplicate response", rid)
    continue
```

---

## 修复总结

| # | Bug | 触发条件 | 症状 | 修复方式 |
|---|-----|---------|------|---------|
| 1 | **asyncio 死锁** | 任何需要权限确认的工具（default 模式） | 按 `y` 后无效，300秒后返回 Permission denied | `_read_requests` 直接 resolve future，绕过请求队列 |
| 2 | **并行 Modal 覆盖** | LLM 一次返回多个需要权限的工具调用 | 部分工具 Permission denied，其他正常 | `_permission_lock` 串行化 `_ask_permission` 调用 |
| 3 | **Future 状态竞争** | 超时后收到迟到响应，或用户重复按 `y` | 会话完全失去响应 | `fut.done()` 检查保护 `set_result()` 调用 |

三个 Bug 共同提交修复，commit：`0b9f481`

---

## 验证

运行相关测试，全部通过：

```bash
$ python -m pytest tests/test_ui/test_react_backend.py tests/test_permissions/ -x -q
...........................................                              [100%]
43 passed in 0.87s
```

---

## 为什么之前没有被发现

1. **`full_auto` 模式不受影响**：该模式下 `PermissionChecker` 直接返回 `allowed=True`，跳过了整个 permission_prompt 回调，不会触发 future。

2. **CLI 模式不受影响**：非 TUI 的 CLI 模式使用 `prompt_toolkit` 的 `ask_permission()` 直接读取终端输入，不经过 `ReactBackendHost` 的事件队列机制。

3. **症状延迟**：问题表现为 **300秒后才返回 denied**（`asyncio.wait_for` 的超时），在快速测试中可能因为超时时间较长而被忽视，或者测试使用了 full_auto 模式。

---

## 教训与预防

1. **asyncio 中的"生产者-消费者"陷阱**：当消费者（主循环）被某个 await 阻塞时，任何依赖该消费者来 resolve 的 future 都会死锁。应将"响应处理"与"请求处理"解耦，让独立的 Task 直接处理响应。

2. **超时时间过长掩盖问题**：`asyncio.wait_for(future, timeout=300)` 的 300 秒超时虽然是为了防止永久挂起，但也使得死锁症状延迟显现，难以在快速集成测试中捕获。

3. **建议**：对于类似的请求-响应模式，使用更短的超时（如 30s）并增加集成测试，模拟真实的前端响应流程。
