import React from 'react';
import {render} from 'ink';
import fs from 'node:fs';
import tty from 'node:tty';

import {App} from './App.js';
import {createTerminalInputStream} from './input/terminalInput.js';
import type {FrontendConfig} from './types.js';

// Guard against EIO crashes in both stdin reads and setRawMode calls.
// Ink's React reconciler calls setRawMode during mount/unmount which can
// throw EIO in certain terminal environments (SSH, tmux, Docker).
process.stdin.on('error', (err: NodeJS.ErrnoException) => {
	if (err.code === 'EIO' || err.code === 'EAGAIN') {
		process.exit(1);
	}
	throw err;
});

if (process.stdin.isTTY && typeof process.stdin.setRawMode === 'function') {
	const origSetRawMode = process.stdin.setRawMode.bind(process.stdin);
	process.stdin.setRawMode = (mode: boolean) => {
		try {
			return origSetRawMode(mode);
		} catch (err: any) {
			if (err?.code === 'EIO' || err?.code === 'EAGAIN') {
				process.exit(1);
			}
			throw err;
		}
	};
}

process.on('uncaughtException', (err: NodeJS.ErrnoException) => {
	if (err.code === 'EIO' || err.code === 'EAGAIN') {
		process.exit(1);
	}
	throw err;
});

const config = JSON.parse(process.env.OPENHARNESS_FRONTEND_CONFIG ?? '{}') as FrontendConfig;

// Restore terminal cursor visibility on exit (Ink hides it by default).
// Also write a newline so the shell prompt starts on a fresh line and does
// not run into the last line of the TUI output. Disable xterm
// modifyOtherKeys mode and bracketed paste so the host terminal returns
// to its default key reporting behaviour.
const restoreTerminal = (): void => {
	process.stdout.write('\x1b[>4;0m\x1b[?2004l\x1B[?25h\n');
};
// Enable xterm modifyOtherKeys mode 2 so terminals that support it report
// Shift+Enter as a distinct escape sequence (\x1b[27;2;13~) instead of a
// plain CR; the input decoder normalises that to LF for the multi-line
// composer.  Also enable bracketed paste mode so the decoder can buffer an
// entire pasted block and deliver it as a single logical input event,
// preventing pasted line endings (CR) from being mistaken for Enter and
// avoiding the multi-burst races that previously dropped or duplicated
// pasted lines in the composer.
if (process.stdout.isTTY) {
	process.stdout.write('\x1b[>4;2m\x1b[?2004h');
}
process.on('exit', restoreTerminal);
process.on('SIGINT', () => {
	restoreTerminal();
	process.exit(130);
});
process.on('SIGTERM', () => {
	restoreTerminal();
	process.exit(143);
});

// On WSL / Windows the process-spawning chain (npm exec → tsx → node) can
// lose the TTY on stdin, which prevents Ink's useInput from enabling raw mode.
// When that happens, open /dev/tty directly to get a real TTY stream.
let stdinStream: NodeJS.ReadStream & {fd: 0} = process.stdin;
let ttyFd: number | undefined;

if (!process.stdin.isTTY) {
	try {
		ttyFd = fs.openSync('/dev/tty', 'r');
		const ttyStream = new tty.ReadStream(ttyFd);
		// Cast is safe — tty.ReadStream is a full readable TTY stream
		stdinStream = ttyStream as unknown as NodeJS.ReadStream & {fd: 0};
	} catch {
		// /dev/tty unavailable (e.g. non-interactive CI) — fall back to process.stdin
	}
}

process.on('exit', () => {
	if (ttyFd !== undefined) {
		try { fs.closeSync(ttyFd); } catch { /* ignore */ }
	}
});

render(<App config={config} />, {
	stdin: createTerminalInputStream(stdinStream) as unknown as NodeJS.ReadStream & {fd: 0},
	exitOnCtrlC: false,
});
