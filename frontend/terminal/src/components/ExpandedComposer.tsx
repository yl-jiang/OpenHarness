import React, {useMemo} from 'react';
import {Box, Text} from 'ink';

import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {CommandPicker} from './CommandPicker.js';

export const EXPAND_TRIGGER_SYMBOL = '⇖⇘';
export const EXPANDED_SEND_SYMBOL = '➤';

const BORDER_PADDING_WIDTH = 6;
const EDITOR_HEADER_ROWS = 1;
const EDITOR_FOOTER_ROWS = 1;

export type TerminalHitbox = {
	column: number;
	row: number;
	width: number;
	height: number;
};

export type ExpandedComposerState = {
	draft: string;
	cursorOffset: number;
	preferredColumn: number | null;
};

type ExpandedComposerViewLine = {
	key: string;
	text: string;
	cursorColumn: number | null;
};

export function createExpandedComposerState(draft: string): ExpandedComposerState {
	return {
		draft,
		cursorOffset: draft.length,
		preferredColumn: null,
	};
}

export function composePromptDraft(input: string, extraInputLines: string[]): string {
	return extraInputLines.length > 0 ? [...extraInputLines, input].join('\n') : input;
}

export function splitExpandedDraft(draft: string): {input: string; extraInputLines: string[]} {
	if (!draft.includes('\n')) {
		return {input: draft, extraInputLines: []};
	}
	const lines = draft.split('\n');
	return {
		extraInputLines: lines.slice(0, -1),
		input: lines.at(-1) ?? '',
	};
}

export function insertComposerText(state: ExpandedComposerState, text: string): ExpandedComposerState {
	const draft = state.draft.slice(0, state.cursorOffset) + text + state.draft.slice(state.cursorOffset);
	return {
		draft,
		cursorOffset: state.cursorOffset + text.length,
		preferredColumn: null,
	};
}

export function applyExpandedComposerInput(state: ExpandedComposerState, chunk: string): ExpandedComposerState {
	if (chunk === '\x7f' || chunk === '\b') {
		return deleteComposerBackward(state);
	}
	return insertComposerText(state, chunk);
}

export function deleteComposerBackward(state: ExpandedComposerState): ExpandedComposerState {
	if (state.cursorOffset === 0) {
		return state;
	}
	return {
		draft: state.draft.slice(0, state.cursorOffset - 1) + state.draft.slice(state.cursorOffset),
		cursorOffset: state.cursorOffset - 1,
		preferredColumn: null,
	};
}

export function deleteComposerForward(state: ExpandedComposerState): ExpandedComposerState {
	if (state.cursorOffset >= state.draft.length) {
		return state;
	}
	return {
		draft: state.draft.slice(0, state.cursorOffset) + state.draft.slice(state.cursorOffset + 1),
		cursorOffset: state.cursorOffset,
		preferredColumn: null,
	};
}

export function moveComposerCursor(
	state: ExpandedComposerState,
	direction: 'left' | 'right' | 'up' | 'down' | 'home' | 'end',
): ExpandedComposerState {
	if (direction === 'left') {
		return {
			...state,
			cursorOffset: Math.max(0, state.cursorOffset - 1),
			preferredColumn: null,
		};
	}
	if (direction === 'right') {
		return {
			...state,
			cursorOffset: Math.min(state.draft.length, state.cursorOffset + 1),
			preferredColumn: null,
		};
	}

	const location = locateCursor(state.draft, state.cursorOffset);
	if (direction === 'home') {
		return {
			...state,
			cursorOffset: location.lineStart,
			preferredColumn: null,
		};
	}
	if (direction === 'end') {
		return {
			...state,
			cursorOffset: location.lineEnd,
			preferredColumn: null,
		};
	}

	const targetLineIndex = direction === 'up' ? location.lineIndex - 1 : location.lineIndex + 1;
	if (targetLineIndex < 0 || targetLineIndex >= location.lineStarts.length) {
		return state;
	}
	const targetLineStart = location.lineStarts[targetLineIndex]!;
	const targetLineEnd = targetLineIndex + 1 < location.lineStarts.length ? location.lineStarts[targetLineIndex + 1]! - 1 : state.draft.length;
	const targetColumn = state.preferredColumn ?? location.column;
	return {
		...state,
		cursorOffset: targetLineStart + Math.min(targetColumn, targetLineEnd - targetLineStart),
		preferredColumn: targetColumn,
	};
}

export function completeLeadingCommand(draft: string, hint: string): {draft: string; cursorOffset: number} {
	const leadingWhitespace = draft.match(/^\s*/) ? draft.match(/^\s*/)![0] : '';
	const trimmed = draft.slice(leadingWhitespace.length);
	if (!trimmed.startsWith('/')) {
		return {draft, cursorOffset: draft.length};
	}
	let tokenEnd = 0;
	while (tokenEnd < trimmed.length && !/\s/.test(trimmed[tokenEnd]!)) {
		tokenEnd += 1;
	}
	const completedDraft = leadingWhitespace + hint + trimmed.slice(tokenEnd);
	return {
		draft: completedDraft,
		cursorOffset: leadingWhitespace.length + hint.length,
	};
}

export function getPromptExpandTriggerHitbox(cols: number, rows: number): TerminalHitbox {
	return {column: Math.max(1, cols - 4), row: Math.max(1, rows - 1), width: 2, height: 1};
}

export function getExpandedComposerSendHitbox(cols: number, rows: number): TerminalHitbox {
	return {column: Math.max(1, cols - 3), row: Math.max(1, rows - 1), width: 1, height: 1};
}

export function hitboxContainsPoint(hitbox: TerminalHitbox, column: number, row: number): boolean {
	return (
		column >= hitbox.column &&
		column < hitbox.column + hitbox.width &&
		row >= hitbox.row &&
		row < hitbox.row + hitbox.height
	);
}

function locateCursor(draft: string, cursorOffset: number): {
	lineIndex: number;
	lineStart: number;
	lineEnd: number;
	column: number;
	lineStarts: number[];
} {
	const safeOffset = Math.max(0, Math.min(draft.length, cursorOffset));
	const lineStarts = [0];
	for (let i = 0; i < draft.length; i += 1) {
		if (draft[i] === '\n') {
			lineStarts.push(i + 1);
		}
	}
	let lineIndex = 0;
	for (let i = 0; i < lineStarts.length; i += 1) {
		if (lineStarts[i]! <= safeOffset) {
			lineIndex = i;
		} else {
			break;
		}
	}
	const lineStart = lineStarts[lineIndex] ?? 0;
	const nextStart = lineIndex + 1 < lineStarts.length ? lineStarts[lineIndex + 1]! : draft.length + 1;
	const lineEnd = nextStart - 1;
	return {
		lineIndex,
		lineStart,
		lineEnd,
		column: safeOffset - lineStart,
		lineStarts,
	};
}

function buildExpandedComposerView(
	state: ExpandedComposerState,
	availableWidth: number,
	availableRows: number,
): {lines: ExpandedComposerViewLine[]; hiddenAbove: number; hiddenBelow: number} {
	const safeWidth = Math.max(1, availableWidth);
	const safeRows = Math.max(1, availableRows);
	const location = locateCursor(state.draft, state.cursorOffset);
	const allLines = state.draft.split('\n');
	const topLine = allLines.length <= safeRows
		? 0
		: Math.max(0, Math.min(location.lineIndex - Math.floor(safeRows / 2), allLines.length - safeRows));
	const visibleLines = allLines.slice(topLine, topLine + safeRows);
	const leftColumn = Math.max(0, location.column - safeWidth + 1);
	return {
		lines: visibleLines.map((line, index) => {
			const chars = [...line];
			const start = Math.min(leftColumn, chars.length);
			const end = Math.min(chars.length, start + safeWidth);
			const text = chars.slice(start, end).join('');
			const actualLineIndex = topLine + index;
			const cursorColumn = actualLineIndex === location.lineIndex ? location.column - leftColumn : null;
			return {
				key: `${actualLineIndex}:${start}:${end}`,
				text,
				cursorColumn,
			};
		}),
		hiddenAbove: topLine,
		hiddenBelow: Math.max(0, allLines.length - (topLine + visibleLines.length)),
	};
}

function renderExpandedComposerLine(line: ExpandedComposerViewLine): React.JSX.Element {
	if (line.cursorColumn == null) {
		return <Text>{line.text || ' '}</Text>;
	}

	const chars = [...line.text];
	const cursorIndex = Math.max(0, Math.min(line.cursorColumn, chars.length));
	const before = chars.slice(0, cursorIndex).join('');
	const cursor = cursorIndex < chars.length ? chars[cursorIndex] : ' ';
	const after = cursorIndex < chars.length ? chars.slice(cursorIndex + 1).join('') : '';

	return (
		<Text>
			{before}
			<Text inverse>{cursor}</Text>
			{after}
		</Text>
	);
}

export function ExpandedComposer({
	state,
	commandHints,
	subHintsByHint = {},
}: {
	state: ExpandedComposerState;
	commandHints: string[];
	subHintsByHint?: Record<string, string[]>;
}): React.JSX.Element {
	const {rows, cols} = useTerminalSize();
	const availableWidth = cols - BORDER_PADDING_WIDTH;
	const pickerRowBudget = commandHints.length > 0 ? Math.min(commandHints.length, 10) + 4 : 0;
	const view = useMemo(
		() => buildExpandedComposerView(state, availableWidth, rows - BORDER_PADDING_WIDTH - EDITOR_HEADER_ROWS - EDITOR_FOOTER_ROWS - pickerRowBudget),
		[state, availableWidth, rows, pickerRowBudget],
	);

	return (
		<Box flexDirection="column" height={rows} borderStyle="round" borderColor="cyan" paddingX={1} overflow="hidden">
			<Text color="cyan" bold>expanded editor</Text>
			{commandHints.length > 0 ? (
				<Box marginTop={1} flexDirection="column">
					<CommandPicker
						hints={commandHints}
						selectedIndex={0}
						title="Commands & Skills"
						subHintsByHint={subHintsByHint}
						footerLabel="tab complete"
						showEnterLabel={false}
					/>
				</Box>
			) : null}
			<Box marginTop={1} flexDirection="column" flexGrow={1}>
				{view.hiddenAbove > 0 ? <Text dimColor>{`... ${view.hiddenAbove} lines above`}</Text> : null}
				{view.lines.map((line) => (
					<Box key={line.key}>
						<Box flexGrow={1} flexShrink={1}>
							{renderExpandedComposerLine(line)}
						</Box>
					</Box>
				))}
				{view.hiddenBelow > 0 ? <Text dimColor>{`... ${view.hiddenBelow} lines below`}</Text> : null}
			</Box>
			<Box justifyContent="space-between" flexShrink={0}>
				<Text dimColor>enter/alt+enter newline · tab complete · esc close · click send</Text>
				<Text color="green" bold>{EXPANDED_SEND_SYMBOL}</Text>
			</Box>
		</Box>
	);
}
