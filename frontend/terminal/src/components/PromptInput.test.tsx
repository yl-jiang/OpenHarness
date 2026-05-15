import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React, {useCallback, useState} from 'react';
import {render} from 'ink';

import {ThemeProvider} from '../theme/ThemeContext.js';
import {PromptInput, shouldAnimateBackgroundCue, shouldAnimateSpinner} from './PromptInput.js';

const stripAnsi = (value: string): string => value.replace(/\u001B\[[0-9;?]*[ -/]*[@-~]/g, '');
const nextLoopTurn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));
const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
const extractLastFrame = (output: string): string => {
	// Each frame has two ─ separator lines (top + bottom of input box).
	// Find the top separator of the last frame.
	const bottomSep = output.lastIndexOf('\n─');
	if (bottomSep >= 0) {
		const topSep = output.lastIndexOf('\n─', bottomSep - 1);
		if (topSep >= 0) return output.slice(topSep + 1);
		return output.slice(bottomSep + 1);
	}
	// Fallback: look for the > prompt prefix
	const promptBoundary = output.lastIndexOf('\n> ');
	return promptBoundary >= 0 ? output.slice(promptBoundary + 1) : output;
};
const ignoreInput = (): void => {};

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
	notices = [],
}: {
	busy?: boolean;
	toolName?: string;
	backgroundTaskCount?: number;
	animateSpinner?: boolean;
	stdoutColumns?: number;
	notices?: string[];
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
				notices={notices}
				toolName={toolName}
				hasBackgroundTasks={backgroundTaskCount > 0}
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
	busy = false,
	initialInput = '',
	initialExtraInputLines = [],
	initialTrailingInputLines = [],
	backgroundTaskCount = 0,
	animateSpinner,
	stdoutColumns = 120,
	vimEnabled = false,
	initialVimInputMode = 'insert',
}: {
	busy?: boolean;
	initialInput?: string;
	initialExtraInputLines?: string[];
	initialTrailingInputLines?: string[];
	backgroundTaskCount?: number;
	animateSpinner?: boolean;
	stdoutColumns?: number;
	vimEnabled?: boolean;
	initialVimInputMode?: 'insert' | 'normal';
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
		const [trailingInputLines, setTrailingInputLines] = useState(initialTrailingInputLines);
		const [vimInputMode, setVimInputMode] = useState<'insert' | 'normal'>(initialVimInputMode);
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
		const handleVimOpenLineBelow = useCallback(() => {
			setExtraInputLines((prev) => [...prev, input]);
			setInput('');
		}, [input]);
		const handleVimOpenLineAbove = useCallback(() => {
			setTrailingInputLines((prev) => [input, ...prev]);
			setInput('');
		}, [input]);
		const handleBackspaceAtStart = useCallback(() => {
			if (extraInputLines.length === 0) {
				return;
			}
			const previousLine = extraInputLines[extraInputLines.length - 1] ?? '';
			setInput(`${previousLine}${input}`);
			setExtraInputLines(extraInputLines.slice(0, -1));
		}, [extraInputLines, input]);

		return (
			<ThemeProvider initialTheme="default">
				<PromptInput
					busy={busy}
					input={input}
					setInput={handleInputChange}
					onSubmit={() => {}}
					extraInputLines={extraInputLines}
					trailingInputLines={trailingInputLines}
					hasBackgroundTasks={backgroundTaskCount > 0}
					animateSpinner={animateSpinner}
					vimEnabled={vimEnabled}
					vimInputMode={vimInputMode}
					onVimInputModeChange={setVimInputMode}
					onVimOpenLineBelow={handleVimOpenLineBelow}
					onVimOpenLineAbove={handleVimOpenLineAbove}
					onBackspaceAtStart={handleBackspaceAtStart}
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

async function renderRerenderablePromptInput({
	backgroundTaskCount = 0,
}: {
	backgroundTaskCount?: number;
} = {}): Promise<{
	rerenderWithBackgroundTaskCount: (count: number) => void;
	getRawOutput: () => string;
	cleanup: () => Promise<void>;
}> {
	const stdout = createTestStdout();
	const stdin = createTestStdin();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});
	let setBackgroundTaskCount: ((count: number) => void) | null = null;

	function Host(): React.JSX.Element {
		const [count, setCount] = useState(backgroundTaskCount);
		setBackgroundTaskCount = setCount;

		return (
			<ThemeProvider initialTheme="default">
				<PromptInput
					busy={false}
					input=""
					setInput={ignoreInput}
					onSubmit={ignoreInput}
					hasBackgroundTasks={count > 0}
					animateSpinner={false}
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
		rerenderWithBackgroundTaskCount: (count: number) => setBackgroundTaskCount?.(count),
		getRawOutput: () => output,
		cleanup: async () => {
			const exitPromise = instance.waitUntilExit();
			instance.unmount();
			await exitPromise;
			instance.cleanup();
		},
	};
}

test('shows prompt prefix and shortcuts in idle state', async () => {
	const output = await renderPromptInput();

	// New layout: > prefix, placeholder, shortcuts footer, expand trigger
	assert.match(output, /> /);
	assert.match(output, /⇖⇘/u);
	assert.match(output, /\/ commands/);
});

test('shows a concise idle shortcut footer', async () => {
	const output = await renderPromptInput();

	assert.match(output, /\/ commands · @ files · ↑↓ history · shift\+enter newline/);
});

test('renders prompt notices inside the input box when paste staging metadata exists', async () => {
	const output = await renderPromptInput({
		notices: ['[Paste #2 - 101 lines] saved to /tmp/openharness-paste-2/demo.txt'],
	});

	assert.match(output, /\[Paste #2 - 101 lines\] saved to \/tmp\/openharness-paste-2\/demo\.txt/);
});

test('shows a static busy indicator with the running tool name', async () => {
	const output = await renderPromptInput({busy: true, toolName: 'bash'});

	assert.match(output, /⠋ bash/);
});

test('shows a focused busy shortcut footer', async () => {
	const output = await renderPromptInput({busy: true, toolName: 'bash'});

	assert.match(output, /esc×2 cancel · ctrl\+c stop/);
	assert.doesNotMatch(output, /@ files/);
});

test('shows a stable visual background activity cue while input remains idle', async () => {
	const output = await renderPromptInput({backgroundTaskCount: 2});

	// Background tasks: spinner prefix indicates activity, input still editable
	assert.match(output, /⠋ /);
});

test('uses a static background cue when animation is disabled', async () => {
	const output = await renderPromptInput({backgroundTaskCount: 1, animateSpinner: false});

	assert.match(output, /⠋ /);
});

test('does not repaint the prompt while only background tasks are running', async () => {
	const prompt = await renderInteractivePromptInput({backgroundTaskCount: 1, animateSpinner: false});
	try {
		const before = await prompt.getOutput();
		await sleep(260);
		await nextLoopTurn();
		const after = await prompt.getOutput();

		assert.equal(after, before);
		assert.match(after, /⠋ /);
	} finally {
		await prompt.cleanup();
	}
});

test('does not repaint the prompt while foreground busy work has background tasks', async () => {
	const prompt = await renderInteractivePromptInput({busy: true, backgroundTaskCount: 1, animateSpinner: false});
	try {
		const before = await prompt.getOutput();
		await sleep(260);
		await nextLoopTurn();
		const after = await prompt.getOutput();

		assert.equal(after, before);
		assert.match(after, /⠋ running/);
	} finally {
		await prompt.cleanup();
	}
});

test('does not repaint the prompt while a foreground turn is busy', async () => {
	const prompt = await renderInteractivePromptInput({busy: true, animateSpinner: false});
	try {
		const before = await prompt.getOutput();
		await sleep(950);
		await nextLoopTurn();
		const after = await prompt.getOutput();

		assert.equal(after, before);
	} finally {
		await prompt.cleanup();
	}
});

test('does not repaint the prompt when the active background task count changes', async () => {
	const prompt = await renderRerenderablePromptInput({backgroundTaskCount: 3});
	try {
		const before = prompt.getRawOutput();
		prompt.rerenderWithBackgroundTaskCount(2);
		await nextLoopTurn();
		await nextLoopTurn();

		assert.equal(prompt.getRawOutput(), before);
	} finally {
		await prompt.cleanup();
	}
});

test('uses a static busy cue when animation is disabled', async () => {
	const output = await renderPromptInput({busy: true, toolName: 'bash', animateSpinner: false});

	assert.match(output, /⠋ bash/);
});

test('disables spinner animation on flicker-prone terminals', () => {
	assert.equal(shouldAnimateSpinner('win32', {}), false);
	assert.equal(shouldAnimateSpinner('win32', {WT_SESSION: 'abc'}), false);
	assert.equal(shouldAnimateSpinner('win32', {TERM_PROGRAM: 'vscode'}), false);
	assert.equal(shouldAnimateSpinner('win32', {MSYSTEM: 'MINGW64'}), false);
	assert.equal(shouldAnimateSpinner('linux', {SSH_TTY: '/dev/pts/0'}), false);
	assert.equal(shouldAnimateSpinner('darwin', {}), false);
	assert.equal(shouldAnimateSpinner('linux', {}), false);
	assert.equal(shouldAnimateBackgroundCue('win32', {}), false);
	assert.equal(shouldAnimateBackgroundCue('win32', {WT_SESSION: 'abc'}), true);
	assert.equal(shouldAnimateBackgroundCue('linux', {SSH_TTY: '/dev/pts/0'}), false);
	assert.equal(shouldAnimateBackgroundCue('darwin', {}), false);
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

test('supports vim normal and insert editing in the prompt', async () => {
	const prompt = await renderInteractivePromptInput({
		vimEnabled: true,
		initialVimInputMode: 'normal',
	});
	try {
		prompt.stdin.write('zzz');
		await nextLoopTurn();
		let output = extractLastFrame(await prompt.getOutput());
		assert.doesNotMatch(output, /> z/u);
		assert.match(output, /vim normal/u);

		prompt.stdin.write('iabc');
		await nextLoopTurn();
		output = extractLastFrame(await prompt.getOutput());
		assert.match(output, /> abc/u);
		assert.match(output, /vim insert/u);

		prompt.stdin.write('\x1b');
		await nextLoopTurn();
		prompt.stdin.write('hx');
		await nextLoopTurn();
		output = extractLastFrame(await prompt.getOutput());
		assert.match(output, /> ab/u);
		assert.match(output, /vim normal/u);
	} finally {
		await prompt.cleanup();
	}
});

test('supports vim open-line-below command in the prompt', async () => {
	const prompt = await renderInteractivePromptInput({
		vimEnabled: true,
		initialVimInputMode: 'normal',
		initialInput: 'tail',
		initialExtraInputLines: ['head'],
	});
	try {
		prompt.stdin.write('o');
		await nextLoopTurn();
		let output = extractLastFrame(await prompt.getOutput());
		assert.match(output, /head/u);
		assert.match(output, /> /u);
		assert.doesNotMatch(output, /> tail/u);
		assert.match(output, /vim insert/u);

		prompt.stdin.write('body');
		await nextLoopTurn();
		output = extractLastFrame(await prompt.getOutput());
		assert.match(output, /head/u);
		assert.match(output, /tail/u);
		assert.match(output, /> body/u);
	} finally {
		await prompt.cleanup();
	}
});

test('supports vim open-line-above command in the prompt', async () => {
	const prompt = await renderInteractivePromptInput({
		vimEnabled: true,
		initialVimInputMode: 'normal',
		initialInput: 'tail',
		initialExtraInputLines: ['head'],
	});
	try {
		prompt.stdin.write('O');
		await nextLoopTurn();
		let output = extractLastFrame(await prompt.getOutput());
		assert.match(output, /head/u);
		assert.match(output, /tail/u);
		assert.match(output, /> /u);
		assert.doesNotMatch(output, /> tail/u);
		assert.match(output, /vim insert/u);

		prompt.stdin.write('body');
		await nextLoopTurn();
		output = extractLastFrame(await prompt.getOutput());
		assert.match(output, /head/u);
		assert.match(output, /tail/u);
		assert.match(output, /> body/u);
	} finally {
		await prompt.cleanup();
	}
});

test('backspace at the start of the last line pulls the previous multiline content back into the prompt', async () => {
	const prompt = await renderInteractivePromptInput({
		initialInput: '',
		initialExtraInputLines: ['head'],
	});
	try {
		prompt.stdin.write('\b');
		await nextLoopTurn();
		let output = extractLastFrame(await prompt.getOutput());
		assert.match(output, /> head/u);
		assert.doesNotMatch(output, /\n   head\n/u);

		prompt.stdin.write('\b');
		await nextLoopTurn();
		output = extractLastFrame(await prompt.getOutput());
		assert.match(output, /> hea/u);
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

		// The preview line should be clipped with ellipsis
		assert.match(output, /\.\.\./);
		assert.match(output, /一行文本/);
		assert.match(output, /> tail/);
	} finally {
		await prompt.cleanup();
	}
});
