---
name: solo-period-tracking
description: solo 生理期记录操作细则，包括按天拆分、字段规范、item 设置和 subject 识别
version: 0.1.0
tags: solo, health, period, menstrual, tracking
triggers:
  - 生理期
  - 月经
  - 经期
  - 大姨妈
  - 例假
---

# 生理期记录操作细则

生理期是**按天拆分**的健康事件，每条记录对应一天。前端用这些记录算周期长度和经期天数，**必须每天一条**，不能一条记录覆盖多天。

## 字段填写规范

| 字段 | 值 |
|------|-----|
| `subject` | **配偶的真实姓名**（不要用 `self`，也不要把"老婆"当 subject） |
| `category` | `period` |
| `item` | 第一天：`生理期开始`；中间天：`生理期`；最后一天：`生理期结束` |
| `severity` | 第一天/最后一天：`mild` 或 `light`；中间天：`moderate` |
| `metrics_json` | `{"flow": "<mild\|moderate\|light>", "cycle_day": <从1开始的天数>}` |
| `date` | 该天的 YYYY-MM-DD |

## 典型消息处理

- 用户说"老婆今天来大姨妈了" → 一条记录：date=今天, item=生理期开始, severity=mild, cycle_day=1（subject 设为配偶真实姓名）
- 用户说"老婆经期结束了" → 一条记录：date=今天, item=生理期结束, severity=light, cycle_day=N（N=距离本次开始的天数）
- 用户说"小红这次经期 6.19 到 6.23" → **拆成 5 条记录**，每天一条，分别设置 item 为 生理期开始/生理期/生理期/生理期/生理期结束，cycle_day 从 1 递增到 5
- 用户只说"老婆 6.19 来月经"但没给结束日期 → 只写第一天那条，结束日期留待后续消息补

## 不要做的事

- 不要把生理期记录成 `category=symptom` 或 `category=medical`——前端周期追踪只识别 `category=period`
- 不要用一条记录覆盖多天（`duration` 字段不要用在这里）
- 不要在 subject 里写"老婆"——前端按数据库中已有的 subject 字面值筛选，必须用配偶的真实姓名
