import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import {ThemeProvider} from '../theme/ThemeContext.js';
import {PromptInput} from './PromptInput.js';

const stripAnsi = (value: string): string => value.replace(/\u001B\[[0-9;?]*[ -/]*[@-~]/g, '');
const nextLoopTurn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));

type InkTestStdout = PassThrough & {
	isTTY: boolean;
	columns: number;
	rows: number;
	cursorTo: () => boolean;
	clearLine: () => boolean;
	moveCursor: () => boolean;
};

type InkTestStdin = PassThrough & {
	isTTY: boolean;
	isRaw: boolean;
	setRawMode: (value: boolean) => InkTestStdin;
	resume: () => InkTestStdin;
	pause: () => InkTestStdin;
	ref: () => InkTestStdin;
	unref: () => InkTestStdin;
};

function createTestStdout(): InkTestStdout {
	return Object.assign(new PassThrough(), {
		isTTY: true,
		columns: 120,
		rows: 40,
		cursorTo: () => true,
		clearLine: () => true,
		moveCursor: () => true,
	});
}

function createTestStdin(): InkTestStdin {
	return Object.assign(new PassThrough(), {
		isTTY: true,
		isRaw: false,
		setRawMode(value: boolean) {
			this.isRaw = value;
			return this;
		},
		resume() {
			return this;
		},
		pause() {
			return this;
		},
		ref() {
			return this;
		},
		unref() {
			return this;
		},
	});
}

async function waitForOutputToStabilize(getOutput: () => string): Promise<string> {
	let previous = '';
	let sawOutput = false;

	for (let i = 0; i < 50; i++) {
		await nextLoopTurn();
		const current = getOutput();
		sawOutput ||= current.length > 0;
		if (sawOutput && current === previous) {
			return current;
		}

		previous = current;
	}

	throw new Error(`Ink output did not stabilize: ${JSON.stringify(previous)}`);
}

async function renderPromptInput({
	busy = false,
	toolName,
}: {
	busy?: boolean;
	toolName?: string;
} = {}): Promise<string> {
	const stdout = createTestStdout();
	const stdin = createTestStdin();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = render(
		<ThemeProvider initialTheme="default">
			<PromptInput
				busy={busy}
				input=""
				setInput={() => {}}
				onSubmit={() => {}}
				toolName={toolName}
			/>
		</ThemeProvider>,
		{
			stdout: stdout as unknown as NodeJS.WriteStream,
			stdin: stdin as unknown as NodeJS.ReadStream,
			debug: true,
			patchConsole: false,
		},
	);

	const exitPromise = instance.waitUntilExit();
	const stableOutput = await waitForOutputToStabilize(() => output);
	instance.unmount();
	await exitPromise;
	instance.cleanup();

	return stripAnsi(stableOutput);
}

test('uses ascii-only idle title cues above the prompt input', async () => {
	const output = await renderPromptInput();

	assert.doesNotMatch(output, /\bPrompt\b/);
	assert.doesNotMatch(output, /\bReady\b/);
	assert.match(output, />> \| \[idle\]/);
	assert.doesNotMatch(output, /[⌨️⏳●⏎›]/u);
});

test('shows an animated busy indicator with the running tool name', async () => {
	const output = await renderPromptInput({busy: true, toolName: 'bash'});

	// Busy state replaces the static ">>" cue with a braille spinner frame
	// and renders the tool label with a trailing animated ellipsis.
	assert.match(output, /\[run\] bash/);
	assert.match(output, /[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/u);
	assert.doesNotMatch(output, />> \| \[run\]/);
});
