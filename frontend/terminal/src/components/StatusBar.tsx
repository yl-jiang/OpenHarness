import React, {useEffect, useState} from 'react';
import {Box, Text} from 'ink';

import {formatDisplayPath} from '../pathDisplay.js';
import {useTheme} from '../theme/ThemeContext.js';
import type {TaskSnapshot} from '../types.js';

const SEP = ' \u2502 ';
const CWD_MARKER = '>ˍ';
const VISIBLE_STATUS_KEYS = [
	'model',
	'permission_mode',
	'cwd',
	'git_branch',
	'mcp_connected',
	'reviews_completed',
	'input_tokens',
	'output_tokens',
] as const;

const WRITE_TOOLS = new Set([
	'Write', 'Edit', 'MultiEdit', 'NotebookEdit',
	'Bash', 'computer', 'str_replace_editor',
]);
const ACTIVE_TASK_STATUSES = new Set(['pending', 'running']);
const TASK_ACTIVITY_FRAMES = ['◐', '◓', '◑', '◒'];
type StatusBarProps = {
	status: Record<string, unknown>;
	tasks: TaskSnapshot[];
	activeToolName?: string;
	elapsedSeconds?: number | null;
	busy?: boolean;
	showTaskSegment?: boolean;
};

function PlanModeIndicator({
	mode,
	activeToolName,
}: {
	mode: string;
	activeToolName?: string;
}): React.JSX.Element | null {
	const [flash, setFlash] = useState(false);
	const [prevMode, setPrevMode] = useState(mode);

	useEffect(() => {
		if (prevMode === 'plan' && mode !== 'plan' && prevMode !== mode) {
			setFlash(true);
			const timer = setTimeout(() => setFlash(false), 800);
			setPrevMode(mode);
			return () => clearTimeout(timer);
		}
		setPrevMode(mode);
	}, [mode]);

	if (mode !== 'plan' && mode !== 'Plan Mode') {
		if (flash) {
			return (
				<Text color="green" bold>
					{' PLAN MODE OFF '}
				</Text>
			);
		}
		return null;
	}

	const isBlockedTool = activeToolName != null && WRITE_TOOLS.has(activeToolName);

	return (
		<Text>
			<Text color="yellow" bold>{' [PLAN MODE] '}</Text>
			{isBlockedTool ? (
				<Text color="red">{'\uD83D\uDEAB '}{activeToolName} blocked</Text>
			) : null}
		</Text>
	);
}

function StatusBarInner({
	status,
	tasks,
	activeToolName,
	elapsedSeconds,
	busy,
	showTaskSegment = true,
}: StatusBarProps): React.JSX.Element {
	const {theme} = useTheme();
	const model = String(status.model ?? 'unknown');
	const mode = String(status.permission_mode ?? 'default');
	const cwd = formatDisplayPath(status.cwd);
	const gitBranch = typeof status.git_branch === 'string' && status.git_branch ? status.git_branch : null;
	const taskCount = countActiveTasks(tasks);
	const mcpCount = Number(status.mcp_connected ?? 0);
	const reviewsCompleted = Number(status.reviews_completed ?? 0);
	const inputTokens = Number(status.input_tokens ?? 0);
	const outputTokens = Number(status.output_tokens ?? 0);
	const isPlanMode = mode === 'plan' || mode === 'Plan Mode';
	const hasElapsed = elapsedSeconds != null;
	const taskActivity = hasElapsed ? `${TASK_ACTIVITY_FRAMES[elapsedSeconds % TASK_ACTIVITY_FRAMES.length]}  ${formatDuration(elapsedSeconds)}` : null;

	return (
		<Box flexDirection="column" marginTop={1}>
			<Text dimColor>
				<Text color={theme.colors.primary} bold>OpenHarness</Text>
				<Text dimColor>{SEP}</Text>
				<Text dimColor>@ {model}</Text>
				<Text dimColor>{SEP}</Text>
				<Text dimColor>$ {formatNum(inputTokens)} {'\u2193'} {formatNum(outputTokens)} {'\u2191'}</Text>
				{!isPlanMode ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text dimColor>⎇  {mode}</Text>
					</>
				) : null}
				<Text dimColor>{SEP}</Text>
				<Text dimColor>{CWD_MARKER} {cwd}</Text>
				{gitBranch ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text dimColor> {gitBranch}</Text>
					</>
				) : null}
				{showTaskSegment && taskCount > 0 ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text dimColor>{'⚙  '}{taskCount}{taskActivity ? `  ${taskActivity}` : ''}</Text>
					</>
				) : null}
				{reviewsCompleted > 0 ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text dimColor>{'✦  '}{reviewsCompleted} reviewed</Text>
					</>
				) : null}
				{mcpCount > 0 ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text dimColor>{'⊞  '}{mcpCount}</Text>
					</>
				) : null}
				{(taskCount === 0 || !showTaskSegment) && hasElapsed ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text color={busy ? 'cyan' : undefined} dimColor={!busy}>{TASK_ACTIVITY_FRAMES[elapsedSeconds % TASK_ACTIVITY_FRAMES.length]}  {formatDuration(elapsedSeconds)}</Text>
					</>
				) : null}
			</Text>
			{isPlanMode ? <PlanModeIndicator mode={mode} activeToolName={activeToolName} /> : null}
		</Box>
	);
}

export const StatusBar = React.memo(StatusBarInner, areStatusBarPropsEqual);

function formatNum(n: number): string {
	if (n >= 1000) {
		return `${(n / 1000).toFixed(1)}k`;
	}
	return String(n);
}

function countActiveTasks(tasks: TaskSnapshot[]): number {
	return tasks.filter((task) => ACTIVE_TASK_STATUSES.has(task.status)).length;
}

export function areStatusBarPropsEqual(prev: StatusBarProps, next: StatusBarProps): boolean {
	if (prev.activeToolName !== next.activeToolName) {
		return false;
	}
	if (prev.showTaskSegment !== next.showTaskSegment) {
		return false;
	}
	const prevTaskCount = countActiveTasks(prev.tasks);
	const nextTaskCount = countActiveTasks(next.tasks);
	if (prevTaskCount !== nextTaskCount) {
		return false;
	}
	if ((prev.busy || prevTaskCount > 0 || next.busy || nextTaskCount > 0) && prev.elapsedSeconds !== next.elapsedSeconds) {
		return false;
	}
	return VISIBLE_STATUS_KEYS.every((key) => prev.status[key] === next.status[key]);
}

export function formatDuration(seconds: number): string {
	if (seconds < 60) {
		return `${seconds}s`;
	}
	const minutes = Math.floor(seconds / 60);
	const remainingSeconds = seconds % 60;
	if (minutes < 60) {
		return remainingSeconds > 0 ? `${minutes}m${remainingSeconds}s` : `${minutes}m`;
	}
	const hours = Math.floor(minutes / 60);
	const remainingMinutes = minutes % 60;
	return remainingMinutes > 0 ? `${hours}h${remainingMinutes}m` : `${hours}h`;
}
