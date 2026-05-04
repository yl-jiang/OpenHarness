import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React, {useCallback, useState} from 'react';
import {render} from 'ink';

import {ThemeProvider} from '../theme/ThemeContext.js';
import {PromptInput, shouldAnimateBackgroundCue, shouldAnimateSpinner} from './PromptInput.js';

const stripAnsi = (value: string): string => value.replace(/\u001B\[[0-9;?]*[ -/]*[@-~]/g, '');
const nextLoopTurn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));
const extractLastFrame = (output: string): string => {
	const boundary = output.lastIndexOf('\n╭');
	return boundary >= 0 ? output.slice(boundary + 1) : output;
};

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

function createTestStdout(columns = 120): InkTestStdout {
	return Object.assign(new PassThrough(), {
		isTTY: true,
		columns,
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
	backgroundTaskCount = 0,
	animateSpinner,
	stdoutColumns,
}: {
	busy?: boolean;
	toolName?: string;
	backgroundTaskCount?: number;
	animateSpinner?: boolean;
	stdoutColumns?: number;
} = {}): Promise<string> {
	const stdout = createTestStdout(stdoutColumns);
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
				backgroundTaskCount={backgroundTaskCount}
				animateSpinner={animateSpinner}
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

async function renderInteractivePromptInput({
	initialInput = '',
	initialExtraInputLines = [],
	stdoutColumns = 120,
}: {
	initialInput?: string;
	initialExtraInputLines?: string[];
	stdoutColumns?: number;
} = {}): Promise<{
	stdin: InkTestStdin;
	getOutput: () => Promise<string>;
	cleanup: () => Promise<void>;
}> {
	const stdout = createTestStdout(stdoutColumns);
	const stdin = createTestStdin();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	function Host(): React.JSX.Element {
		const [input, setInput] = useState(initialInput);
		const [extraInputLines, setExtraInputLines] = useState(initialExtraInputLines);
		const handleInputChange = useCallback((value: string) => {
			if (!value.includes('\n')) {
				setInput(value);
				return;
			}
			const parts = value.split('\n');
			const lastPart = parts.pop() ?? '';
			setExtraInputLines((prev) => [...prev, ...parts]);
			setInput(lastPart);
		}, []);

		return (
			<ThemeProvider initialTheme="default">
				<PromptInput
					busy={false}
					input={input}
					setInput={handleInputChange}
					onSubmit={() => {}}
					extraInputLines={extraInputLines}
				/>
			</ThemeProvider>
		);
	}

	const instance = render(<Host />, {
		stdout: stdout as unknown as NodeJS.WriteStream,
		stdin: stdin as unknown as NodeJS.ReadStream,
		debug: true,
		patchConsole: false,
	});

	await waitForOutputToStabilize(() => output);

	return {
		stdin,
		getOutput: async () => stripAnsi(await waitForOutputToStabilize(() => output)),
		cleanup: async () => {
			const exitPromise = instance.waitUntilExit();
			instance.unmount();
			await exitPromise;
			instance.cleanup();
		},
	};
}

test('uses ascii-only idle title cues above the prompt input', async () => {
	const output = await renderPromptInput();

	assert.doesNotMatch(output, /\bPrompt\b/);
	assert.doesNotMatch(output, /\bReady\b/);
	assert.match(output, /[◇◈◆] {2}\| ready/);
	assert.match(output, /⇖⇘/u);
	assert.doesNotMatch(output, /↖↘/u);
	assert.doesNotMatch(output, /◢/u);
	assert.doesNotMatch(output, /[⌨️⏳●⏎›]/u);
});

test('shows a concise idle shortcut footer', async () => {
	const output = await renderPromptInput();

	assert.match(output, /\/ commands · @ files · ↑↓ history · shift\/alt\+enter newline/);
	assert.doesNotMatch(output, /wheel\/PgUp scroll/);
	assert.doesNotMatch(output, /ctrl\+c ctrl\+c exit/);
});

test('shows an animated busy indicator with the running tool name', async () => {
	const output = await renderPromptInput({busy: true, toolName: 'bash'});

	// Busy state replaces the static ">>" cue with a braille spinner frame
	// and renders the tool label with a trailing animated ellipsis.
	assert.match(output, /\[run\] bash/);
	assert.match(output, /[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/u);
	assert.doesNotMatch(output, /[◇◈◆] {2}\| \[run\]/);
});

test('shows a focused busy shortcut footer', async () => {
	const output = await renderPromptInput({busy: true, toolName: 'bash'});

	assert.match(output, /PgUp\/Dn scroll · End resume · \/stop or Ctrl\+C cancel/);
	assert.doesNotMatch(output, /@ files/);
	assert.doesNotMatch(output, /ctrl\+c ctrl\+c exit/);
});

test('shows a visual background activity cue while input remains idle', async () => {
	const output = await renderPromptInput({backgroundTaskCount: 2});

	assert.match(output, /\[bg\] 2 running/);
	assert.match(output, /[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/u);
	assert.match(output, /> /);
	assert.doesNotMatch(output, /\[idle\]/);
});

test('uses a static background cue when animation is disabled', async () => {
	const output = await renderPromptInput({backgroundTaskCount: 1, animateSpinner: false});

	assert.match(output, /● \| \[bg\] 1 running/);
	assert.doesNotMatch(output, /\[idle\]/);
});

test('uses a static busy cue when animation is disabled', async () => {
	const output = await renderPromptInput({busy: true, toolName: 'bash', animateSpinner: false});

	assert.match(output, /⠋ {2}\| \[run\] bash\.\.\./);
});

test('disables spinner animation on flicker-prone terminals', () => {
	assert.equal(shouldAnimateSpinner('win32', {}), false);
	assert.equal(shouldAnimateSpinner('win32', {WT_SESSION: 'abc'}), true);
	assert.equal(shouldAnimateSpinner('win32', {TERM_PROGRAM: 'vscode'}), true);
	assert.equal(shouldAnimateSpinner('win32', {MSYSTEM: 'MINGW64'}), true);
	assert.equal(shouldAnimateSpinner('linux', {SSH_TTY: '/dev/pts/0'}), false);
	assert.equal(shouldAnimateSpinner('darwin', {}), true);
	assert.equal(shouldAnimateSpinner('linux', {}), true);
	// Backwards-compatible alias.
	assert.equal(shouldAnimateBackgroundCue('darwin', {}), true);
});

test('keeps previously typed text when a paste arrives in the same prompt session', async () => {
	const prompt = await renderInteractivePromptInput();
	try {
		prompt.stdin.write('abc');
		await nextLoopTurn();
		prompt.stdin.write('XYZ');
		const output = extractLastFrame(await prompt.getOutput());

		assert.match(output, /> abcXYZ/);
	} finally {
		await prompt.cleanup();
	}
});

test('preserves earlier typed text when a multiline paste arrives mid-session', async () => {
	const prompt = await renderInteractivePromptInput();
	try {
		prompt.stdin.write('lsjfld');
		await nextLoopTurn();
		prompt.stdin.write('first-line\nsecond-line\nthird-line');
		const output = extractLastFrame(await prompt.getOutput());

		// extraInputLines preview should contain the previously-typed prefix joined
		// with the first pasted line ("lsjfldfirst-line"), then the second line.
		assert.match(output, /lsjfldfirst-line/);
		assert.match(output, /second-line/);
		// The current input row should show the last pasted line.
		assert.match(output, /> third-line/);
	} finally {
		await prompt.cleanup();
	}
});

test('does not duplicate buffered lines when a paste arrives one char at a time', async () => {
	const prompt = await renderInteractivePromptInput();
	try {
		// Simulate a slow paste where each character is delivered separately
		// and may interleave with React renders.  This catches regressions where
		// the local input draft retains already-consumed segments and replays
		// them on the next event, duplicating buffered preview lines.
		const sequence = 'pre\npasted\nlive';
		for (const char of sequence) {
			prompt.stdin.write(char);
			for (let i = 0; i < 4; i++) {
				await nextLoopTurn();
			}
		}
		const output = extractLastFrame(await prompt.getOutput());

		// Each segment should appear exactly once.
		const occurrences = (haystack: string, needle: string): number =>
			haystack.split(needle).length - 1;
		assert.equal(occurrences(output, 'pre'), 1, `'pre' duplicated:\n${output}`);
		assert.equal(occurrences(output, 'pasted'), 1, `'pasted' duplicated:\n${output}`);
		assert.match(output, /> live/);
	} finally {
		await prompt.cleanup();
	}
});

test('renders buffered multiline preview lines without wrapping wide text', async () => {
	const prompt = await renderInteractivePromptInput({
		stdoutColumns: 40,
		initialInput: 'tail',
		initialExtraInputLines: ['这是很长很长很长很长很长很长的一行文本'],
	});
	try {
		const output = extractLastFrame(await prompt.getOutput());

		assert.match(output, /│   \.\.\.长很长很长很长很长的一行文本/);
		assert.doesNotMatch(output, /\n│   文本/);
	} finally {
		await prompt.cleanup();
	}
});
