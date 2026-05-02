import {PassThrough} from 'node:stream';

export type TerminalMouseEvent =
	| {
		kind: 'wheel';
		direction: 'up' | 'down';
		buttonCode: number;
		column: number;
		row: number;
	}
	| {
		kind: 'button';
		action: 'press' | 'release';
		buttonCode: number;
		column: number;
		row: number;
	};

type DecodedTerminalInput = {
	text: string;
	mouseEvents: TerminalMouseEvent[];
};

type TerminalInputDecoder = {
	push: (chunk: string) => DecodedTerminalInput;
	flush: () => DecodedTerminalInput;
};

type MouseStreamEvents = {
	on(event: 'mouse', listener: (event: TerminalMouseEvent) => void): TerminalInputStream;
	off(event: 'mouse', listener: (event: TerminalMouseEvent) => void): TerminalInputStream;
	emit(event: 'mouse', payload: TerminalMouseEvent): boolean;
};

export type TerminalInputStream = PassThrough & MouseStreamEvents & {
	fd?: number;
	isTTY?: boolean;
	setRawMode?: (mode: boolean) => void;
	ref?: () => void;
	unref?: () => void;
};

const COMPLETE_MOUSE_SEQUENCE = /^\u001b\[<(\d+);(\d+);(\d+)([Mm])/;
const PARTIAL_MOUSE_SEQUENCE = /^\u001b\[<[\d;]*$/;
const BACKSPACE_CONTROL_PATTERN = /[\b\u007f]+/g;

// Sequences emitted by various terminals for Shift+Enter / Alt+Enter and
// Shift-modified printable keys.
// Shift+Enter is normalised to a literal LF so the higher-level input handler
// can treat it uniformly as "insert newline" instead of "submit".
//   - \x1b[27;<modifier>;13~  : xterm modifyOtherKeys mode 2 (Shift+Enter -> mod=2)
//   - \x1b[13;<modifier>u     : kitty keyboard protocol
//   - \x1b\r                  : Alt/Option+Enter on most macOS terminals
const COMPLETE_NEWLINE_SEQUENCE = /^\u001b\[(?:27;(\d+);13~|13;(\d+)u)/;
const COMPLETE_PRINTABLE_KEY_SEQUENCE = /^\u001b\[(?:27;(\d+);(\d+)~|(\d+);(\d+)u)/;
const PARTIAL_KEY_SEQUENCE = /^\u001b\[(?:27(?:;\d*)?(?:;\d*)?|(?:\d+)(?:;\d*)?)?$/;
const ALT_ENTER_SEQUENCE = '\u001b\r';
const BRACKETED_PASTE_START = '\u001b[200~';
const BRACKETED_PASTE_END = '\u001b[201~';

export function chunkTerminalTextForInk(text: string): string[] {
	if (!text) {
		return [];
	}

	const chunks: string[] = [];
	let cursor = 0;
	for (const match of text.matchAll(BACKSPACE_CONTROL_PATTERN)) {
		const start = match.index ?? 0;
		if (start > cursor) {
			chunks.push(text.slice(cursor, start));
		}
		for (const char of match[0]) {
			chunks.push(char === '\u007f' ? '\b' : char);
		}
		cursor = start + match[0].length;
	}
	if (cursor < text.length) {
		chunks.push(text.slice(cursor));
	}
	return chunks;
}

export function createTerminalInputDecoder(): TerminalInputDecoder {
	let pending = '';
	let inBracketedPaste = false;
	// Buffers the body of an in-progress bracketed paste.  Real terminals can
	// split one paste across many stdin data events; emitting partial paste
	// content would race the React composer's newline handling and cause
	// duplicated/dropped lines.  Keep the entire paste hidden until the end
	// marker arrives, then deliver it as one logical input event.
	let pasteBuffer = '';

	const normalisePasteLineEndings = (value: string): string =>
		value.replace(/\r\n?/g, '\n');

	return {
		push(chunk: string): DecodedTerminalInput {
			const input = pending + chunk;
			const mouseEvents: TerminalMouseEvent[] = [];
			let text = '';
			let cursor = 0;
			pending = '';

			while (cursor < input.length) {
				if (inBracketedPaste) {
					const endIndex = input.indexOf(BRACKETED_PASTE_END, cursor);
					if (endIndex === -1) {
						const {text: completeText, pendingText} = splitIncompleteSuffix(
							input.slice(cursor),
							BRACKETED_PASTE_END,
						);
						pasteBuffer += completeText;
						pending = pendingText;
						cursor = input.length;
						continue;
					}
					pasteBuffer += input.slice(cursor, endIndex);
					text += normalisePasteLineEndings(pasteBuffer);
					pasteBuffer = '';
					cursor = endIndex + BRACKETED_PASTE_END.length;
					inBracketedPaste = false;
					continue;
				}

				const escapeIndex = input.indexOf('\u001b[', cursor);
				const altEnterIndex = input.indexOf(ALT_ENTER_SEQUENCE, cursor);
				const nextEscape = pickFirstIndex(escapeIndex, altEnterIndex);
				if (nextEscape === -1) {
					text += input.slice(cursor);
					break;
				}

				text += input.slice(cursor, nextEscape);
				const remainder = input.slice(nextEscape);

				// Alt/Option+Enter -> normalise to LF.
				if (remainder.startsWith(ALT_ENTER_SEQUENCE)) {
					text += '\n';
					cursor = nextEscape + ALT_ENTER_SEQUENCE.length;
					continue;
				}

				if (remainder.startsWith(BRACKETED_PASTE_START)) {
					inBracketedPaste = true;
					cursor = nextEscape + BRACKETED_PASTE_START.length;
					continue;
				}
				if (remainder.startsWith(BRACKETED_PASTE_END)) {
					cursor = nextEscape + BRACKETED_PASTE_END.length;
					continue;
				}
				if (
					isIncompletePrefix(remainder, BRACKETED_PASTE_START) ||
					isIncompletePrefix(remainder, BRACKETED_PASTE_END)
				) {
					pending = remainder;
					cursor = input.length;
					continue;
				}

				// Mouse events.
				if (remainder.startsWith('\u001b[<')) {
					const match = COMPLETE_MOUSE_SEQUENCE.exec(remainder);
					if (match) {
						const buttonCode = Number(match[1]);
						const column = Number(match[2]);
						const row = Number(match[3]);
						const terminator = match[4];
						const mouseEvent = toMouseEvent(buttonCode, terminator, column, row);
						if (mouseEvent) {
							mouseEvents.push(mouseEvent);
						}
						cursor = nextEscape + match[0].length;
						continue;
					}
					if (PARTIAL_MOUSE_SEQUENCE.test(remainder)) {
						pending = remainder;
						cursor = input.length;
						continue;
					}
					text += input.slice(nextEscape, nextEscape + 1);
					cursor = nextEscape + 1;
					continue;
				}

				// modifyOtherKeys / kitty Shift+Enter sequences -> LF when shift bit set.
				const newlineMatch = COMPLETE_NEWLINE_SEQUENCE.exec(remainder);
				if (newlineMatch) {
					const modifier = Number(newlineMatch[1] ?? newlineMatch[2] ?? '1');
					// modifier is 1 + bitfield (shift=1, alt=2, ctrl=4); only consume
					// when shift (or any modifier) is present so plain Enter still submits.
					if (modifier > 1) {
						text += '\n';
					} else {
						text += '\r';
					}
					cursor = nextEscape + newlineMatch[0].length;
					continue;
				}
				const printableKeyMatch = COMPLETE_PRINTABLE_KEY_SEQUENCE.exec(remainder);
				if (printableKeyMatch) {
					const modifier = Number(printableKeyMatch[1] ?? printableKeyMatch[4] ?? '1');
					const codepoint = Number(printableKeyMatch[2] ?? printableKeyMatch[3] ?? '0');
					const shiftedOnly = modifier === 2;
					if (shiftedOnly && isPrintableCodepoint(codepoint)) {
						text += String.fromCodePoint(codepoint);
						cursor = nextEscape + printableKeyMatch[0].length;
						continue;
					}
				}
				if (PARTIAL_KEY_SEQUENCE.test(remainder)) {
					pending = remainder;
					cursor = input.length;
					continue;
				}

				text += input.slice(nextEscape, nextEscape + 1);
				cursor = nextEscape + 1;
			}

			return {text, mouseEvents};
		},
		flush(): DecodedTerminalInput {
			let text = pending;
			pending = '';
			if (pasteBuffer) {
				text += normalisePasteLineEndings(pasteBuffer);
				pasteBuffer = '';
				inBracketedPaste = false;
			}
			return {text, mouseEvents: []};
		},
	};
}

export function createTerminalInputStream(
	source: NodeJS.ReadStream & {
		fd?: number;
		isTTY?: boolean;
		setRawMode?: (mode: boolean) => void;
		ref?: () => void;
		unref?: () => void;
	},
): TerminalInputStream {
	const stream = new PassThrough() as TerminalInputStream;
	const decoder = createTerminalInputDecoder();

	const handleData = (chunk: string | Buffer): void => {
		const decoded = decoder.push(chunk.toString());
		if (decoded.text) {
			for (const textChunk of chunkTerminalTextForInk(decoded.text)) {
				stream.write(textChunk);
			}
		}
		for (const mouseEvent of decoded.mouseEvents) {
			stream.emit('mouse', mouseEvent);
		}
	};

	const handleEnd = (): void => {
		const decoded = decoder.flush();
		if (decoded.text) {
			for (const textChunk of chunkTerminalTextForInk(decoded.text)) {
				stream.write(textChunk);
			}
		}
		stream.end();
	};

	const handleError = (error: Error): void => {
		stream.destroy(error);
	};

	const cleanup = (): void => {
		source.off('data', handleData);
		source.off('end', handleEnd);
		source.off('error', handleError);
	};

	source.on('data', handleData);
	source.on('end', handleEnd);
	source.on('error', handleError);

	const originalDestroy = stream.destroy.bind(stream);
	stream.destroy = ((error?: Error) => {
		cleanup();
		return originalDestroy(error);
	}) as typeof stream.destroy;
	stream.on('close', cleanup);

	stream.pause = (() => {
		source.pause();
		return PassThrough.prototype.pause.call(stream);
	}) as typeof stream.pause;
	stream.resume = (() => {
		source.resume();
		return PassThrough.prototype.resume.call(stream);
	}) as typeof stream.resume;

	stream.isTTY = source.isTTY;
	stream.fd = source.fd;
	if (typeof source.setRawMode === 'function') {
		stream.setRawMode = source.setRawMode.bind(source);
	}
	if (typeof source.ref === 'function') {
		stream.ref = source.ref.bind(source);
	}
	if (typeof source.unref === 'function') {
		stream.unref = source.unref.bind(source);
	}

	return stream;
}

function pickFirstIndex(a: number, b: number): number {
	if (a === -1) return b;
	if (b === -1) return a;
	return Math.min(a, b);
}

function isIncompletePrefix(value: string, target: string): boolean {
	return value.length < target.length && target.startsWith(value);
}

function splitIncompleteSuffix(
	value: string,
	target: string,
): {text: string; pendingText: string} {
	for (let length = Math.min(value.length, target.length - 1); length > 0; length--) {
		const suffix = value.slice(-length);
		if (target.startsWith(suffix)) {
			return {
				text: value.slice(0, -length),
				pendingText: suffix,
			};
		}
	}
	return {text: value, pendingText: ''};
}

function isPrintableCodepoint(codepoint: number): boolean {
	return Number.isInteger(codepoint) && codepoint >= 0x20 && codepoint <= 0x10FFFF;
}

function toMouseEvent(buttonCode: number, terminator: string, column: number, row: number): TerminalMouseEvent | null {
	if (buttonCode === 64) {
		return {kind: 'wheel', direction: 'up', buttonCode, column, row};
	}
	if (buttonCode === 65) {
		return {kind: 'wheel', direction: 'down', buttonCode, column, row};
	}
	return {
		kind: 'button',
		action: terminator === 'm' ? 'release' : 'press',
		buttonCode,
		column,
		row,
	};
}
