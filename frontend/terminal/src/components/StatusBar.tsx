import React, {useEffect, useState} from 'react';
import {Box, Text} from 'ink';

import {formatDisplayPath} from '../pathDisplay.js';
import type {TaskSnapshot} from '../types.js';

// Hermes brand gold — matches WelcomeBanner palette
const H_GOLD = '#ffbd38';

const SEP = ' \u2502 ';
const CWD_MARKER = '>ˍ';

const WRITE_TOOLS = new Set([
	'Write', 'Edit', 'MultiEdit', 'NotebookEdit',
	'Bash', 'computer', 'str_replace_editor',
]);
const ACTIVE_TASK_STATUSES = new Set(['pending', 'running']);

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
}: {
	status: Record<string, unknown>;
	tasks: TaskSnapshot[];
	activeToolName?: string;
	elapsedSeconds?: number | null;
	busy?: boolean;
}): React.JSX.Element {
	const model = String(status.model ?? 'unknown');
	const mode = String(status.permission_mode ?? 'default');
	const cwd = formatDisplayPath(status.cwd);
	const gitBranch = typeof status.git_branch === 'string' && status.git_branch ? status.git_branch : null;
	const taskCount = tasks.filter((task) => ACTIVE_TASK_STATUSES.has(task.status)).length;
	const mcpCount = Number(status.mcp_connected ?? 0);
	const reviewsCompleted = Number(status.reviews_completed ?? 0);
	const inputTokens = Number(status.input_tokens ?? 0);
	const outputTokens = Number(status.output_tokens ?? 0);
	const isPlanMode = mode === 'plan' || mode === 'Plan Mode';

	return (
		<Box flexDirection="column" marginTop={1}>
			<Text dimColor>
				<Text color={H_GOLD} bold>OpenHarness</Text>
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
				{taskCount > 0 ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text dimColor>⚙️  {taskCount}</Text>
					</>
				) : null}
				{reviewsCompleted > 0 ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text dimColor>🧠 {reviewsCompleted} reviewed</Text>
					</>
				) : null}
				{mcpCount > 0 ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text dimColor>🔌 {mcpCount}</Text>
					</>
				) : null}
				{elapsedSeconds != null ? (
					<>
						<Text dimColor>{SEP}</Text>
						<Text color={busy ? 'cyan' : undefined} dimColor={!busy}>⏱ {elapsedSeconds}s</Text>
					</>
				) : null}
			</Text>
			{isPlanMode ? <PlanModeIndicator mode={mode} activeToolName={activeToolName} /> : null}
		</Box>
	);
}

export const StatusBar = React.memo(StatusBarInner);

function formatNum(n: number): string {
	if (n >= 1000) {
		return `${(n / 1000).toFixed(1)}k`;
	}
	return String(n);
}
