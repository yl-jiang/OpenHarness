import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {MarkdownText} from './MarkdownText.js';
import {ToolCallDisplay, type TreePos} from './ToolCallDisplay.js';
import {WelcomeBanner} from './WelcomeBanner.js';

type ToolPair = {kind: 'pair'; call: TranscriptItem; result: TranscriptItem; index: number};
type SoloTool = {kind: 'tool'; item: TranscriptItem; index: number};
type SoloMessage = {kind: 'message'; item: TranscriptItem; index: number};
type RenderGroup = ToolPair | SoloTool | SoloMessage;

function buildGroups(items: TranscriptItem[]): RenderGroup[] {
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
				groups.push({kind: 'pair', call: items[i + j], result: items[toolEnd + j], index: i + j});
			}
			for (let j = pairedCount; j < toolCount; j++) {
				groups.push({kind: 'tool', item: items[i + j], index: i + j});
			}
			for (let j = pairedCount; j < resultCount; j++) {
				groups.push({kind: 'message', item: items[toolEnd + j], index: toolEnd + j});
			}
			i = resultEnd;
		} else {
			groups.push({kind: 'message', item: cur, index: i});
			i++;
		}
	}
	return groups;
}

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
		const isEmptyAssistant = g.kind === 'message' && g.item.role === 'assistant' && !g.item.text.trim();
		if (isToolGroup) {
			if (runStart < 0) runStart = i;
		} else if (isEmptyAssistant) {
			continue;
		} else {
			flush(i);
		}
	}
	flush(groups.length);
	return out;
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
	return (
		<MessageRow
			key={`g-${group.index}`}
			item={group.item}
			theme={theme}
			outputStyle={outputStyle}
		/>
	);
}

/**
 * Scrollable transcript panel.  Renders the (possibly sliced) transcript in
 * a single overflow:hidden, column-reverse box.  The slice's last item is
 * pinned to the visual bottom, with earlier items stacking upward; anything
 * beyond the box top is clipped.  Item-level scrolling is implemented at
 * the caller by passing a shorter slice — that naturally moves the visible
 * bottom anchor toward the start of the transcript, revealing earlier
 * history above it.
 */
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

	const groups = buildGroups(transcript);
	const treePositions = assignTreePositions(groups);

	// We use column-reverse so the *first* DOM child renders at the visual
	// bottom of the viewport, with subsequent children stacking upward.  When
	// the children overflow the box, yoga clips the visual top — i.e. the
	// older content scrolls off the top edge first, exactly like a normal
	// terminal scrollback view.  Item-level scrolling is implemented at the
	// caller (App.tsx) by simply slicing `transcript`: a shorter slice means
	// the slice's last item becomes the new visual bottom, naturally
	// revealing earlier history above it.
	const reversedGroupEntries = groups.map((group, i) => ({group, treePos: treePositions[i]})).reverse();

	return (
		<Box flexGrow={1} flexShrink={1} flexDirection="column-reverse" overflow="hidden">
			{assistantBuffer ? (
				isCodexStyle ? (
					<Box flexShrink={0} flexDirection="row" marginTop={0}>
						<Text>{assistantBuffer}</Text>
					</Box>
				) : (
					<Box flexShrink={0} marginTop={1} marginBottom={0} flexDirection="column">
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
			{reversedGroupEntries.map(({group, treePos}) => (
				<Box key={`g-${group.index}`} flexShrink={0} flexDirection="column">
					{renderGroup(group, theme, outputStyle, treePos)}
				</Box>
			))}
			{showWelcome ? <WelcomeBanner /> : null}
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
