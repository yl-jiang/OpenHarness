/**
 * A text input component with horizontal scrolling.
 *
 * Unlike `ink-text-input`, the displayed text never wraps to multiple lines.
 * When the text is wider than `availableWidth`, only a viewport window around
 * the cursor is rendered.  This prevents CJK/wide-character text from causing
 * border misalignment in Ink's output buffer.
 *
 * API is intentionally close to `ink-text-input` for drop-in replacement.
 */
import React, {useState, useEffect} from 'react';
import {Text, useInput} from 'ink';
import chalk from 'chalk';
import stringWidth from 'string-width';

export interface ScrollableTextInputProps {
	value: string;
	onChange: (value: string) => void;
	onSubmit?: (value: string) => void;
	focus?: boolean;
	placeholder?: string;
	/** Visual columns available for the text + cursor.  Must be ≥ 1. */
	availableWidth: number;
}

/** Split a string into an array of user-perceived characters (handles surrogate pairs). */
function toChars(s: string): string[] {
	return [...s];
}

/**
 * Compute which characters are visible in the viewport.
 *
 * Returns [startIdx, endIdx) — the slice of `chars` to render.
 * Strategy: keep the cursor at the RIGHT edge of the viewport so
 * the user always sees recently typed/pasted text.  Then fill any
 * remaining space with characters after the cursor.
 */
function computeViewport(
	chars: string[],
	widths: number[],
	cursorOffset: number,
	availableWidth: number,
): {startIdx: number; endIdx: number} {
	const totalWidth = widths.reduce((a, b) => a + b, 0);
	const cursorBlockWidth = cursorOffset >= chars.length ? 1 : 0;

	if (totalWidth + cursorBlockWidth <= availableWidth) {
		return {startIdx: 0, endIdx: chars.length};
	}

	// Start with the cursor char (or the end-block space)
	let usedWidth = cursorBlockWidth;
	if (cursorOffset < chars.length) {
		usedWidth += widths[cursorOffset]!;
	}

	// Walk backward from cursor to fill viewport
	let startIdx = cursorOffset;
	for (let i = cursorOffset - 1; i >= 0; i--) {
		if (usedWidth + widths[i]! > availableWidth) break;
		usedWidth += widths[i]!;
		startIdx = i;
	}

	// Walk forward past cursor to fill remaining space
	let endIdx = Math.min(cursorOffset + 1, chars.length);
	for (let i = endIdx; i < chars.length; i++) {
		if (usedWidth + widths[i]! > availableWidth) break;
		usedWidth += widths[i]!;
		endIdx = i + 1;
	}

	return {startIdx, endIdx};
}

/** Render visible chars with chalk.inverse cursor highlight. */
function renderViewport(
	chars: string[],
	startIdx: number,
	endIdx: number,
	cursorOffset: number,
	focus: boolean,
): string {
	let out = '';
	for (let i = startIdx; i < endIdx; i++) {
		out += focus && i === cursorOffset ? chalk.inverse(chars[i]) : chars[i];
	}
	// Cursor block at end of text
	if (focus && cursorOffset >= chars.length && endIdx === chars.length) {
		out += chalk.inverse(' ');
	}
	return out;
}

export default function ScrollableTextInput({
	value,
	onChange,
	onSubmit,
	focus = true,
	placeholder = '',
	availableWidth,
}: ScrollableTextInputProps): React.JSX.Element {
	const [cursorOffset, setCursorOffset] = useState(value.length);

	// Keep cursor within bounds when value changes externally
	useEffect(() => {
		setCursorOffset((prev) => Math.min(prev, value.length));
	}, [value]);

	useInput(
		(input, key) => {
			if (
				key.upArrow ||
				key.downArrow ||
				(key.ctrl && input === 'c') ||
				key.tab ||
				(key.shift && key.tab)
			) {
				return;
			}

			if (key.return) {
				onSubmit?.(value);
				return;
			}

			let nextOffset = cursorOffset;
			let nextValue = value;

			if (key.leftArrow) {
				nextOffset = Math.max(0, cursorOffset - 1);
			} else if (key.rightArrow) {
				nextOffset = Math.min(value.length, cursorOffset + 1);
			} else if (key.backspace || key.delete) {
				if (cursorOffset > 0) {
					nextValue =
						value.slice(0, cursorOffset - 1) + value.slice(cursorOffset);
					nextOffset = cursorOffset - 1;
				}
			} else {
				// Regular character or paste
				nextValue =
					value.slice(0, cursorOffset) + input + value.slice(cursorOffset);
				nextOffset = cursorOffset + input.length;
			}

			nextOffset = Math.max(0, Math.min(nextValue.length, nextOffset));
			setCursorOffset(nextOffset);

			if (nextValue !== value) {
				onChange(nextValue);
			}
		},
		{isActive: focus},
	);

	// --- Render ---

	const safeWidth = Math.max(1, availableWidth);

	// Empty value → show placeholder
	if (!value) {
		if (focus) {
			const ph =
				placeholder.length > 0
					? chalk.inverse(placeholder[0]) + chalk.grey(placeholder.slice(1))
					: chalk.inverse(' ');
			return <Text>{ph}</Text>;
		}
		return placeholder ? <Text>{chalk.grey(placeholder)}</Text> : <Text>{' '}</Text>;
	}

	const chars = toChars(value);
	const widths = chars.map((c) => stringWidth(c));
	const {startIdx, endIdx} = computeViewport(
		chars,
		widths,
		cursorOffset,
		safeWidth,
	);
	const rendered = renderViewport(chars, startIdx, endIdx, cursorOffset, focus);

	return <Text>{rendered}</Text>;
}
