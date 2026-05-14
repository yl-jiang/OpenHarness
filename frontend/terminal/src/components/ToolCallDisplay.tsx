import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';

export type TreePos = 'single' | 'first' | 'middle' | 'last';

const USER_SHELL_ORIGIN = 'user_shell';

export function ToolCallDisplay({item, resultItem, outputStyle, treePos}: {item: TranscriptItem; resultItem?: TranscriptItem; outputStyle?: string; treePos?: TreePos}): React.JSX.Element {
	const {theme} = useTheme();
	const isCodexStyle = outputStyle === 'codex';

	if (item.role === 'tool') {
		const isUserShellTool = item.tool_input?.origin === USER_SHELL_ORIGIN;
		const toolName = isUserShellTool ? 'shell' : (item.tool_name ?? 'tool');
		const summaryToolName = item.tool_name ?? toolName;
		const summary = summarizeInput(summaryToolName, item.tool_input, item.text).replace(/\s+/g, ' ').trim();

		let statusNode: React.ReactNode = null;
		let errorLines: string[] | null = null;
		let outputLines: string[] | null = null;

		if (resultItem) {
			if (resultItem.is_error) {
				statusNode = isCodexStyle
					? <Text color={theme.colors.error}> error</Text>
					: <Text color={theme.colors.error}> {theme.icons.error.trim()}</Text>;
				const lines = resultItem.text.split('\n').filter((l) => l.trim());
				const maxErrLines = isCodexStyle ? 8 : 5;
				errorLines = lines.length > maxErrLines
					? [...lines.slice(0, maxErrLines), `... (${lines.length - maxErrLines} more lines)`]
					: lines;
			} else if (!isCodexStyle) {
				const lineCount = resultItem.text.split('\n').filter((l) => l.trim()).length;
				const resultLabel = lineCount > 0 ? `${lineCount}L` : theme.icons.success.trim();
				statusNode = <Text dimColor> → {resultLabel}</Text>;
				if (isUserShellTool) {
					outputLines = resultItem.text.length > 0 ? resultItem.text.split('\n') : [''];
				}
			} else {
				const lineCount = resultItem.text.split('\n').filter((l) => l.trim()).length;
				statusNode = <Text dimColor>{lineCount > 0 ? ` ${lineCount}L` : ''}</Text>;
				if (isUserShellTool) {
					outputLines = resultItem.text.length > 0 ? resultItem.text.split('\n') : [''];
				}
			}
		} else if (!isCodexStyle) {
			// Tool is still in progress — no result yet
			statusNode = <Text dimColor> → …</Text>;
		}

		if (isUserShellTool && !isCodexStyle) {
			return (
				<ShellToolPanel
					command={summary}
					resultItem={resultItem}
					outputLines={outputLines}
					errorLines={errorLines}
				/>
			);
		}

		if (isCodexStyle) {
			return (
				<Box marginLeft={0} flexDirection="column">
					<Text dimColor>{`• Ran ${toolName}${summary ? ` ${summary}` : ''}`}{statusNode}</Text>
					{errorLines?.map((line, i) => {
						const prefix = i === errorLines.length - 1 ? '└ ' : '│ ';
						return (
							<Text key={i} color={theme.colors.error}>
								{prefix}
								{line}
							</Text>
						);
					})}
					{outputLines?.map((line, i) => {
						const prefix = i === outputLines.length - 1 ? '└ ' : '│ ';
						return (
							<Text key={i} dimColor>
								{prefix}
								{line || ' '}
							</Text>
						);
					})}
				</Box>
			);
		}

		// Tree connector: parallel members use ├─/└─, single tool uses the theme icon
		const isParallel = treePos !== undefined && treePos !== 'single';
		const connectorIcon = isParallel
			? (treePos === 'last' ? '  └─ ' : '  ├─ ')
			: theme.icons.tool;
		const toolColor = isUserShellTool ? theme.colors.warning : theme.colors.accent;
		const connectorColor = isParallel ? theme.colors.muted : toolColor;

		return (
			<Box marginLeft={2} flexDirection="column">
				<Text>
					<Text color={connectorColor}>{connectorIcon}</Text>
					<Text color={toolColor} bold>{toolName}</Text>
					<Text dimColor> {summary}</Text>
					{statusNode}
				</Text>
				{errorLines?.map((line, i) => (
					<Box key={i} marginLeft={4}>
						<Text color={theme.colors.error}>{line}</Text>
					</Box>
				))}
				{outputLines?.map((line, i) => (
					<Box key={i} marginLeft={4}>
						<Text>{line || ' '}</Text>
					</Box>
				))}
			</Box>
		);
	}

	if (item.role === 'tool_result') {
		const lines = item.text.length > 0
			? item.text.split('\n').filter((l) => l.trim())
			: [''];
		const maxLines = isCodexStyle ? 8 : 5;
		const display = lines.length > maxLines ? [...lines.slice(0, maxLines), `... (${lines.length - maxLines} more lines)`] : lines;
		const color = item.is_error ? theme.colors.error : undefined;
		if (isCodexStyle) {
			return (
				<Box marginLeft={0} flexDirection="column">
					{display.map((line, i) => {
						const prefix = i === display.length - 1 ? '└ ' : '│ ';
						return (
							<Text key={i} color={color} dimColor={!item.is_error}>
								{prefix}
								{line}
							</Text>
						);
					})}
				</Box>
			);
		}
		if (!item.is_error) {
			return <></>;
		}
		return (
			<Box marginLeft={4} flexDirection="column">
				{display.map((line, i) => (
					<Text key={i} color={theme.colors.error}>{line}</Text>
				))}
			</Box>
		);
	}

	return <Text>{item.text}</Text>;
}

function ShellToolPanel({
	command,
	resultItem,
	outputLines,
	errorLines,
}: {
	command: string;
	resultItem?: TranscriptItem;
	outputLines: string[] | null;
	errorLines: string[] | null;
}): React.JSX.Element {
	const {theme} = useTheme();
	const statusColor = resultItem?.is_error ? theme.colors.error : theme.colors.success;
	const statusSymbol = resultItem ? (resultItem.is_error ? 'x' : '✓') : '⊷';
	const bodyLines = resultItem?.is_error ? errorLines : outputLines;

	return (
		<Box marginLeft={2} flexDirection="column">
			<Text>
				<Text color={statusColor} bold>{statusSymbol}</Text>
				<Text>{'  '}</Text>
				<Text bold>Shell Command</Text>
				{command ? (
					<>
						<Text>{' '}</Text>
						<Text color={theme.colors.muted}>{command}</Text>
					</>
				) : null}
			</Text>
			{bodyLines && bodyLines.length > 0 ? (
				<>
					<Text> </Text>
					{bodyLines.map((line, i) => {
						const lineColor = resultItem?.is_error ? theme.colors.error : undefined;
						return (
							<Box key={i} marginLeft={4}>
								<Text color={lineColor} dimColor>
									{line || ' '}
								</Text>
							</Box>
						);
					})}
				</>
			) : null}
		</Box>
	);
}

function summarizeInput(toolName: string, toolInput?: Record<string, unknown>, fallback?: string): string {
	if (!toolInput) {
		return fallback?.slice(0, 80) ?? '';
	}
	const lower = toolName.toLowerCase();
	// bash
	if (lower === 'bash' && toolInput.command) {
		return String(toolInput.command).slice(0, 120);
	}
	// file read — actual tool uses `path`; also accept legacy `file_path`
	if ((lower === 'read_file' || lower === 'read' || lower === 'fileread') && (toolInput.path || toolInput.file_path)) {
		const p = String(toolInput.path ?? toolInput.file_path);
		const limit = toolInput.limit ? ` [${toolInput.limit}L]` : '';
		return p + limit;
	}
	// file write / edit
	if ((lower === 'write_file' || lower === 'write' || lower === 'filewrite') && (toolInput.path || toolInput.file_path)) {
		return String(toolInput.path ?? toolInput.file_path);
	}
	if ((lower === 'edit_file' || lower === 'edit' || lower === 'fileedit' || lower === 'multiedit') && (toolInput.path || toolInput.file_path)) {
		return String(toolInput.path ?? toolInput.file_path);
	}
	// grep — show path when present for context
	if (lower === 'grep' && toolInput.pattern) {
		const inPath = toolInput.path ? ` ${String(toolInput.path)}` : '';
		return `/${String(toolInput.pattern)}/${inPath}`.trim();
	}
	// glob — show path for context
	if (lower === 'glob' && toolInput.pattern) {
		const inPath = toolInput.path ? ` in ${String(toolInput.path)}` : '';
		return String(toolInput.pattern) + inPath;
	}
	// web tools
	if ((lower === 'web_fetch' || lower === 'webfetch') && toolInput.url) {
		return String(toolInput.url).slice(0, 80);
	}
	if ((lower === 'web_search' || lower === 'websearch') && toolInput.query) {
		return `"${String(toolInput.query).slice(0, 70)}"`;
	}
	// agents / tasks
	if ((lower === 'agent' || lower === 'task_create') && toolInput.description) {
		return String(toolInput.description).slice(0, 80);
	}
	// Fallback: show first key=value
	const entries = Object.entries(toolInput);
	if (entries.length > 0) {
		const [key, val] = entries[0];
		return `${key}=${String(val).slice(0, 60)}`;
	}
	return fallback?.slice(0, 80) ?? '';
}
