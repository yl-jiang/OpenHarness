"""Domain-specific string constants for solo."""

from __future__ import annotations

# --- Runner ---

FALLBACK_MESSAGE = "这里是 solo 记录专用 bot，请发送想要记录的内容。"

TOOL_LABELS: dict[str, str] = {
    "record": "📝 记录内容",
    "import_records": "📝 批量记录",
    "backfill": "📝 补录记录",
    "view": "📖 浏览记录",
    "search": "🔍 搜索记录",
    "work_query": "🔍 综合回顾",
    "show": "🖼️ 查看来源",
    "status": "📊 查看状态",
    "llm_usage": "🤖 模型调用统计",
    "get_now": "🕐 查询时间",
    "remind": "⏰ 设置提醒",
    "schedule": "📅 定时任务",
    "heartbeat_task": "🔁 周期任务",
    "jobs": "📋 查看任务",
    "cancel": "🚫 取消任务",
    "report": "📑 生成报告",
    "report_list": "📑 报告列表",
    "report_show": "📑 查看报告",
    "report_search": "📑 搜索报告",
    "fetch_digest": "📡 获取资讯简报",
    "export": "📤 导出记录",
    "visualize": "📈 生成可视化",
    "process": "⚙️ 整理记录",
    "sync_context": "🔄 同步上下文",
    "todos": "✅ 待办清单",
    "done": "✅ 完成待办",
    "update_todo": "✏️ 更新待办",
    "update_record": "✏️ 更新记录",
    "delete_record": "🗑️ 删除记录",
    "clarify": "💬 请你补充",
    "remember": "🧠 记入长期记忆",
    "profile_update": "🪪 更新资料",
    "experiments": "🧪 查看实验",
    "patterns": "🧩 查看模式",
    "rulebook": "📒 查看规则",
}

ARG_LABELS: dict[str, str] = {
    "content": "内容",
    "corrected_content": "整理后内容",
    "summary": "摘要",
    "query": "关键词",
    "keyword": "关键词",
    "domain": "领域",
    "date": "日期",
    "tags": "标签",
    "tag": "标签",
    "emotion": "情绪",
    "status": "状态",
    "limit": "数量",
    "report_type": "报告类型",
    "message": "提醒内容",
    "task": "任务内容",
    "delay_minutes": "延迟(分钟)",
    "when": "时间",
    "cron": "周期",
    "interval_minutes": "间隔(分钟)",
    "job_name": "任务名",
    "title": "标题",
    "todo_id": "待办",
    "record_id": "记录",
    "period": "时段",
    "format": "格式",
    "kind": "类型",
    "name": "名称",
}

PASSTHROUGH_TOOLS: frozenset[str] = frozenset({"solo_report", "solo_visualize"})

# --- Processor ---

PENDING_REMINDER_TMPL = "还有 {count} 条待确认 solo 需要你确认。"
MISSING_DAY_REMINDER_TMPL = "你已经连续 {streak} 天没有 solo 记录，要不要补一下？"

# --- Commands ---

HELP_TEXT = (
    "solo 可以把你的日常输入整理成可回顾的个人记录。\n"
    "最省事的用法：直接发一句话、一段流水账，或贴一段旧记录，我会自动入库并整理。\n"
    "常用命令：\n"
    "- /solo process：整理待处理记录\n"
    "- /solo view [数量]：查看最近记录，例如 /solo view 20\n"
    "- /solo report weekly|monthly|yearly：生成周/月/年回顾\n"
    "- /solo status：查看记录与待确认状态\n"
    "- /solo llm-usage：查看模型调用统计\n"
    "- /solo backfill [YYYY-MM-DD] 内容：补录过去的记录"
)

COMMAND_ALIASES: dict[str, set[str]] = {
    "help": {"help", "-h", "--help", "帮助"},
    "process": {"process", "整理"},
    "status": {"status", "状态"},
    "llm_usage": {"llm-usage", "llm_usage", "llm", "models", "模型", "模型调用"},
    "view": {"view", "list", "recent", "查看", "最近"},
    "report": {"report", "周报", "月报", "年报"},
    "backfill": {"backfill", "补录"},
}

# --- Heartbeat notification labels ---

NOTIFICATION_LABELS: dict[str, str] = {
    "pending": "待确认：{msg}",
    "todo": "Todo 提醒：{msg}",
    "cron_failed": "⚠️ 定时任务失败：{msg}",
    "scheduler_stopped": "⚠️ 定时任务调度器已停止运行，提醒和定时任务将不会执行。",
}

HEARTBEAT_SIGNAL_HEADERS: dict[str, str] = {
    "overdue_todos": "【逾期/今日到期 Todo】",
    "pending_records": "【待确认记录（>24h）】",
    "failed_cron": "【失败的定时任务】",
    "scheduler_stopped": "【系统异常】定时任务调度器已停止运行",
    "heartbeat_md": "【HEARTBEAT.md 周期性任务】",
}

# --- Todo reminder labels ---

TODO_REMINDER_NO_TODOS = "✅ 没有需要提醒的待办事项。"
TODO_REMINDER_HEADER = "📋 **Solo 待办提醒**"
TODO_REMINDER_OVERDUE = "⚠️ 已逾期"
TODO_REMINDER_TODAY = "🔔 今日到期"
TODO_REMINDER_UPCOMING = "📅 即将到期"
