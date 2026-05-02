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
import React, {useEffect, useRef, useState} from 'react';
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
				(key.ctrl && input === 'c') ||
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
			let nextOffset = currentOffset;
			let nextValue = currentValue;

			if (key.leftArrow) {
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
				// lines and keep only the trailing segment as the live input.  We
				// mirror that behaviour locally so that subsequent input events
				// — which often arrive faster than React can flush the parent state
				// back through the controlled `value` prop — build on the post-\n
				// segment instead of replaying the already-consumed prefix and
				// duplicating buffered lines.
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
			const ph =
				placeholder.length > 0
					? chalk.inverse(placeholder[0]) + chalk.grey(placeholder.slice(1))
					: chalk.inverse(' ');
			return <Text>{ph}</Text>;
		}
		return placeholder ? <Text>{chalk.grey(placeholder)}</Text> : <Text>{' '}</Text>;
	}

	const chars = toChars(draftValue);
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
