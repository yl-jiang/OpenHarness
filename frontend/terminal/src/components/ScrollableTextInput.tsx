/**
 * A text input component with line-wrapping, inline syntax highlighting,
 * word-level navigation and line editing operations.
 *
 * When the text is wider than `availableWidth`, it wraps to additional lines
 * rather than scrolling horizontally.
 *
 * API is intentionally close to `ink-text-input` for drop-in replacement.
 */
import React, {useEffect, useRef, useState} from 'react';
import {Box, Text, useInput} from 'ink';
import stringWidth from 'string-width';

/** Regex to detect /commands and @file references for syntax highlighting. */
const HIGHLIGHT_REGEX = /^\/[a-zA-Z0-9_-]+|(?<![\\])@[^\s]+/g;

export interface ScrollableTextInputProps {
	value: string;
	onChange: (value: string) => void;
	onSubmit?: (value: string) => void;
	focus?: boolean;
	placeholder?: string;
	/** Visual columns available for the text + cursor.  Must be ≥ 1. */
	availableWidth: number;
	/** Accent color for syntax-highlighted tokens (/commands, @files). */
	accentColor?: string;
}

/** Split a string into an array of user-perceived characters (handles surrogate pairs). */
function toChars(s: string): string[] {
	return [...s];
}

/** Check if a character is a word character (alphanumeric or underscore). */
function isWordChar(c: string): boolean {
	return /[\w]/.test(c);
}

/** Find the offset of the previous word boundary. */
function prevWordBoundary(chars: string[], offset: number): number {
	let i = offset - 1;
	// Skip whitespace
	while (i > 0 && !isWordChar(chars[i]!)) i--;
	// Skip word characters
	while (i > 0 && isWordChar(chars[i - 1]!)) i--;
	return Math.max(0, i);
}

/** Find the offset of the next word boundary. */
function nextWordBoundary(chars: string[], offset: number): number {
	let i = offset;
	// Skip current word characters
	while (i < chars.length && isWordChar(chars[i]!)) i++;
	// Skip whitespace/non-word
	while (i < chars.length && !isWordChar(chars[i]!)) i++;
	return i;
}

/**
 * Tokenize input text for syntax highlighting.
 * Returns segments with type ('text' | 'command' | 'file') and content.
 */
function tokenize(text: string): Array<{type: 'text' | 'command' | 'file'; content: string}> {
	const segments: Array<{type: 'text' | 'command' | 'file'; content: string}> = [];
	let lastIndex = 0;

	for (const match of text.matchAll(HIGHLIGHT_REGEX)) {
		const start = match.index ?? 0;
		if (start > lastIndex) {
			segments.push({type: 'text', content: text.slice(lastIndex, start)});
		}
		const token = match[0];
		segments.push({
			type: token.startsWith('/') ? 'command' : 'file',
			content: token,
		});
		lastIndex = start + token.length;
	}
	if (lastIndex < text.length) {
		segments.push({type: 'text', content: text.slice(lastIndex)});
	}
	if (segments.length === 0 && text.length > 0) {
		segments.push({type: 'text', content: text});
	}
	return segments;
}

/**
 * Split the characters array into visual lines based on `availableWidth`.
 * Each line is represented as [startIdx, endIdx) into `chars`.
 */
function computeWrappedLines(
	chars: string[],
	widths: number[],
	availableWidth: number,
): Array<{startIdx: number; endIdx: number}> {
	if (chars.length === 0) {
		return [{startIdx: 0, endIdx: 0}];
	}
	const lines: Array<{startIdx: number; endIdx: number}> = [];
	let lineStart = 0;
	let lineWidth = 0;
	for (let i = 0; i < chars.length; i++) {
		const w = widths[i]!;
		if (lineWidth + w > availableWidth && lineWidth > 0) {
			lines.push({startIdx: lineStart, endIdx: i});
			lineStart = i;
			lineWidth = 0;
		}
		lineWidth += w;
	}
	lines.push({startIdx: lineStart, endIdx: chars.length});
	return lines;
}

/** A render segment with text content and optional styling. */
interface RenderSegment {
	text: string;
	color?: string;
	inverse?: boolean;
}

/**
 * Build an array of styled segments for the visible viewport.
 * Uses per-character color map from tokenization and cursor position.
 */
function buildViewportSegments(
	chars: string[],
	startIdx: number,
	endIdx: number,
	cursorOffset: number,
	focus: boolean,
	segments: Array<{type: 'text' | 'command' | 'file'; content: string}>,
	accentColor: string,
): RenderSegment[] {
	// Build a per-character color map from segments
	const colorMap: Array<string | undefined> = new Array(chars.length).fill(undefined) as Array<string | undefined>;
	let charIdx = 0;
	for (const seg of segments) {
		const segChars = toChars(seg.content);
		for (let j = 0; j < segChars.length && charIdx < chars.length; j++, charIdx++) {
			if (seg.type !== 'text') {
				colorMap[charIdx] = accentColor;
			}
		}
	}

	// Group consecutive chars with same styling
	const result: RenderSegment[] = [];
	let currentText = '';
	let currentColor: string | undefined = undefined;
	let currentInverse = false;

	for (let i = startIdx; i < endIdx; i++) {
		const char = chars[i]!;
		const color = colorMap[i];
		const inverse = focus && i === cursorOffset;

		if (color === currentColor && inverse === currentInverse) {
			currentText += char;
		} else {
			if (currentText) {
				result.push({text: currentText, color: currentColor, inverse: currentInverse});
			}
			currentText = char;
			currentColor = color;
			currentInverse = inverse;
		}
	}
	if (currentText) {
		result.push({text: currentText, color: currentColor, inverse: currentInverse});
	}

	// Cursor block at end of text
	if (focus && cursorOffset >= chars.length && endIdx === chars.length) {
		result.push({text: ' ', inverse: true});
	}

	return result;
}

export default function ScrollableTextInput({
	value,
	onChange,
	onSubmit,
	focus = true,
	placeholder = '',
	availableWidth,
	accentColor = '#bb9af7',
}: ScrollableTextInputProps): React.JSX.Element {
	const [cursorOffset, setCursorOffset] = useState(value.length);
	const [draftValue, setDraftValue] = useState(value);
	const cursorOffsetRef = useRef(value.length);
	const draftValueRef = useRef(value);
	const lastPropValueRef = useRef(value);

	const updateCursorOffset = (nextOffset: number): void => {
		cursorOffsetRef.current = nextOffset;
		setCursorOffset(nextOffset);
	};

	const updateDraftValue = (nextValue: string): void => {
		draftValueRef.current = nextValue;
		setDraftValue(nextValue);
	};

	// Keep the local buffer aligned with external changes while still allowing
	// multi-character input bursts to build on the latest local state before the
	// controlled parent value catches up.
	useEffect(() => {
		if (value === lastPropValueRef.current) {
			return;
		}
		lastPropValueRef.current = value;
		updateDraftValue(value);
		updateCursorOffset(Math.min(cursorOffsetRef.current, value.length));
	}, [value]);

	useInput(
		(input, key) => {
			if (
				key.upArrow ||
				key.downArrow ||
				key.tab ||
				(key.shift && key.tab)
			) {
				return;
			}

			if (key.return) {
				onSubmit?.(draftValueRef.current);
				return;
			}

			const currentOffset = cursorOffsetRef.current;
			const currentValue = draftValueRef.current;
			const chars = toChars(currentValue);
			let nextOffset = currentOffset;
			let nextValue = currentValue;

			// --- Word-level navigation ---
			if (key.ctrl && key.leftArrow) {
				nextOffset = prevWordBoundary(chars, currentOffset);
			} else if (key.ctrl && key.rightArrow) {
				nextOffset = nextWordBoundary(chars, currentOffset);
			// --- Home / End ---
			} else if (key.ctrl && input === 'a') {
				nextOffset = 0;
			} else if (key.ctrl && input === 'e') {
				nextOffset = currentValue.length;
			// --- Line editing ---
			} else if (key.ctrl && input === 'k') {
				// Kill to end of line
				nextValue = currentValue.slice(0, currentOffset);
				nextOffset = currentOffset;
			} else if (key.ctrl && input === 'u') {
				// Kill to start of line
				nextValue = currentValue.slice(currentOffset);
				nextOffset = 0;
			} else if (key.ctrl && input === 'w') {
				// Delete word backward
				const boundary = prevWordBoundary(chars, currentOffset);
				nextValue = currentValue.slice(0, boundary) + currentValue.slice(currentOffset);
				nextOffset = boundary;
			} else if (key.ctrl || key.meta) {
				return;
			} else if (key.leftArrow) {
				nextOffset = Math.max(0, currentOffset - 1);
			} else if (key.rightArrow) {
				nextOffset = Math.min(currentValue.length, currentOffset + 1);
			} else if (key.backspace || key.delete) {
				if (currentOffset > 0) {
					nextValue =
						currentValue.slice(0, currentOffset - 1) + currentValue.slice(currentOffset);
					nextOffset = currentOffset - 1;
				}
			} else {
				// Regular character or paste
				nextValue =
					currentValue.slice(0, currentOffset) + input + currentValue.slice(currentOffset);
				nextOffset = currentOffset + input.length;
			}

			nextOffset = Math.max(0, Math.min(nextValue.length, nextOffset));

			if (nextValue !== currentValue) {
				// When the new value contains a newline, the parent (App) is expected
				// to consume the segments before the final '\n' as buffered preview
				// lines and keep only the trailing segment as the live input.
				const newlineIndex = nextValue.lastIndexOf('\n');
				if (newlineIndex >= 0) {
					const trailing = nextValue.slice(newlineIndex + 1);
					updateDraftValue(trailing);
					updateCursorOffset(trailing.length);
				} else {
					updateDraftValue(nextValue);
					updateCursorOffset(nextOffset);
				}
				onChange(nextValue);
			} else {
				updateCursorOffset(nextOffset);
			}
		},
		{isActive: focus},
	);

	// --- Render ---

	const safeWidth = Math.max(1, availableWidth);

	// Empty value → show placeholder
	if (!draftValue) {
		if (focus) {
			return (
				<Text>
					<Text inverse>{placeholder.length > 0 ? placeholder[0] : ' '}</Text>
					{placeholder.length > 1 && <Text dimColor>{placeholder.slice(1)}</Text>}
				</Text>
			);
		}
		return placeholder ? <Text dimColor>{placeholder}</Text> : <Text>{' '}</Text>;
	}

	const chars = toChars(draftValue);
	const widths = chars.map((c) => stringWidth(c));
	const wrappedLines = computeWrappedLines(chars, widths, safeWidth);
	const tokenized = tokenize(draftValue);

	return (
		<Box flexDirection="column">
			{wrappedLines.map((line, lineIdx) => {
				const segments = buildViewportSegments(
					chars,
					line.startIdx,
					line.endIdx,
					cursorOffset,
					focus,
					tokenized,
					accentColor,
				);
				return (
					<Text key={lineIdx}>
						{segments.map((seg, i) => (
							<Text key={i} color={seg.color} inverse={seg.inverse}>{seg.text}</Text>
						))}
					</Text>
				);
			})}
		</Box>
	);
}
