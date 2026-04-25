import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {MarkdownText} from './MarkdownText.js';
import {ToolCallDisplay, type TreePos} from './ToolCallDisplay.js';
import {WelcomeBanner} from './WelcomeBanner.js';

type ToolPair = readonly [TranscriptItem, TranscriptItem];
type GroupedItem = TranscriptItem | ToolPair;

function groupToolPairs(items: TranscriptItem[]): GroupedItem[] {
	const result: GroupedItem[] = [];
	let i = 0;
	while (i < items.length) {
		const cur = items[i];
		if (cur.role === 'tool') {
			// Count consecutive tool calls starting at i (parallel batch)
			let toolEnd = i + 1;
			while (toolEnd < items.length && items[toolEnd].role === 'tool') {
				toolEnd++;
			}
			const toolCount = toolEnd - i;

			// Count consecutive tool_results immediately following the batch
			let resultEnd = toolEnd;
			while (resultEnd < items.length && items[resultEnd].role === 'tool_result') {
				resultEnd++;
			}
			const resultCount = resultEnd - toolEnd;

			// Pair each tool with its positionally-corresponding result
			const pairedCount = Math.min(toolCount, resultCount);
			for (let j = 0; j < pairedCount; j++) {
				result.push([items[i + j], items[toolEnd + j]] as const);
			}
			// Tools that don't yet have a result (still in progress)
			for (let j = pairedCount; j < toolCount; j++) {
				result.push(items[i + j]);
			}
			// Orphaned results (should not normally occur)
			for (let j = pairedCount; j < resultCount; j++) {
				result.push(items[toolEnd + j]);
			}
			i = resultEnd;
		} else {
			result.push(cur);
			i++;
		}
	}
	return result;
}

function ConversationViewInner({
	items,
	assistantBuffer,
	showWelcome,
	outputStyle,
	olderItemCount = 0,
	newerItemCount = 0,
}: {
	items: TranscriptItem[];
	assistantBuffer: string;
	showWelcome: boolean;
	outputStyle?: string;
	olderItemCount?: number;
	newerItemCount?: number;
}): React.JSX.Element {
	const {theme} = useTheme();
	const isCodexStyle = outputStyle === 'codex';
	const grouped = groupToolPairs(items);

	// Build rendered elements, detecting consecutive ToolPairs for tree connectors
	const elements: React.ReactNode[] = [];
	let gi = 0;
	while (gi < grouped.length) {
		const group = grouped[gi];
		if (Array.isArray(group)) {
			// Collect all ToolPairs in this run.
			// Empty-text assistant items are transparent separators: skip them
			// so that sequential single-tool calls also get tree connectors.
			const runPairs: ToolPair[] = [];
			let scanIdx = gi;
			while (scanIdx < grouped.length) {
				const it = grouped[scanIdx];
				if (Array.isArray(it)) {
					runPairs.push(it as ToolPair);
					scanIdx++;
				} else {
					const ti = it as TranscriptItem;
					if (ti.role === 'assistant' && !ti.text.trim()) {
						scanIdx++; // consume empty assistant (renders as <> anyway)
					} else {
						break;
					}
				}
			}
			gi = scanIdx;

			const runLen = runPairs.length;
			runPairs.forEach((pair, k) => {
				let treePos: TreePos;
				if (runLen === 1) {
					treePos = 'single';
				} else if (k === 0) {
					treePos = 'first';
				} else if (k === runLen - 1) {
					treePos = 'last';
				} else {
					treePos = 'middle';
				}
				elements.push(
					<ToolCallDisplay
						key={`tp-${gi - runLen + k}`}
						item={pair[0]}
						resultItem={pair[1]}
						outputStyle={outputStyle}
						treePos={treePos}
					/>
				);
			});
		} else {
			const single = group as TranscriptItem;
			// For unpaired in-progress tools, determine whether they're in a run with paired tools
			if (single.role === 'tool') {
				// Peek ahead to see if this tool is isolated or adjacent to other tools
				let runEnd = gi + 1;
				while (runEnd < grouped.length && !Array.isArray(grouped[runEnd]) && (grouped[runEnd] as TranscriptItem).role === 'tool') {
					runEnd++;
				}
				const runLen = runEnd - gi;
				for (let k = 0; k < runLen; k++) {
					const t = grouped[gi + k] as TranscriptItem;
					let treePos: TreePos;
					if (runLen === 1) {
						treePos = 'single';
					} else if (k === 0) {
						treePos = 'first';
					} else if (k === runLen - 1) {
						treePos = 'last';
					} else {
						treePos = 'middle';
					}
					elements.push(
						<ToolCallDisplay
							key={`t-${gi + k}`}
							item={t}
							resultItem={undefined}
							outputStyle={outputStyle}
							treePos={treePos}
						/>
					);
				}
				gi = runEnd;
			} else {
				elements.push(
					<MessageRow key={`m-${gi}`} item={single} theme={theme} outputStyle={outputStyle} />
				);
				gi++;
			}
		}
	}

	return (
		<Box flexDirection="column" flexGrow={1}>
			<ViewportBanner olderItemCount={olderItemCount} newerItemCount={newerItemCount} />
			{showWelcome && items.length === 0 ? <WelcomeBanner /> : null}

			{elements}

			{assistantBuffer ? (
				isCodexStyle ? (
					<Box flexDirection="row" marginTop={0}>
						<Text>{assistantBuffer}</Text>
					</Box>
				) : (
					<Box marginTop={1} marginBottom={0} flexDirection="column">
						<Text>
							<Text color={theme.colors.success} bold>{theme.icons.assistant}</Text>
						</Text>
						<Box marginLeft={2} flexDirection="column">
							<MarkdownText content={assistantBuffer} />
						</Box>
					</Box>
				)
			) : null}
		</Box>
	);
}

export const ConversationView = React.memo(ConversationViewInner);

function ViewportBanner({
	olderItemCount,
	newerItemCount,
}: {
	olderItemCount: number;
	newerItemCount: number;
}): React.JSX.Element {
	const {theme} = useTheme();
	const isReviewingHistory = olderItemCount > 0 || newerItemCount > 0;
	const statusColor = isReviewingHistory ? theme.colors.warning : theme.colors.success;
	const statusText = isReviewingHistory ? 'Reviewing history' : 'Live output';
	const detailText = isReviewingHistory
		? `${olderItemCount} above · ${newerItemCount} below · PgDn resumes live`
		: 'Newest responses stay pinned to the bottom';

	return (
		<Box marginBottom={0}>
			<Text dimColor>
				<Text color={statusColor} bold>{statusText}</Text>
				<Text dimColor> · {detailText}</Text>
			</Text>
		</Box>
	);
}

function MessageRow({item, theme, outputStyle}: {item: TranscriptItem; theme: ReturnType<typeof useTheme>['theme']; outputStyle?: string}): React.JSX.Element {
	const isCodexStyle = outputStyle === 'codex';
	switch (item.role) {
		case 'user':
			if (isCodexStyle) {
				return (
					<Box marginTop={0}>
						<Text>
							<Text dimColor>{'> '}</Text>
							<Text>{item.text}</Text>
						</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={0} marginBottom={0}>
					<Text>
						<Text color={theme.colors.secondary} bold>you</Text>
						<Text dimColor> · </Text>
						<Text>{item.text}</Text>
					</Text>
				</Box>
			);

		case 'assistant':
			if (isCodexStyle) {
				if (!item.text.trim()) return <></>;
				return (
					<Box marginTop={0} marginBottom={0}>
						<Text>{item.text}</Text>
					</Box>
				);
			}
			if (!item.text.trim()) return <></>;
			return (
				<Box marginTop={0} marginBottom={0} flexDirection="column">
					<Text color={theme.colors.success} bold>assistant</Text>
					<Box marginLeft={2} flexDirection="column">
						<MarkdownText content={item.text} />
					</Box>
				</Box>
			);

		case 'tool':
		case 'tool_result':
			return <ToolCallDisplay item={item} outputStyle={outputStyle} />;

		case 'system':
			if (isCodexStyle) {
				return (
					<Box marginTop={0}>
						<Text>
							<Text color={theme.colors.warning}>[system]</Text>
							<Text> {item.text}</Text>
						</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={0}>
					<Text>
						<Text color={theme.colors.warning} bold>system</Text>
						<Text dimColor> · </Text>
						<Text color={theme.colors.warning}>{item.text}</Text>
					</Text>
				</Box>
			);

		case 'status':
			return (
				<Box marginTop={0}>
					<Text color={theme.colors.info}>· {item.text}</Text>
				</Box>
			);

		case 'log':
			return (
				<Box>
					<Text dimColor>{item.text}</Text>
				</Box>
			);

		default:
			return (
				<Box>
					<Text>{item.text}</Text>
				</Box>
			);
	}
}
