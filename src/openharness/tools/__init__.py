"""Built-in tool registration."""

from openharness.tools.ask_user_question_tool import AskUserQuestionTool
from openharness.tools.agent_tool import AgentTool
from openharness.tools.bash_tool import BashTool
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.brief_tool import BriefTool
from openharness.tools.config_tool import ConfigTool
from openharness.tools.done_tool import DoneTool
from openharness.tools.cron_manager_tool import CronManagerTool
from openharness.tools.enter_worktree_tool import EnterWorktreeTool
from openharness.tools.exit_worktree_tool import ExitWorktreeTool
from openharness.tools.file_edit_tool import FileEditTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.file_write_tool import FileWriteTool
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool
from openharness.tools.image_to_text_tool import ImageToTextTool
from openharness.tools.list_mcp_resources_tool import ListMcpResourcesTool
from openharness.tools.lsp_tool import LspTool
from openharness.tools.mcp_auth_tool import McpAuthTool
from openharness.tools.mcp_tool import McpToolAdapter
from openharness.tools.memory_tool import MemoryTool
from openharness.tools.notebook_edit_tool import NotebookEditTool
from openharness.tools.plan_mode_tool import PlanModeTool
from openharness.tools.read_mcp_resource_tool import ReadMcpResourceTool
from openharness.tools.remote_trigger_tool import RemoteTriggerTool
from openharness.tools.send_message_tool import SendMessageTool
from openharness.tools.skill_manager_tool import SkillManagerTool
from openharness.tools.sleep_tool import SleepTool
from openharness.tools.task_create_tool import TaskCreateTool
from openharness.tools.task_get_tool import TaskGetTool
from openharness.tools.task_list_tool import TaskListTool
from openharness.tools.task_output_tool import TaskOutputTool
from openharness.tools.task_stop_tool import TaskStopTool
from openharness.tools.task_update_tool import TaskUpdateTool
from openharness.tools.task_wait_tool import TaskWaitTool
from openharness.tools.team_create_tool import TeamCreateTool
from openharness.tools.team_delete_tool import TeamDeleteTool
from openharness.tools.todo_tool import TodoTool
from openharness.tools.tool_search_tool import ToolSearchTool
from openharness.tools.web_fetch_tool import WebFetchTool
from openharness.tools.web_search_tool import WebSearchTool


def create_default_tool_registry(mcp_manager=None) -> ToolRegistry:
    """Return the default built-in tool registry."""
    registry = ToolRegistry()
    for tool in (
        BashTool(),
        AskUserQuestionTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        NotebookEditTool(),
        LspTool(),
        McpAuthTool(),
        GlobTool(),
        GrepTool(),
        ImageToTextTool(),
        SkillManagerTool(),
        ToolSearchTool(),
        WebFetchTool(),
        WebSearchTool(),
        MemoryTool(),
        ConfigTool(),
        BriefTool(),
        SleepTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        TodoTool(),
        PlanModeTool(),
        CronManagerTool(),
        RemoteTriggerTool(),
        TaskCreateTool(),
        TaskGetTool(),
        TaskListTool(),
        TaskStopTool(),
        TaskOutputTool(),
        TaskUpdateTool(),
        TaskWaitTool(),
        AgentTool(),
        SendMessageTool(),
        TeamCreateTool(),
        TeamDeleteTool(),
    ):
        registry.register(tool)
    if mcp_manager is not None:
        registry.register(ListMcpResourcesTool(mcp_manager))
        registry.register(ReadMcpResourceTool(mcp_manager))
        for tool_info in mcp_manager.list_tools():
            registry.register(McpToolAdapter(mcp_manager, tool_info))
    return registry


__all__ = [
    "BaseTool",
    "DoneTool",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
]
