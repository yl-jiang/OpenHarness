---
name: solo-todo-closure
description: solo Todo 闭环原则，识别完成/取消/未来可执行事项并正确维护待办
version: 0.1.0
tags: solo, todo, closure, task, tracking
triggers:
  - Todo 闭环
  - 完成待办
  - 取消待办
  - 未来事项识别
---

# Todo 闭环原则

- 当用户提到 "已做完 X"、"X 搞定了"、"取消 X" 等状态变更，主动调用 solo_todos 查找匹配条目，然后更新状态
- 当用户发送的记录内容中隐含待办（如"明天要去..."、"下周要..."），记录入库后系统会自动提取待办
- 当用户**明确列出**"要做 X / 计划 X / 记得 X / 买 X / 约了 X"等可执行事项时，在用 solo_record 记下内容的**同一轮**，逐条调用 solo_add_todo 入库。每条 todo 一次调用，title 必须具体可执行（不要写成"本周待办"这种概括）；若 solo_record 返回了 record_id，把它作为 record_id 传入，让待办能溯源到原始记录
- 不要把模糊愿望（"想瘦一点"、"以后少熬夜"）当 todo 入库，只入库具体可执行的事项
- 定期提醒逾期或即将到期的待办
