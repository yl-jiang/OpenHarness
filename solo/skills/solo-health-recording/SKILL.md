---
name: solo-health-recording
description: solo 健康记录细则，包括 category 选择、subject 识别、隐含健康信息扫描和生理期记录规范
version: 0.1.0
tags: solo, health, recording, period, symptom
triggers:
  - 症状
  - 用药
  - 运动
  - 睡眠
  - 饮食
  - 心理状态
  - 体检
  - 体征数据
---

# 健康记录提取原则

用户的日常记录中经常包含**健康相关信息**——症状、用药、运动、睡眠、饮食、心理变化等。
这些信息需要写入专用的 `health_records` 表，以便后续统计和趋势分析。

## 判断标准

这条信息是否与身体健康直接相关？
问自己：**如果用户未来想回顾自己的健康历史，这条信息是否有参考价值？** 如果是，调用 `solo_health_record`。

## 隐含健康信息识别（重要）

用户的日常记录中经常**顺嘴提到**自己或家人的健康信息，这些信息虽然不是记录的主角，但同样需要识别并记录到 health_records 表。

**识别要点：**
- 不要只看记录的主题/主要内容，要**逐句扫描**是否有健康相关的附带信息
- 特别注意"**因为…所以…**"、"**没有去，因为…**"、"**顺便…**"等因果/附带结构
- 即使整条记录的主题是游乐场/购物/工作，其中提到的体检、看病、吃药等仍需提取

**典型隐含场景：**

| 用户说的（日常记录） | 隐含的健康信息 | 提取结果 |
|---------------------|---------------|----------|
| "今天带小明去游乐场玩了，小红没去，因为她去体检了" | 小红去体检 | subject=小红, category=medical, item=体检 |
| "周末陪爸妈去了一趟医院，老爸做了个胃镜" | 老爸做胃镜 | subject=老爸, category=medical, item=胃镜 |
| "今天加班到很晚，吃了颗维生素C" | 吃维生素C | subject=self, category=medication, item=维生素C |
| "小明在幼儿园被小朋友传染了，开始流鼻涕" | 小明流鼻涕 | subject=小明, category=symptom, item=流鼻涕 |
| "下午开会头疼，喝了杯咖啡就好了" | 头疼 | subject=self, category=symptom, item=头疼 |
| "小红今天状态不太好，大姨妈来了" | 小红生理期 | subject=**小红**, category=period, item=生理期开始, severity=mild, metrics_json={"flow":"mild","cycle_day":1} |

## 类别选择（category）

优先使用以下推荐类别，仅在明确不属于任何推荐类别时才创建新类别（单个英文小写单词）：

| category | 适用场景 |
|----------|----------|
| `medical` | 医院就诊、体检、复查、手术、诊断 |
| `symptom` | 头疼、鼻炎、感冒、过敏、疼痛、疲劳 |
| `medication` | 服药、处方、保健品、疫苗接种 |
| `fitness` | 跑步、游泳、骑行、力量训练、瑜伽 |
| `sleep` | 入睡时间、睡眠时长、睡眠质量、失眠 |
| `nutrition` | 饮食习惯、节食、营养补充、戒糖 |
| `mental` | 情绪波动、压力、焦虑、抑郁、冥想 |
| `vital` | 体重、心率、血压、血氧、体温等量化指标 |
| `period` | 生理期 / 月经 / 经期 / 大姨妈 / 例假（按天记录，每条一天） |

**约束**：新类别必须是单个英文小写单词（如 `dental`），禁止使用 `other`、`misc` 等模糊名称。

## 典型场景

| 用户说的 | subject | category | item | 关键参数 |
|---------|---------|----------|------|---------|
| "今天跑了5公里" | self | fitness | 跑步 | exercise_type=跑步, exercise_duration_min≈30 |
| "头疼了一整天" | self | symptom | 头疼 | body_part=头, severity=moderate |
| "吃了布洛芬止痛" | self | medication | 布洛芬 | medication_name=布洛芬, dosage=1颗 |
| "昨晚睡了8小时，质量不错" | self | sleep | 睡眠 | sleep_hours=8, sleep_quality=good |
| "带小明去新华医院做发育评估" | **小明** | medical | 发育评估 | description=评估结果 |
| "小明鼻炎又犯了" | **小明** | symptom | 过敏性鼻炎 | body_part=鼻, severity=mild |
| "小红今天来大姨妈了" | **小红** | period | 生理期开始 | severity=mild, metrics_json={"flow":"mild","cycle_day":1} |
| "老婆这次经期 6.19 到 6.23" | **小红**（用户的配偶） | period | 生理期 | 拆成 5 条记录：每天一条，severity/cycle_day 按开始/中段/结束区分 |

**subject 判断规则：**
- 默认 `self`（用户自己）
- 当消息明确提到家庭成员（如"小明"、"小红"、"老婆/妻子/太太"）的健康事件时，设为对应名称。**"老婆/妻子/太太"指用户的配偶，subject 必须用配偶的真实姓名（系统中已有的 subject 值），不要直接把"老婆"当作 subject**
- 特别注意儿童健康记录：用户经常记录子女的就诊、症状、用药，必须正确识别 subject
- **特别注意生理期记录**：几乎总是关于用户的配偶，subject 必须是配偶的真实姓名，不要用 `self`

## 操作要求

1. 每次调用 `solo_record` 时，同步检查消息是否包含健康信息。若有，**同一轮**调用 `solo_health_record`
2. **逐句扫描整条消息**，不要只看主题。健康信息可能作为附带信息出现
3. 一条消息可能包含**多种健康事件**（如运动+用药），此时**分别调用**多次 `solo_health_record`
4. `item` 使用中文，简明扼要
5. 只填与 category 相关的字段，不要填无关字段
6. `metrics_json` 仅用于无法被其他字段覆盖的量化数据（体重、心率、步数等）
7. **`subject` 必须正确识别**：默认 `self`，但当健康事件是关于家庭成员时，必须设为对应名称
8. **稳定的健康事实**（如"我有过敏性鼻炎"）仍然用 `solo_remember`，`solo_health_record` 记录的是**事件级别**的健康信息（如"今天鼻炎发作了"）
9. 如果用户只是泛泛提到健康但不包含具体事件（如"要注意健康了"），不需要调用

## 不提取的情况

- 纯粹是工作计划中的运动安排描述，不含实际执行
- 已经通过 solo_remember 存入的稳定健康事实
- 无法提取出具体 item 的模糊表述
