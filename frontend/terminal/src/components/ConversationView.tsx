import React, {forwardRef, useEffect, useImperativeHandle, useRef, useState} from 'react';
import {Box, Text, measureElement} from 'ink';
import type {DOMElement} from 'ink';
import stringWidth from 'string-width';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {truncateWithEllipsis} from '../textLayout.js';
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
type ContentMode = 'welcome' | 'conversation';

const TURN_DIVIDER = '╌'.repeat(18);
const USER_SHELL_ORIGIN = 'user_shell';

function isUserShellToolItem(item: TranscriptItem | undefined): boolean {
	return item?.tool_input?.origin === USER_SHELL_ORIGIN;
}

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
	cols: number,
): React.JSX.Element {
	if (group.kind === 'pair') {
		return (
			<ToolCallDisplay
				key={`g-${group.index}`}
				item={group.call}
				resultItem={group.result}
				outputStyle={outputStyle}
				treePos={treePos}
				availableWidth={cols}
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
				availableWidth={cols}
			/>
		);
	}
	return (
		<MessageRow
			key={`g-${group.index}`}
			item={group.item}
			theme={theme}
			outputStyle={outputStyle}
			cols={cols}
		/>
	);
}

function conversationOwner(group: RenderGroup): 'assistant' | 'user' | null {
	if (group.kind === 'pair') {
		return isUserShellToolItem(group.call) ? 'user' : 'assistant';
	}
	if (group.kind === 'tool') {
		return isUserShellToolItem(group.item) ? 'user' : 'assistant';
	}
	if (group.item.role === 'user' || group.item.role === 'user_shell') {
		return 'user';
	}
	if (group.item.role === 'tool_result') {
		return isUserShellToolItem(group.item) ? 'user' : 'assistant';
	}
	if (group.item.role === 'assistant' && group.item.text.trim().length > 0) {
		return 'assistant';
	}
	return null;
}

function previousConversationOwner(groups: RenderGroup[], index: number): 'assistant' | 'user' | null {
	for (let i = index - 1; i >= 0; i--) {
		const owner = conversationOwner(groups[i]);
		if (owner) {
			return owner;
		}
	}
	return null;
}

function shouldRenderAssistantHeaderBeforeGroup(groups: RenderGroup[], index: number, outputStyle?: string): boolean {
	if (outputStyle === 'codex') {
		return false;
	}

	const current = groups[index];
	if (!current || conversationOwner(current) !== 'assistant') {
		return false;
	}

	return previousConversationOwner(groups, index) !== 'assistant';
}

function shouldRenderTurnDividerBeforeGroup(groups: RenderGroup[], index: number, outputStyle?: string): boolean {
	if (outputStyle === 'codex') {
		return false;
	}

	return conversationOwner(groups[index]) === 'user' && previousConversationOwner(groups, index) === 'assistant';
}

function AssistantRunHeader({
	theme,
}: {
	theme: ReturnType<typeof useTheme>['theme'];
}): React.JSX.Element {
	return (
		<Text>
			<Text color={theme.colors.muted} dimColor>
				╰─{' '}
			</Text>
			<Text color={theme.colors.success} bold>
				assistant
			</Text>
		</Text>
	);
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
	reasoningBuffer?: string;
	reasoningExpanded?: boolean;
	showWelcome: boolean;
	welcomeVersion?: string | null;
	outputStyle?: string;
	revealHeadKey?: number;
	onPauseChange?: (paused: boolean) => void;
};

const ConversationViewInner = forwardRef<ConversationViewHandle, ConversationViewInnerProps>(
	function ConversationViewInner(
		{transcript, assistantBuffer, reasoningBuffer = '', reasoningExpanded = false, showWelcome, welcomeVersion, outputStyle, revealHeadKey, onPauseChange},
		forwardedRef,
	): React.JSX.Element {
		const {theme} = useTheme();
		const {cols} = useTerminalSize();
		const isCodexStyle = outputStyle === 'codex';

		const viewportRef = useRef<DOMElement | null>(null);
		const contentRef = useRef<DOMElement | null>(null);
		const [viewportHeight, setViewportHeight] = useState(0);
		const [contentHeight, setContentHeight] = useState(0);
		const [measuredContentMode, setMeasuredContentMode] = useState<ContentMode | null>(null);
		const [scrollFromTop, setScrollFromTop] = useState(0);
		const [paused, setPaused] = useState(false);
		const lastRevealHeadKeyRef = useRef<number | undefined>(undefined);
		const pendingRevealHeadAnchorRef = useRef<number | null>(null);

		const groups = buildGroups(transcript);
		const treePositions = assignTreePositions(groups);
		const liveAssistantStartsNewRun = previousConversationOwner(groups, groups.length) !== 'assistant';
		const hasConversation = groups.length > 0 || assistantBuffer.length > 0 || reasoningBuffer.length > 0;
		const showWelcomeBanner = showWelcome;
		const contentMode: ContentMode = hasConversation ? 'conversation' : 'welcome';

		useEffect(() => {
			if (viewportRef.current) {
				const h = measureElement(viewportRef.current).height;
				if (h !== viewportHeight) setViewportHeight(h);
			}
			if (contentRef.current) {
				const h = measureElement(contentRef.current).height;
				if (h !== contentHeight) setContentHeight(h);
				if (measuredContentMode !== contentMode) setMeasuredContentMode(contentMode);
			}
		});

		useEffect(() => {
			onPauseChange?.(paused);
		}, [paused, onPauseChange]);

		const measuredHeightForCurrentContent = measuredContentMode === contentMode ? contentHeight : 0;
		const maxScroll = Math.max(0, measuredHeightForCurrentContent - viewportHeight);

		useEffect(() => {
			if (revealHeadKey == null || revealHeadKey === lastRevealHeadKeyRef.current) {
				return;
			}
			lastRevealHeadKeyRef.current = revealHeadKey;
			pendingRevealHeadAnchorRef.current = measuredHeightForCurrentContent;
		}, [measuredHeightForCurrentContent, revealHeadKey]);

		// In follow mode keep `scrollFromTop` mirrored to the live tail so
		// that the moment the user scrolls up we have a sensible base.
		// Must run BEFORE the reveal-trigger effect so that when both fire in
		// the same commit, the reveal's setScrollFromTop wins (last setState
		// call wins in React 18 automatic batching across effects).
		useEffect(() => {
			if (pendingRevealHeadAnchorRef.current != null) {
				return;
			}
			if (!paused && scrollFromTop !== maxScroll) {
				setScrollFromTop(maxScroll);
			}
		}, [paused, maxScroll, scrollFromTop]);

		useEffect(() => {
			const anchor = pendingRevealHeadAnchorRef.current;
			if (anchor == null || measuredHeightForCurrentContent <= anchor) {
				return;
			}
			pendingRevealHeadAnchorRef.current = null;
			setPaused(true);
			setScrollFromTop(Math.max(0, Math.min(anchor, maxScroll)));
		}, [maxScroll, measuredHeightForCurrentContent]);

		// Keep the empty welcome screen pinned to the top. Once conversation
		// content exists, keep the banner in scrollback and follow the live tail.
		// The content-mode check above prevents the first user turn from
		// inheriting the welcome-only measured height and jumping for one frame.
		const effectiveScroll = paused
			? Math.max(0, Math.min(scrollFromTop, maxScroll))
			: (hasConversation ? maxScroll : 0);
		const marginTop = -effectiveScroll;

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
					{showWelcomeBanner ? <WelcomeBanner version={welcomeVersion} /> : null}
					{groups.map((group, i) => {
						const showAssistantHeader = shouldRenderAssistantHeaderBeforeGroup(groups, i, outputStyle);
						const showTurnDivider = shouldRenderTurnDividerBeforeGroup(groups, i, outputStyle);
						const marginTop = showAssistantHeader || showTurnDivider ? 1 : 0;

						return (
							<Box key={`g-${group.index}`} flexShrink={0} flexDirection="column" marginTop={marginTop}>
								{showTurnDivider ? (
									<Text color={theme.colors.muted} dimColor>
										{TURN_DIVIDER}
									</Text>
								) : null}
								{showAssistantHeader ? <AssistantRunHeader theme={theme} /> : null}
								{renderGroup(group, theme, outputStyle, treePositions[i], cols)}
							</Box>
						);
					})}
					{reasoningBuffer || assistantBuffer ? (
						isCodexStyle ? (
							<Box flexShrink={0} flexDirection="column" marginTop={0}>
								{reasoningBuffer ? <Text dimColor>{reasoningBuffer}</Text> : null}
								{assistantBuffer ? <Text>{assistantBuffer}</Text> : null}
							</Box>
						) : (
							<Box
								flexShrink={0}
								marginTop={liveAssistantStartsNewRun ? 1 : 0}
								marginBottom={0}
								flexDirection="column"
							>
								{liveAssistantStartsNewRun ? <AssistantRunHeader theme={theme} /> : null}
								{reasoningBuffer ? <ReasoningBlock text={reasoningBuffer} theme={theme} expanded={reasoningExpanded} cols={cols} /> : null}
								{assistantBuffer ? (
									<Box marginLeft={2} flexDirection="column">
										<MarkdownText content={assistantBuffer} availableWidth={cols - 2} />
									</Box>
								) : null}
							</Box>
						)
					) : null}
				</Box>
			</Box>
		);
	},
);

export const ConversationView = React.memo(ConversationViewInner);

function ReasoningBlock({
	text,
	theme,
	expanded = false,
	cols = 80,
}: {
	text: string;
	theme: ReturnType<typeof useTheme>['theme'];
	expanded?: boolean;
	cols?: number;
}): React.JSX.Element {
	const [spinnerFrame, setSpinnerFrame] = useState(0);
	useEffect(() => {
		const interval = setInterval(() => {
			setSpinnerFrame((f) => (f + 1) % theme.icons.spinner.length);
		}, 80);
		return () => clearInterval(interval);
	}, [theme.icons.spinner.length]);

	const allLines = text.split('\n');
	if (allLines[allLines.length - 1] === '') {
		allLines.pop();
	}

	const foldLines = 3;
	const needsFold = allLines.length > foldLines && !expanded;
	const visibleLines = needsFold ? allLines.slice(-foldLines) : allLines;
	const hiddenCount = Math.max(0, allLines.length - foldLines);

	const spinner = theme.icons.spinner[spinnerFrame] ?? '⠋';
	const header = `╭─ ${spinner} reasoning`;

	// Account for parent paddingX={1} (2 cols) + this Box marginLeft={2} (2 cols) + │+space prefix (2 cols)
	const contentWidth = Math.max(1, cols - 6);
	// Account for parent paddingX={1} (2 cols) + this Box marginLeft={2} (2 cols)
	const borderLineWidth = Math.max(1, cols - 4);

	let footer: string | null = null;
	if (needsFold) {
		footer = truncateWithEllipsis(
			`╰─ ⬇ Space · expand ${hiddenCount} line${hiddenCount === 1 ? '' : 's'}`,
			borderLineWidth,
		);
	} else if (allLines.length > foldLines) {
		footer = '╰─ ⬆ Space · collapse';
	}

	return (
		<Box marginLeft={2} flexDirection="column">
			<Text color={theme.colors.info}>{truncateWithEllipsis(header, borderLineWidth)}</Text>
			{visibleLines.map((line, i) => (
				<Text key={i} color={theme.colors.muted} dimColor>
					{'│ '}{truncateWithEllipsis(line || ' ', contentWidth)}
				</Text>
			))}
			{footer ? (
				<Text color={theme.colors.info} dimColor>{footer}</Text>
			) : (
				<Text color={theme.colors.muted} dimColor>╰─</Text>
			)}
		</Box>
	);
}

function CompletedReasoningBlock({
	text,
	theme,
	cols = 80,
}: {
	text: string;
	theme: ReturnType<typeof useTheme>['theme'];
	cols?: number;
}): React.JSX.Element {
	const allLines = text.split('\n');
	if (allLines[allLines.length - 1] === '') {
		allLines.pop();
	}

	const lineCount = allLines.length;
	const header = `╭─ ✓ reasoning · ${lineCount} line${lineCount === 1 ? '' : 's'}`;
	// Account for parent paddingX={1} (2 cols) + this Box marginLeft={2} (2 cols) + │+space prefix (2 cols)
	const contentWidth = Math.max(1, cols - 6);
	// Account for parent paddingX={1} (2 cols) + this Box marginLeft={2} (2 cols)
	const borderLineWidth = Math.max(1, cols - 4);

	return (
		<Box marginLeft={2} flexDirection="column" marginBottom={0}>
			<Text color={theme.colors.muted} dimColor>{truncateWithEllipsis(header, borderLineWidth)}</Text>
			{allLines.map((line, i) => (
				<Text key={i} color={theme.colors.muted} dimColor>
					{'│ '}{truncateWithEllipsis(line || ' ', contentWidth)}
				</Text>
			))}
			<Text color={theme.colors.muted} dimColor>╰─</Text>
		</Box>
	);
}


function MessageRow({
	item,
	theme,
	outputStyle,
	cols,
}: {
	item: TranscriptItem;
	theme: ReturnType<typeof useTheme>['theme'];
	outputStyle?: string;
	cols: number;
}): React.JSX.Element {
	const isCodexStyle = outputStyle === 'codex';
	switch (item.role) {
		case 'user_shell': {
			const warning = theme.colors.warning;
			const lines = item.text.split('\n');
			return (
				<Box marginTop={0} marginBottom={0} flexDirection="column">
					{lines.map((line, i) => (
						<Text key={i}>
							<Text color={warning} bold>{i === 0 ? '! ' : '  '}</Text>
							<Text color={warning}>{line}</Text>
						</Text>
					))}
				</Box>
			);
		}

		case 'user': {
			const isMultiline = item.text.includes('\n');
			if (isCodexStyle) {
				if (!isMultiline) {
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
					<Box marginTop={0} flexDirection="column">
						{item.text.split('\n').map((line, i) => (
							<Text key={i}>
								<Text dimColor>{i === 0 ? '> ' : '  '}</Text>
								<Text>{line}</Text>
							</Text>
						))}
					</Box>
				);
			}
			if (!isMultiline) {
				return (
					<Box marginTop={0} marginBottom={0}>
						<Text>
							<Text color={theme.colors.secondary} bold>you</Text>
							<Text dimColor> · </Text>
							<Text>{item.text}</Text>
						</Text>
					</Box>
				);
			}
			const lines = item.text.split('\n');
			const firstLine = lines[0] ?? '';
			const restLines = lines.slice(1);
			const headerPrefix = 'you · ';
			const headerWidth = Math.max(1, cols - stringWidth(headerPrefix));
			const bodyWidth = Math.max(1, cols - 2);
			return (
				<Box marginTop={0} marginBottom={0} flexDirection="column">
					<Text>
						<Text color={theme.colors.secondary} bold>you</Text>
						{firstLine.length > 0 ? (
							<>
								<Text dimColor> · </Text>
								<Text>{truncateWithEllipsis(firstLine, headerWidth)}</Text>
							</>
						) : null}
					</Text>
					{restLines.length > 0 ? (
						<Box marginLeft={2} flexDirection="column">
							{restLines.map((line, i) => (
								<Text key={i}>{truncateWithEllipsis(line, bodyWidth) || ' '}</Text>
							))}
						</Box>
					) : null}
				</Box>
			);
		}

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
					{item.reasoning ? (
						<CompletedReasoningBlock text={item.reasoning} theme={theme} cols={cols} />
					) : null}
					<Box marginLeft={2} flexDirection="column">
						<MarkdownText content={item.text} availableWidth={cols - 2} />
					</Box>
				</Box>
			);

		case 'tool':
		case 'tool_result':
			return <ToolCallDisplay item={item} outputStyle={outputStyle} availableWidth={cols} />;

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
