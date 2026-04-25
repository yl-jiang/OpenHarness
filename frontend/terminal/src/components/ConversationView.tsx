import React from 'react';
import {Box, Static, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {MarkdownText} from './MarkdownText.js';
import {ToolCallDisplay, type TreePos} from './ToolCallDisplay.js';
import {WelcomeBanner} from './WelcomeBanner.js';
import {computeCommittedCutoff} from './transcriptCutoff.js';

type ToolPair = {kind: 'pair'; call: TranscriptItem; result: TranscriptItem; index: number};
type SoloTool = {kind: 'tool'; item: TranscriptItem; index: number};
type SoloMessage = {kind: 'message'; item: TranscriptItem; index: number};
type RenderGroup = ToolPair | SoloTool | SoloMessage;

/**
 * Group consecutive tool/tool_result items into pairs, while preserving the
 * absolute index of the first transcript item in every group.  The absolute
 * index is used as a stable React key so that <Static> can correctly identify
 * append-only growth across renders.
 */
function buildGroups(items: TranscriptItem[], offset: number): RenderGroup[] {
	const groups: RenderGroup[] = [];
	let i = 0;
	while (i < items.length) {
		const cur = items[i];
		if (cur.role === 'tool') {
			let toolEnd = i + 1;
			while (toolEnd < items.length && items[toolEnd].role === 'tool') toolEnd++;
			const toolCount = toolEnd - i;
			let resultEnd = toolEnd;
			while (resultEnd < items.length && items[resultEnd].role === 'tool_result') resultEnd++;
			const resultCount = resultEnd - toolEnd;
			const pairedCount = Math.min(toolCount, resultCount);
			for (let j = 0; j < pairedCount; j++) {
				groups.push({
					kind: 'pair',
					call: items[i + j],
					result: items[toolEnd + j],
					index: offset + i + j,
				});
			}
			for (let j = pairedCount; j < toolCount; j++) {
				groups.push({kind: 'tool', item: items[i + j], index: offset + i + j});
			}
			for (let j = pairedCount; j < resultCount; j++) {
				groups.push({kind: 'message', item: items[toolEnd + j], index: offset + toolEnd + j});
			}
			i = resultEnd;
		} else {
			groups.push({kind: 'message', item: cur, index: offset + i});
			i++;
		}
	}
	return groups;
}

function renderGroup(
	group: RenderGroup,
	theme: ReturnType<typeof useTheme>['theme'],
	outputStyle: string | undefined,
	treePos: TreePos,
): React.JSX.Element {
	if (group.kind === 'pair') {
		return (
			<ToolCallDisplay
				key={`g-${group.index}`}
				item={group.call}
				resultItem={group.result}
				outputStyle={outputStyle}
				treePos={treePos}
			/>
		);
	}
	if (group.kind === 'tool') {
		return (
			<ToolCallDisplay
				key={`g-${group.index}`}
				item={group.item}
				resultItem={undefined}
				outputStyle={outputStyle}
				treePos={treePos}
			/>
		);
	}
	return <MessageRow key={`g-${group.index}`} item={group.item} theme={theme} outputStyle={outputStyle} />;
}

/**
 * Assign tree connector positions ("first", "middle", "last", "single") to
 * tool groups that appear in adjacent runs (Copilot-style branching tree).
 */
function assignTreePositions(groups: RenderGroup[]): TreePos[] {
	const out: TreePos[] = new Array(groups.length).fill('single');
	let runStart = -1;
	const flush = (endExclusive: number): void => {
		if (runStart < 0) return;
		const len = endExclusive - runStart;
		if (len === 1) {
			out[runStart] = 'single';
		} else {
			for (let k = runStart; k < endExclusive; k++) {
				if (k === runStart) out[k] = 'first';
				else if (k === endExclusive - 1) out[k] = 'last';
				else out[k] = 'middle';
			}
		}
		runStart = -1;
	};
	for (let i = 0; i < groups.length; i++) {
		const g = groups[i];
		const isToolGroup = g.kind === 'pair' || g.kind === 'tool';
		const isEmptyAssistant =
			g.kind === 'message' && g.item.role === 'assistant' && !g.item.text.trim();
		if (isToolGroup) {
			if (runStart < 0) runStart = i;
		} else if (isEmptyAssistant) {
			// Empty assistants act as transparent separators inside a run.
			continue;
		} else {
			flush(i);
		}
	}
	flush(groups.length);
	return out;
}

type StaticEntry =
	| {kind: 'banner'}
	| {kind: 'group'; group: RenderGroup; treePos: TreePos};

function ConversationViewInner({
	transcript,
	assistantBuffer,
	showWelcome,
	outputStyle,
}: {
	transcript: TranscriptItem[];
	assistantBuffer: string;
	showWelcome: boolean;
	outputStyle?: string;
}): React.JSX.Element {
	const {theme} = useTheme();
	const isCodexStyle = outputStyle === 'codex';

	const cutoff = computeCommittedCutoff(transcript);
	const committedItems = transcript.slice(0, cutoff);
	const liveItems = transcript.slice(cutoff);

	const committedGroups = buildGroups(committedItems, 0);
	const liveGroups = buildGroups(liveItems, cutoff);

	// Compute tree positions across the *entire* group sequence (committed +
	// live) so that adjacent tools split across the boundary still get the
	// correct connectors.
	const allGroups = [...committedGroups, ...liveGroups];
	const allTreePos = assignTreePositions(allGroups);
	const committedTreePos = allTreePos.slice(0, committedGroups.length);
	const liveTreePos = allTreePos.slice(committedGroups.length);

	const staticEntries: StaticEntry[] = [];
	if (showWelcome) {
		staticEntries.push({kind: 'banner'});
	}
	for (let i = 0; i < committedGroups.length; i++) {
		staticEntries.push({kind: 'group', group: committedGroups[i], treePos: committedTreePos[i]});
	}

	return (
		<Box flexDirection="column">
			<Static items={staticEntries}>
				{(entry, idx) => {
					if (entry.kind === 'banner') {
						return <WelcomeBanner key="banner" />;
					}
					return (
						<Box key={`s-${entry.group.index}`} flexDirection="column">
							{renderGroup(entry.group, theme, outputStyle, entry.treePos)}
						</Box>
					);
				}}
			</Static>

			{liveGroups.map((group, i) => renderGroup(group, theme, outputStyle, liveTreePos[i]))}

			{assistantBuffer ? (
				isCodexStyle ? (
					<Box flexDirection="row" marginTop={0}>
						<Text>{assistantBuffer}</Text>
					</Box>
				) : (
					<Box marginTop={1} marginBottom={0} flexDirection="column">
						<Text>
							<Text color={theme.colors.success} bold>
								{theme.icons.assistant}
							</Text>
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

function MessageRow({
	item,
	theme,
	outputStyle,
}: {
	item: TranscriptItem;
	theme: ReturnType<typeof useTheme>['theme'];
	outputStyle?: string;
}): React.JSX.Element {
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
