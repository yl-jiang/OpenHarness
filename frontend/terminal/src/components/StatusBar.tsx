import React, {useEffect, useRef, useState} from 'react';
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
type StatusBarProps = {
	status: Record<string, unknown>;
	tasks: TaskSnapshot[];
	activeToolName?: string;
	showTaskSegment?: boolean;
};

const MODE_CYCLE: ReadonlyArray<{label: string; color: string}> = [
	{label: 'Default', color: 'green'},
	{label: 'Plan Mode', color: 'yellow'},
	{label: 'Auto', color: 'red'},
];
const TOAST_DURATION_MS = 1500;

function modeColor(mode: string): string {
	return MODE_CYCLE.find((m) => m.label === mode)?.color ?? 'gray';
}

function PermissionModeToast({
	mode,
	activeToolName,
}: {
	mode: string;
	activeToolName?: string;
}): React.JSX.Element | null {
	const [showToast, setShowToast] = useState(false);
	const prevModeRef = useRef(mode);

	useEffect(() => {
		if (prevModeRef.current !== mode) {
			prevModeRef.current = mode;
			setShowToast(true);
			const timer = setTimeout(() => setShowToast(false), TOAST_DURATION_MS);
			return () => clearTimeout(timer);
		}
	}, [mode]);

	if (showToast) {
		const isPlanBlocked = activeToolName != null && WRITE_TOOLS.has(activeToolName);
		return (
			<Box flexDirection="column">
				<Box>
					<Text dimColor>{'  \u27F3 '}</Text>
					{MODE_CYCLE.map((m, i) => {
						const isActive = m.label === mode;
						return (
							<React.Fragment key={m.label}>
								{i > 0 ? <Text dimColor> · </Text> : null}
								{isActive ? (
									<Text color={m.color} bold>{m.label}</Text>
								) : (
									<Text dimColor>{m.label}</Text>
								)}
							</React.Fragment>
						);
					})}
				</Box>
				{mode === 'Plan Mode' && isPlanBlocked ? (
					<Box>
						<Text color="red">{'    \u26D4 '}{activeToolName} blocked in plan mode</Text>
					</Box>
				) : null}
			</Box>
		);
	}

	if (mode === 'Plan Mode') {
		const isBlockedTool = activeToolName != null && WRITE_TOOLS.has(activeToolName);
		if (isBlockedTool) {
			return (
				<Box>
					<Text color="red">{'  \u26D4 '}{activeToolName} blocked in plan mode</Text>
				</Box>
			);
		}
	}

	return null;
}

function StatusBarInner({
	status,
	tasks,
	activeToolName,
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

	return (
		<Box flexDirection="column" marginTop={1}>
			<Text dimColor>
				<Text color={theme.colors.primary} bold>OpenHarness</Text>
				<Text dimColor>{SEP}</Text>
				<Text dimColor>@ {model}</Text>
				<Text dimColor>{SEP}</Text>
				<Text dimColor>$ {formatNum(inputTokens)} {'\u2193'} {formatNum(outputTokens)} {'\u2191'}</Text>
				<Text dimColor>{SEP}</Text>
				<Text color={modeColor(mode)} bold>{isPlanMode ? '\u270E' : mode === 'Auto' ? '\u26A1' : '\u2713'}</Text>
				<Text dimColor>{' '}{mode}</Text>
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
						<Text dimColor>{'⚙  '}{taskCount}</Text>
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
			</Text>
			<PermissionModeToast mode={mode} activeToolName={activeToolName} />
		</Box>
	);
}

export const StatusBar = React.memo(StatusBarInner, areStatusBarPropsEqual);

function formatNum(n: number): string {
	if (n >= 1_000_000_000_000) {
		return `${(n / 1_000_000_000_000).toFixed(1)}T`;
	}
	if (n >= 1_000_000_000) {
		return `${(n / 1_000_000_000).toFixed(1)}B`;
	}
	if (n >= 1_000_000) {
		return `${(n / 1_000_000).toFixed(1)}M`;
	}
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
	return VISIBLE_STATUS_KEYS.every((key) => prev.status[key] === next.status[key]);
}

