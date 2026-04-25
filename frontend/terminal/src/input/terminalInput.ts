import {PassThrough} from 'node:stream';

export type TerminalMouseEvent =
	| {
		kind: 'wheel';
		direction: 'up' | 'down';
		buttonCode: number;
	}
	| {
		kind: 'button';
		action: 'press' | 'release';
		buttonCode: number;
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

const COMPLETE_MOUSE_SEQUENCE = /^\u001b\[<(\d+);\d+;\d+([Mm])/;
const PARTIAL_MOUSE_SEQUENCE = /^\u001b\[<[\d;]*$/;

export function createTerminalInputDecoder(): TerminalInputDecoder {
	let pending = '';

	return {
		push(chunk: string): DecodedTerminalInput {
			const input = pending + chunk;
			const mouseEvents: TerminalMouseEvent[] = [];
			let text = '';
			let cursor = 0;
			pending = '';

			while (cursor < input.length) {
				const escapeIndex = input.indexOf('\u001b[<', cursor);
				if (escapeIndex === -1) {
					text += input.slice(cursor);
					break;
				}

				text += input.slice(cursor, escapeIndex);
				const remainder = input.slice(escapeIndex);
				const match = COMPLETE_MOUSE_SEQUENCE.exec(remainder);
				if (match) {
					const buttonCode = Number(match[1]);
					const terminator = match[2];
					const mouseEvent = toMouseEvent(buttonCode, terminator);
					if (mouseEvent) {
						mouseEvents.push(mouseEvent);
					}
					cursor = escapeIndex + match[0].length;
					continue;
				}

				if (PARTIAL_MOUSE_SEQUENCE.test(remainder)) {
					pending = remainder;
					cursor = input.length;
					continue;
				}

				text += input.slice(escapeIndex, escapeIndex + 1);
				cursor = escapeIndex + 1;
			}

			return {text, mouseEvents};
		},
		flush(): DecodedTerminalInput {
			const text = pending;
			pending = '';
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
			stream.write(decoded.text);
		}
		for (const mouseEvent of decoded.mouseEvents) {
			stream.emit('mouse', mouseEvent);
		}
	};

	const handleEnd = (): void => {
		const decoded = decoder.flush();
		if (decoded.text) {
			stream.write(decoded.text);
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

function toMouseEvent(buttonCode: number, terminator: string): TerminalMouseEvent | null {
	if (buttonCode === 64) {
		return {kind: 'wheel', direction: 'up', buttonCode};
	}
	if (buttonCode === 65) {
		return {kind: 'wheel', direction: 'down', buttonCode};
	}
	return {
		kind: 'button',
		action: terminator === 'm' ? 'release' : 'press',
		buttonCode,
	};
}
