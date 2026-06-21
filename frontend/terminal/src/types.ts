export type FrontendConfig = {
	backend_command: string[];
	initial_prompt?: string | null;
	theme?: string;
	version?: string | null;
};

export type TranscriptItem = {
	role: 'system' | 'user' | 'user_shell' | 'assistant' | 'tool' | 'tool_result' | 'log' | 'status';
	text: string;
	tool_name?: string;
	tool_input?: Record<string, unknown>;
	is_error?: boolean;
	/** Captured reasoning text for completed assistant turns. */
	reasoning?: string;
};

export type ImageAttachmentPayload = {
	media_type: string;
	data: string;
	source_path?: string;
};

export type TaskSnapshot = {
	id: string;
	type: string;
	status: string;
	description: string;
	started_at?: number | null;
	metadata: Record<string, string>;
};

export type McpServerSnapshot = {
	name: string;
	state: string;
	detail?: string;
	transport?: string;
	auth_configured?: boolean;
	tool_count?: number;
	resource_count?: number;
};

export type BridgeSessionSnapshot = {
	session_id: string;
	command: string;
	cwd: string;
	pid: number;
	status: string;
	started_at: number;
	output_path: string;
};

export type SelectOptionPayload = {
	value: string;
	label: string;
	description?: string;
	active?: boolean;
	badge?: string;
	badgeTone?: 'accent' | 'warning' | 'muted';
};

export type TodoItemSnapshot = {
	text: string;
	checked: boolean;
};

export type SwarmTeammateSnapshot = {
	name: string;
	status: 'running' | 'idle' | 'done' | 'error';
	duration?: number;
	task?: string;
};

export type SwarmNotificationSnapshot = {
	from: string;
	message: string;
	timestamp: number;
};

export type GoalChangeStats = {
	turns_used: number;
	tokens_used: number;
	wall_clock_ms: number;
};

export type GoalChange = {
	kind: 'lifecycle' | 'completion';
	status?: string | null;
	reason?: string | null;
	actor?: string | null;
	stats?: GoalChangeStats | null;
};

export type GoalSnapshot = {
	goal_id: string;
	objective: string;
	completion_criterion?: string | null;
	status: string;
	turns_used: number;
	tokens_used: number;
	wall_clock_ms: number;
	terminal_reason?: string | null;
	last_actor?: string | null;
	budget: {
		turn_budget?: number | null;
		token_budget?: number | null;
		wall_clock_budget_ms?: number | null;
		remaining_turns?: number | null;
		remaining_tokens?: number | null;
		remaining_wall_clock_ms?: number | null;
		turn_budget_reached: boolean;
		token_budget_reached: boolean;
		wall_clock_budget_reached: boolean;
		over_budget: boolean;
		usage_fraction: number;
	};
};

export type BackendEventType =
	| 'ready'
	| 'state_snapshot'
	| 'tasks_snapshot'
	| 'transcript_item'
	| 'command_output_start'
	| 'compact_progress'
	| 'assistant_delta'
	| 'reasoning_delta'
	| 'assistant_complete'
	| 'line_complete'
	| 'tool_started'
	| 'tool_completed'
	| 'clear_transcript'
	| 'modal_request'
	| 'select_request'
	| 'todo_update'
	| 'plan_mode_change'
	| 'swarm_status'
	| 'goal_updated'
	| 'error'
	| 'shutdown'
	| 'status';

export type BackendEvent = {
	type: BackendEventType;
	message?: string | null;
	item?: TranscriptItem | null;
	state?: Record<string, unknown> | null;
	tasks?: TaskSnapshot[] | null;
	mcp_servers?: McpServerSnapshot[] | null;
	bridge_sessions?: BridgeSessionSnapshot[] | null;
	commands?: string[] | null;
	skills?: string[] | null;
	modal?: Record<string, unknown> | null;
	select_options?: SelectOptionPayload[] | null;
	tool_name?: string | null;
	output?: string | null;
	is_error?: boolean | null;
	compact_phase?: string | null;
	compact_trigger?: string | null;
	attempt?: number | null;
	compact_checkpoint?: string | null;
	compact_metadata?: Record<string, unknown> | null;
	// New event payloads
	todo_items?: TodoItemSnapshot[] | null;
	todo_markdown?: string | null;
	plan_mode?: string | null;
	swarm_teammates?: SwarmTeammateSnapshot[] | null;
	swarm_notifications?: SwarmNotificationSnapshot[] | null;
	goal_snapshot?: GoalSnapshot | null;
	goal_change?: GoalChange | null;
	// Terminal-state reason for line_complete events
	reason?: string | null;
};
