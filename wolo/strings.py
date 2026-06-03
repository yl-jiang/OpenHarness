"""Domain-specific string constants for wolo."""

from __future__ import annotations

# --- Runner ---

FALLBACK_MESSAGE = "这里是 wolo 工作记录专用 bot，请发送想要记录的工作内容。"

TOOL_LABELS: dict[str, str] = {
    "record": "📝 记录内容",
    "import_records": "📝 批量记录",
    "backfill": "📝 补录记录",
    "view": "📖 浏览记录",
    "search": "🔍 搜索记录",
    "work_query": "🔍 综合回顾",
    "decisions": "🧭 查看决策",
    "highlights": "✨ 查看高光",
    "blockers": "🚧 查看阻塞",
    "playbook": "📘 查看打法",
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
    "suggest_reflection": "💡 复盘建议",
    "experiments": "🧪 查看实验",
    "patterns": "🧩 查看模式",
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
    "project": "项目",
    "format": "格式",
    "kind": "类型",
    "name": "名称",
}

PASSTHROUGH_TOOLS: frozenset[str] = frozenset({"wolo_report", "wolo_visualize"})

# --- Processor ---

PENDING_REMINDER_TMPL = "还有 {count} 条待确认 wolo 工作记录需要你确认。"
MISSING_DAY_REMINDER_TMPL = "你已经连续 {streak} 天没有 wolo 工作记录，要不要补一下？"

# --- Commands ---

HELP_TEXT = (
    "wolo 用法：\n"
    "- 直接发送工作记录：自动入库并由模型整理\n"
    "- /wolo process：整理待处理记录\n"
    "- /wolo view [数量]：查看最近记录\n"
    "- /wolo report weekly|monthly|yearly：生成报告\n"
    "- 询问待办/blocker/决策/prompt 或 tool 经验：查询工作 artifacts\n"
    "- /wolo status：查看状态\n"
    "- /wolo llm-usage：查看模型调用统计\n"
    "- /wolo backfill [YYYY-MM-DD] 内容：补录"
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
    "blocker": "Blocker：{msg}",
    "cron_failed": "⚠️ 定时任务失败：{msg}",
    "scheduler_stopped": "⚠️ 定时任务调度器已停止运行，提醒和定时任务将不会执行。",
}

HEARTBEAT_SIGNAL_HEADERS: dict[str, str] = {
    "overdue_todos": "【逾期/今日到期 Todo】",
    "pending_records": "【待确认记录（>24h）】",
    "active_blockers": "【活跃 Blocker】",
    "failed_cron": "【失败的定时任务】",
    "scheduler_stopped": "【系统异常】定时任务调度器已停止运行",
    "heartbeat_md": "【HEARTBEAT.md 周期性任务】",
}

# --- Todo reminder labels ---

TODO_REMINDER_NO_TODOS = "✅ 没有需要提醒的工作待办。"
TODO_REMINDER_HEADER = "📋 **Wolo 工作待办提醒**"
TODO_REMINDER_OVERDUE = "⚠️ 已逾期"
TODO_REMINDER_TODAY = "🔔 今日到期"
TODO_REMINDER_UPCOMING = "📅 即将到期"
