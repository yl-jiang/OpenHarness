import {useCallback, useRef, useState} from 'react';
import {useStdout} from 'ink';

import type {TerminalMouseEvent} from '../input/terminalInput.js';
import {getScreenLines} from '../syncOutput.js';

export type SelectionRange = {
	startRow: number;
	startCol: number;
	endRow: number;
	endCol: number;
};

export type CopyModeState = {
	active: boolean;
	selection: SelectionRange | null;
	selecting: boolean;
	copiedNotice: string | null;
};

// ANSI escape for reverse video (selection highlight)
const REVERSE_ON = '\x1b[7m';
const REVERSE_OFF = '\x1b[27m';
const SAVE_CURSOR = '\x1b[s';
const RESTORE_CURSOR = '\x1b[u';

// OSC 52 clipboard set (base64 encoded)
function osc52Copy(text: string): string {
	const encoded = Buffer.from(text, 'utf-8').toString('base64');
	return `\x1b]52;c;${encoded}\x07`;
}

function normalizeRange(sel: SelectionRange): SelectionRange {
	if (sel.startRow > sel.endRow || (sel.startRow === sel.endRow && sel.startCol > sel.endCol)) {
		return {startRow: sel.endRow, startCol: sel.endCol, endRow: sel.startRow, endCol: sel.startCol};
	}
	return sel;
}

/**
 * Extracts plain text from the screen buffer within the given selection range.
 */
function extractSelectedText(sel: SelectionRange): string {
	const lines = getScreenLines();
	const {startRow, startCol, endRow, endCol} = normalizeRange(sel);
	const result: string[] = [];

	for (let row = startRow; row <= endRow && row <= lines.length; row++) {
		const line = lines[row - 1] ?? '';
		if (startRow === endRow) {
			result.push(line.slice(startCol - 1, endCol));
		} else if (row === startRow) {
			result.push(line.slice(startCol - 1));
		} else if (row === endRow) {
			result.push(line.slice(0, endCol));
		} else {
			result.push(line);
		}
	}

	return result.join('\n');
}

/**
 * Renders selection highlight by writing ANSI escape sequences directly to stdout.
 * This overlays inverse video on the selected rows without interfering with Ink's render.
 */
function renderSelectionHighlight(
	stdout: NodeJS.WriteStream,
	sel: SelectionRange,
	cols: number,
): void {
	const lines = getScreenLines();
	const {startRow, startCol, endRow, endCol} = normalizeRange(sel);
	let output = SAVE_CURSOR;

	for (let row = startRow; row <= endRow && row <= lines.length; row++) {
		const line = lines[row - 1] ?? '';
		let colStart: number;
		let colEnd: number;

		if (startRow === endRow) {
			colStart = startCol;
			colEnd = endCol;
		} else if (row === startRow) {
			colStart = startCol;
			colEnd = line.length + 1;
		} else if (row === endRow) {
			colStart = 1;
			colEnd = endCol;
		} else {
			colStart = 1;
			colEnd = line.length + 1;
		}

		const text = line.slice(colStart - 1, colEnd - 1) || ' ';
		// Move cursor to position and write reverse-highlighted text
		output += `\x1b[${row};${colStart}H${REVERSE_ON}${text}${REVERSE_OFF}`;
	}

	output += RESTORE_CURSOR;
	stdout.write(output);
}

export function useCopyMode(
	scrollUp: (lines: number) => void,
	scrollDown: (lines: number) => void,
	cols: number,
): {
	state: CopyModeState;
	toggle: () => void;
	handleMouseEvent: (event: TerminalMouseEvent) => void;
	renderHighlight: () => void;
	clearNotice: () => void;
} {
	const [active, setActive] = useState(false);
	const [selection, setSelection] = useState<SelectionRange | null>(null);
	const [selecting, setSelecting] = useState(false);
	const [copiedNotice, setCopiedNotice] = useState<string | null>(null);
	const selectingRef = useRef(false);
	const selectionRef = useRef<SelectionRange | null>(null);
	const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const {stdout} = useStdout();

	const toggle = useCallback(() => {
		setActive((prev) => {
			if (prev) {
				// Exiting copy mode — clear selection
				setSelection(null);
				setSelecting(false);
				selectingRef.current = false;
				selectionRef.current = null;
				setCopiedNotice(null);
			}
			return !prev;
		});
	}, []);

	const copyToClipboard = useCallback((text: string) => {
		if (!stdout || !text.trim()) return;
		// Use OSC 52 to set clipboard (widely supported in modern terminals)
		stdout.write(osc52Copy(text));
		const lineCount = text.split('\n').length;
		setCopiedNotice(`Copied ${lineCount} line${lineCount > 1 ? 's' : ''}`);
		if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
		noticeTimerRef.current = setTimeout(() => setCopiedNotice(null), 3000);
	}, [stdout]);

	const handleMouseEvent = useCallback((event: TerminalMouseEvent) => {
		if (!active) return;

		if (event.kind === 'wheel') {
			const delta = event.direction === 'up' ? -1 : 1;
			const step = 3;
			if (delta < 0) scrollUp(step);
			else scrollDown(step);

			// Extend selection while scrolling if button is held
			if (selectingRef.current && selectionRef.current) {
				const updated = {
					...selectionRef.current,
					endRow: event.row,
					endCol: event.column,
				};
				selectionRef.current = updated;
				setSelection(updated);
			}
			return;
		}

		if (event.kind === 'button') {
			// Left button press (buttonCode 0) starts selection
			if (event.action === 'press' && event.buttonCode === 0) {
				const newSel: SelectionRange = {
					startRow: event.row,
					startCol: event.column,
					endRow: event.row,
					endCol: event.column,
				};
				setSelection(newSel);
				setSelecting(true);
				selectingRef.current = true;
				selectionRef.current = newSel;
				setCopiedNotice(null);
				return;
			}

			// Left button release ends selection and copies
			if (event.action === 'release' && event.buttonCode === 0) {
				if (selectingRef.current && selectionRef.current) {
					const finalSel = {
						...selectionRef.current,
						endRow: event.row,
						endCol: event.column,
					};
					setSelection(finalSel);
					selectionRef.current = finalSel;
					setSelecting(false);
					selectingRef.current = false;

					const text = extractSelectedText(finalSel);
					if (text.trim()) {
						copyToClipboard(text);
					}
				}
				return;
			}
		}

		if (event.kind === 'drag') {
			// Update selection end during drag
			if (selectingRef.current && selectionRef.current) {
				const updated = {
					...selectionRef.current,
					endRow: event.row,
					endCol: event.column,
				};
				selectionRef.current = updated;
				setSelection(updated);
			}
			return;
		}
	}, [active, copyToClipboard, scrollDown, scrollUp]);

	const renderHighlight = useCallback(() => {
		if (!active || !stdout || !selectionRef.current) return;
		const sel = selectionRef.current;
		// Only render if there's a meaningful selection (not just a single point)
		const norm = normalizeRange(sel);
		if (norm.startRow === norm.endRow && norm.startCol === norm.endCol) return;
		renderSelectionHighlight(stdout, sel, cols);
	}, [active, cols, stdout]);

	const clearNotice = useCallback(() => {
		setCopiedNotice(null);
	}, []);

	return {
		state: {active, selection, selecting, copiedNotice},
		toggle,
		handleMouseEvent,
		renderHighlight,
		clearNotice,
	};
}
