import React from 'react';
import {Box, Text} from 'ink';
import stringWidth from 'string-width';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';

export type TreePos = 'single' | 'first' | 'middle' | 'last';

const USER_SHELL_ORIGIN = 'user_shell';
const DEFAULT_AVAILABLE_WIDTH = 100;

export function ToolCallDisplay({item, resultItem, outputStyle, treePos, availableWidth}: {item: TranscriptItem; resultItem?: TranscriptItem; outputStyle?: string; treePos?: TreePos; availableWidth?: number}): React.JSX.Element {
	const {theme} = useTheme();
	const isCodexStyle = outputStyle === 'codex';

	if (item.role === 'tool') {
		const isUserShellTool = item.tool_input?.origin === USER_SHELL_ORIGIN;
		const toolName = isUserShellTool ? 'shell' : (item.tool_name ?? 'tool');
		const summaryToolName = item.tool_name ?? toolName;
		const isShellLikeTool = isShellToolName(summaryToolName) || isShellToolName(toolName);
		const summary = summarizeInput(summaryToolName, item.tool_input, item.text).replace(/\s+/g, ' ').trim();
		const isAskUserTool = summaryToolName.toLowerCase() === 'ask_user_question';

		let statusNode: React.ReactNode = null;
		let errorLines: string[] | null = null;
		let userAnswer: string | null = null;

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
			} else if (isAskUserTool && resultItem.text.trim()) {
				userAnswer = resultItem.text.trim();
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
					errorLines={errorLines}
				/>
			);
		}

		if (isCodexStyle) {
			return (
				<Box marginLeft={0} flexDirection="column">
					<Text>
						<Text dimColor>{`• Ran ${toolName}`}</Text>
						{summary ? (
							<Text color={isShellLikeTool ? theme.colors.warning : undefined} dimColor={!isShellLikeTool}>
								{` ${summary}`}
							</Text>
						) : null}
						{statusNode}
					</Text>
					{errorLines?.map((line, i) => {
						const prefix = i === errorLines.length - 1 ? '└ ' : '│ ';
						return (
							<Text key={i} color={theme.colors.error}>
								{prefix}
								{line}
							</Text>
						);
					})}
					{userAnswer !== null ? (
						<Text>
							<Text dimColor>{'> '}</Text>
							<Text>{userAnswer}</Text>
						</Text>
					) : null}
				</Box>
			);
		}

		// Tree connector: parallel members use ├─/└─, single tool uses the theme icon
		const isParallel = treePos !== undefined && treePos !== 'single';
		const connectorIcon = isParallel
			? (treePos === 'last' ? '  └─ ' : '  ├─ ')
			: theme.icons.tool;
		const continuationConnector = isParallel && treePos !== 'last'
			? '  │  '
			: ' '.repeat(stringWidth(connectorIcon));
		const toolColor = isUserShellTool ? theme.colors.warning : theme.colors.accent;
		const connectorColor = isParallel ? theme.colors.muted : toolColor;
		// availableWidth = raw terminal cols; subtract App paddingX(1)*2 + own marginLeft(2)
		const contentWidth = Math.max(1, (availableWidth ?? DEFAULT_AVAILABLE_WIDTH) - 4);
		const summaryLines = wrapToolSummary({
			summary,
			firstPrefixWidth: stringWidth(connectorIcon) + stringWidth(toolName) + 1,
			continuationPrefixWidth: stringWidth(continuationConnector) + stringWidth(toolName) + 1,
			availableWidth: contentWidth,
		});
		const firstSummaryLine = summaryLines[0] ?? '';
		const continuationSummaryLines = summaryLines.slice(1);

		return (
			<Box marginLeft={2} flexDirection="column">
				<Text>
					<Text color={connectorColor}>{connectorIcon}</Text>
					<Text color={toolColor} bold>{toolName}</Text>
					<Text color={isShellLikeTool ? theme.colors.warning : undefined} dimColor={!isShellLikeTool}>
						{firstSummaryLine ? ` ${firstSummaryLine}` : ''}
					</Text>
					{statusNode}
				</Text>
				{continuationSummaryLines.map((line, i) => (
					<Text key={i}>
						<Text color={connectorColor}>{continuationConnector}</Text>
						<Text>{' '.repeat(stringWidth(toolName))}</Text>
						<Text>{' '}</Text>
						<Text color={isShellLikeTool ? theme.colors.warning : undefined} dimColor={!isShellLikeTool}>
							{line || ' '}
						</Text>
					</Text>
				))}
				{errorLines?.map((line, i) => {
					const isLast = i === errorLines.length - 1;
					const errPrefix = isLast ? '└ ' : '│ ';
					return (
						<Text key={i}>
							<Text color={connectorColor}>{continuationConnector}</Text>
							<Text color={theme.colors.error}>{errPrefix}{line}</Text>
						</Text>
					);
				})}
				{userAnswer !== null ? (
					<AskUserAnswerRow answer={userAnswer} connector={continuationConnector} connectorColor={connectorColor} theme={theme} availableWidth={contentWidth} />
				) : null}
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
		if (!item.is_error) {
			return <></>;
		}
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
	errorLines,
}: {
	command: string;
	resultItem?: TranscriptItem;
	errorLines: string[] | null;
}): React.JSX.Element {
	const {theme} = useTheme();
	const statusColor = resultItem?.is_error ? theme.colors.error : theme.colors.success;
	const statusSymbol = resultItem ? (resultItem.is_error ? 'x' : '✓') : '⊷';

	return (
		<Box marginLeft={2} flexDirection="column">
			<Text>
				<Text color={statusColor} bold>{statusSymbol}</Text>
				<Text>{'  '}</Text>
				<Text bold>Shell Command</Text>
				{command ? (
					<>
						<Text>{' '}</Text>
						<Text color={theme.colors.warning}>{command}</Text>
					</>
				) : null}
			</Text>
			{errorLines && errorLines.length > 0 ? (
				<>
					<Text> </Text>
					{errorLines.map((line, i) => (
						<Box key={i} marginLeft={4}>
							<Text color={theme.colors.error} dimColor>
								{line || ' '}
							</Text>
						</Box>
					))}
				</>
			) : null}
		</Box>
	);
}

function AskUserAnswerRow({
	answer,
	connector,
	connectorColor,
	theme,
	availableWidth,
}: {
	answer: string;
	connector: string;
	connectorColor: string;
	theme: ReturnType<typeof useTheme>['theme'];
	availableWidth: number;
}): React.JSX.Element {
	const connectorWidth = stringWidth(connector);
	const prefix = 'you · ';
	const textWidth = Math.max(1, availableWidth - connectorWidth - stringWidth(prefix));
	const lines = answer.split('\n');
	const firstLine = lines[0] ?? '';
	const restLines = lines.slice(1);
	return (
		<Box flexDirection="column">
			<Text>
				<Text color={connectorColor}>{connector}</Text>
				<Text color={theme.colors.secondary} bold>you</Text>
				<Text dimColor> · </Text>
				<Text>{firstLine.slice(0, textWidth)}</Text>
			</Text>
			{restLines.map((line, i) => (
				<Text key={i}>
					<Text color={connectorColor}>{connector}</Text>
					<Text>{' '.repeat(stringWidth(prefix))}</Text>
					<Text>{line.slice(0, textWidth) || ' '}</Text>
				</Text>
			))}
		</Box>
	);
}

function isShellToolName(toolName: string): boolean {
	const lower = toolName.toLowerCase();
	return lower === 'bash' || lower === 'shell';
}

function wrapToolSummary({
	summary,
	firstPrefixWidth,
	continuationPrefixWidth,
	availableWidth,
}: {
	summary: string;
	firstPrefixWidth: number;
	continuationPrefixWidth: number;
	availableWidth: number;
}): string[] {
	if (!summary) {
		return [''];
	}
	const firstWidth = Math.max(1, availableWidth - firstPrefixWidth);
	const continuationWidth = Math.max(1, availableWidth - continuationPrefixWidth);
	const lines: string[] = [];
	let remaining = summary;
	let width = firstWidth;

	while (remaining) {
		const [line, rest] = takeWrappedLine(remaining, width);
		lines.push(line);
		remaining = rest;
		width = continuationWidth;
	}

	return lines;
}

function takeWrappedLine(value: string, maxWidth: number): [string, string] {
	if (stringWidth(value) <= maxWidth) {
		return [value, ''];
	}

	let sliceEnd = 0;
	let usedWidth = 0;
	let lastWhitespace = -1;
	let offset = 0;
	for (const char of value) {
		const charWidth = stringWidth(char);
		if (usedWidth + charWidth > maxWidth) {
			break;
		}
		usedWidth += charWidth;
		offset += char.length;
		sliceEnd = offset;
		if (/\s/.test(char)) {
			lastWhitespace = offset;
		}
	}

	const breakAt = lastWhitespace > 0 ? lastWhitespace : Math.max(sliceEnd, 1);
	return [value.slice(0, breakAt).trimEnd(), value.slice(breakAt).trimStart()];
}

function summarizeInput(toolName: string, toolInput?: Record<string, unknown>, fallback?: string): string {
	if (!toolInput) {
		return fallback?.slice(0, 80) ?? '';
	}
	const lower = toolName.toLowerCase();
	// bash / shell
	if ((lower === 'bash' || lower === 'shell') && toolInput.command) {
		return String(toolInput.command).slice(0, 200);
	}
	if (lower === 'mcp_call') {
		const server = toolInput.server ?? toolInput.server_name ?? toolInput.mcp_server;
		const tool = toolInput.tool ?? toolInput.tool_name ?? toolInput.name;
		if (server && tool) {
			return `${String(server)}:${String(tool)}`;
		}
		if (tool) {
			return String(tool);
		}
	}
	// ask_user_question — show just the question text
	if (lower === 'ask_user_question' && toolInput.question) {
		return String(toolInput.question).slice(0, 120);
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
