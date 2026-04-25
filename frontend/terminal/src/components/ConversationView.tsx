import React, {forwardRef, useEffect, useImperativeHandle, useRef, useState} from 'react';
import {Box, Text, measureElement} from 'ink';
import type {DOMElement} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {MarkdownText} from './MarkdownText.js';
import {ToolCallDisplay, type TreePos} from './ToolCallDisplay.js';
import {WelcomeBanner} from './WelcomeBanner.js';

export type ConversationViewHandle = {
	scrollUp(lines: number): void;
	scrollDown(lines: number): void;
	scrollToTop(): void;
	scrollToBottom(): void;
};

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

function isAssistantOwnedGroup(group: RenderGroup): boolean {
	if (group.kind === 'pair' || group.kind === 'tool') {
		return true;
	}

	return group.item.role === 'tool_result';
}

function hasVisibleAssistantHeader(group: RenderGroup): boolean {
	return group.kind === 'message' && group.item.role === 'assistant' && group.item.text.trim().length > 0;
}

function shouldRenderAssistantHeaderBeforeGroup(groups: RenderGroup[], index: number, outputStyle?: string): boolean {
	if (outputStyle === 'codex') {
		return false;
	}

	const current = groups[index];
	if (!current || !isAssistantOwnedGroup(current)) {
		return false;
	}

	const previous = groups[index - 1];
	if (!previous) {
		return true;
	}

	if (isAssistantOwnedGroup(previous)) {
		return false;
	}

	return !hasVisibleAssistantHeader(previous);
}

/**
 * Smooth, line-level scrollable transcript panel.
 *
 * Approach: render the full transcript top-to-bottom in a tall content box
 * (no slicing, no column-reverse), wrap it in a fixed-height viewport with
 * `overflow: hidden`, then translate the content vertically by setting a
 * (possibly negative) `marginTop` on it.  After every render we measure the
 * actual viewport and content heights with `measureElement`, then clamp the
 * scroll offset and update the margin.
 *
 * Scroll semantics (line offset from top of content):
 *   - paused = false → "follow tail":  marginTop tracks the live tail so the
 *     bottom of the content stays at the bottom of the viewport.
 *   - paused = true  → user is browsing history: `scrollFromTop` is fixed at
 *     the user-chosen position; new content arriving at the bottom does not
 *     shift the visible window.
 *   - One wheel tick = 3 lines, PgUp/PgDn = half a viewport, g/Home = top,
 *     G/End = resume tail.
 */
type ConversationViewInnerProps = {
	transcript: TranscriptItem[];
	assistantBuffer: string;
	showWelcome: boolean;
	outputStyle?: string;
	onPauseChange?: (paused: boolean) => void;
};

const ConversationViewInner = forwardRef<ConversationViewHandle, ConversationViewInnerProps>(
	function ConversationViewInner(
		{transcript, assistantBuffer, showWelcome, outputStyle, onPauseChange},
		forwardedRef,
	): React.JSX.Element {
		const {theme} = useTheme();
		const isCodexStyle = outputStyle === 'codex';

		const viewportRef = useRef<DOMElement | null>(null);
		const contentRef = useRef<DOMElement | null>(null);
		const [viewportHeight, setViewportHeight] = useState(0);
		const [contentHeight, setContentHeight] = useState(0);
		const [scrollFromTop, setScrollFromTop] = useState(0);
		const [paused, setPaused] = useState(false);

		const groups = buildGroups(transcript);
		const treePositions = assignTreePositions(groups);

		useEffect(() => {
			if (viewportRef.current) {
				const h = measureElement(viewportRef.current).height;
				if (h !== viewportHeight) setViewportHeight(h);
			}
			if (contentRef.current) {
				const h = measureElement(contentRef.current).height;
				if (h !== contentHeight) setContentHeight(h);
			}
		});

		useEffect(() => {
			onPauseChange?.(paused);
		}, [paused, onPauseChange]);

		const maxScroll = Math.max(0, contentHeight - viewportHeight);
		const effectiveScroll = paused
			? Math.max(0, Math.min(scrollFromTop, maxScroll))
			: maxScroll;
		const marginTop = -effectiveScroll;

		// In follow mode keep `scrollFromTop` mirrored to the live tail so
		// that the moment the user scrolls up we have a sensible base.
		useEffect(() => {
			if (!paused && scrollFromTop !== maxScroll) {
				setScrollFromTop(maxScroll);
			}
		}, [paused, maxScroll, scrollFromTop]);

		useImperativeHandle(
			forwardedRef,
			() => ({
				scrollUp(lines: number) {
					setPaused(true);
					setScrollFromTop((s) => {
						const base = paused ? s : maxScroll;
						return Math.max(0, base - Math.max(1, Math.floor(lines)));
					});
				},
				scrollDown(lines: number) {
					setScrollFromTop((s) => {
						const base = paused ? s : maxScroll;
						const next = base + Math.max(1, Math.floor(lines));
						if (next >= maxScroll) {
							setPaused(false);
							return maxScroll;
						}
						setPaused(true);
						return next;
					});
				},
				scrollToTop() {
					setPaused(true);
					setScrollFromTop(0);
				},
				scrollToBottom() {
					setPaused(false);
					setScrollFromTop(maxScroll);
				},
			}),
			[paused, maxScroll],
		);

		return (
			<Box ref={viewportRef} flexGrow={1} flexShrink={1} flexDirection="column" overflow="hidden">
				<Box ref={contentRef} flexShrink={0} flexDirection="column" marginTop={marginTop}>
					{showWelcome ? <WelcomeBanner /> : null}
					{groups.map((group, i) => (
						<Box key={`g-${group.index}`} flexShrink={0} flexDirection="column">
							{shouldRenderAssistantHeaderBeforeGroup(groups, i, outputStyle) ? (
								<Text color={theme.colors.success} bold>
									assistant
								</Text>
							) : null}
							{renderGroup(group, theme, outputStyle, treePositions[i])}
						</Box>
					))}
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
				</Box>
			</Box>
		);
	},
);

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
