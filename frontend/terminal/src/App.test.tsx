import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import * as AppModule from './App.js';

import {App, buildSubmittedValue, resolveSelectModalChoice} from './App.js';

const stripAnsi = (value: string): string => value.replace(/\u001B\[[0-9;?]*[ -/]*[@-~]/g, '');
const nextLoopTurn = (): Promise<void> => new Promise((resolve) => setImmediate(resolve));
const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

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

test('prefills the composer after selecting a skill instead of applying the selection immediately', () => {
	const result = resolveSelectModalChoice('skills', 'weekly-report');

	assert.deepEqual(result, {
		kind: 'prefill',
		input: '/weekly-report ',
	});
});

test('keeps backend-applied behavior for non-skill select commands', () => {
	const result = resolveSelectModalChoice('theme', 'solarized');

	assert.deepEqual(result, {
		kind: 'apply',
		command: 'theme',
		value: 'solarized',
	});
});

test('filters /skills select options by skill name with case-insensitive matching', () => {
	const filterSelectModalOptions = (AppModule as {
		filterSelectModalOptions?: (
			command: string,
			options: Array<{value: string; label: string}>,
			query: string,
		) => Array<{value: string; label: string}>;
	}).filterSelectModalOptions;

	assert.equal(typeof filterSelectModalOptions, 'function');
	assert.deepEqual(
		filterSelectModalOptions?.(
			'skills',
			[
				{value: 'weekly-report', label: 'weekly-report'},
				{value: 'incident-review', label: 'incident-review'},
				{value: 'release-checklist', label: 'release-checklist'},
			],
			'REp',
		).map((option) => option.value),
		['weekly-report'],
	);
});

test('builds a submittable multiline value when the current line is empty but prior lines exist', () => {
	assert.equal(buildSubmittedValue('', ['first line', 'second line']), 'first line\nsecond line\n');
});

test('does not submit whitespace-only buffered lines', () => {
	assert.equal(buildSubmittedValue('', ['', '   ']), null);
});

test('treats double escape while busy as a cancellation request', () => {
	const resolveEscapeAction = (AppModule as {
		resolveEscapeAction?: (state: {
			busy: boolean;
			paused: boolean;
			hasInput: boolean;
			now: number;
			lastEscapeAt: number;
		}) => {action: string; nextLastEscapeAt: number};
	}).resolveEscapeAction;

	assert.equal(typeof resolveEscapeAction, 'function');
	assert.deepEqual(
		resolveEscapeAction?.({
			busy: true,
			paused: false,
			hasInput: false,
			now: 1_000,
			lastEscapeAt: 700,
		}),
		{action: 'cancel_busy_turn', nextLastEscapeAt: 0},
	);
});

test('keeps the first escape during a busy turn as an arming press', () => {
	const resolveEscapeAction = (AppModule as {
		resolveEscapeAction?: (state: {
			busy: boolean;
			paused: boolean;
			hasInput: boolean;
			now: number;
			lastEscapeAt: number;
		}) => {action: string; nextLastEscapeAt: number};
	}).resolveEscapeAction;

	assert.equal(typeof resolveEscapeAction, 'function');
	assert.deepEqual(
		resolveEscapeAction?.({
			busy: true,
			paused: false,
			hasInput: false,
			now: 1_000,
			lastEscapeAt: 0,
		}),
		{action: 'arm_escape', nextLastEscapeAt: 1_000},
	);
});

test('cycles slash picker navigation at both ends', () => {
	const cyclePickerIndex = (AppModule as {
		cyclePickerIndex?: (currentIndex: number, delta: number, itemCount: number) => number;
	}).cyclePickerIndex;

	assert.equal(typeof cyclePickerIndex, 'function');
	assert.equal(cyclePickerIndex?.(0, -1, 3), 2);
	assert.equal(cyclePickerIndex?.(2, 1, 3), 0);
	assert.equal(cyclePickerIndex?.(1, 1, 1), 0);
});

test('builds a full slash command when selecting a submenu item', () => {
	const buildSlashCommandSelection = (AppModule as {
		buildSlashCommandSelection?: (rootCommand: string, subcommand?: string) => string;
	}).buildSlashCommandSelection;

	assert.equal(typeof buildSlashCommandSelection, 'function');
	assert.equal(buildSlashCommandSelection?.('/memory', 'show'), '/memory show ');
	assert.equal(buildSlashCommandSelection?.('/resume', undefined), '/resume');
});

test('animates the prompt cue from backend task snapshots in the full app', async () => {
	const stdout = createTestStdout();
	const stdin = createTestStdin();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const backendScript = `
const emit = (event) => process.stdout.write('OHJSON:' + JSON.stringify(event) + '\\n');
emit({type: 'ready', state: {model: 'test-model', permission_mode: 'default', cwd: process.cwd()}, tasks: []});
setTimeout(() => emit({type: 'tasks_snapshot', tasks: [{id: 'task-1', type: 'local_agent', status: 'running', description: 'agent', started_at: Date.now() / 1000, metadata: {}}]}), 25);
setInterval(() => {}, 1000);
`;

	const instance = render(
		<App config={{backend_command: [process.execPath, '-e', backendScript], theme: 'default'}} />,
		{
			stdout: stdout as unknown as NodeJS.WriteStream,
			stdin: stdin as unknown as NodeJS.ReadStream,
			exitOnCtrlC: false,
			patchConsole: false,
			debug: true,
		},
	);

	try {
		await sleep(600);
		await nextLoopTurn();
		const text = stripAnsi(output);
		assert.match(text, /⠋ \| \[bg\] running/u);
		assert.match(text, /⠙ \| \[bg\] running/u);
		assert.doesNotMatch(text, /⠙ \| \[bg\] running \d+s/u);
		assert.match(text, /⚙ 1 · 00:00/u);
		assert.doesNotMatch(text, /[◐◓◑◒]/u);
		assert.doesNotMatch(text, /OpenHarness[^\n]*⚙/u);
	} finally {
		const exitPromise = instance.waitUntilExit();
		instance.unmount();
		await exitPromise;
		instance.cleanup();
	}
});

test('does not direct-write the prompt overlay while foreground processing output streams', async () => {
	const stdout = createTestStdout();
	const stdin = createTestStdin();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const backendScript = `
const emit = (event) => process.stdout.write('OHJSON:' + JSON.stringify(event) + '\\n');
emit({type: 'ready', state: {model: 'test-model', permission_mode: 'default', cwd: process.cwd()}, tasks: []});
setTimeout(() => emit({type: 'tool_started', tool_name: 'bash', item: {role: 'tool', text: 'run', tool_name: 'bash'}}), 25);
setTimeout(() => emit({type: 'tool_completed', tool_name: 'bash', item: {role: 'tool_result', text: 'done', tool_name: 'bash'}}), 80);
let i = 0;
setInterval(() => process.stdout.write('history agent message ' + i++ + '\\n'), 40);
setInterval(() => {}, 1000);
`;

	const instance = render(
		<App config={{backend_command: [process.execPath, '-e', backendScript], theme: 'default'}} />,
		{
			stdout: stdout as unknown as NodeJS.WriteStream,
			stdin: stdin as unknown as NodeJS.ReadStream,
			exitOnCtrlC: false,
			patchConsole: false,
			debug: true,
		},
	);

	try {
		await sleep(180);
		await nextLoopTurn();
		output = '';
		await sleep(700);
		await nextLoopTurn();
		assert.match(stripAnsi(output), /⠋ {2}\| Processing\.\.\./u);
		assert.doesNotMatch(stripAnsi(output), /Processing\.\.\. \d+s/u);
		assert.doesNotMatch(output, /\u001B\[s\u001B\[[0-9]+;4H[^\u001B]*.*Processing\.\.\./u);
	} finally {
		const exitPromise = instance.waitUntilExit();
		instance.unmount();
		await exitPromise;
		instance.cleanup();
	}
});

test('does not emit periodic full-frame Ink redraws while background work is idle', async () => {
	const stdout = createTestStdout();
	const stdin = createTestStdin();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const backendScript = `
const emit = (event) => process.stdout.write('OHJSON:' + JSON.stringify(event) + '\\n');
emit({type: 'ready', state: {model: 'test-model', permission_mode: 'default', cwd: process.cwd()}, tasks: []});
setTimeout(() => emit({type: 'tasks_snapshot', tasks: [{id: 'task-1', type: 'local_agent', status: 'running', description: 'agent', started_at: Date.now() / 1000, metadata: {}}]}), 25);
setInterval(() => {}, 1000);
`;

	const instance = render(
		<App config={{backend_command: [process.execPath, '-e', backendScript], theme: 'default'}} />,
		{
			stdout: stdout as unknown as NodeJS.WriteStream,
			stdin: stdin as unknown as NodeJS.ReadStream,
			exitOnCtrlC: false,
			patchConsole: false,
			debug: true,
		},
	);

	try {
		await sleep(350);
		await nextLoopTurn();
		output = '';
		await sleep(1300);
		await nextLoopTurn();

		assert.doesNotMatch(stripAnsi(output), /OpenHarness|commands · @ files|PgUp\/Dn scroll|╭|╰/u);
	} finally {
		const exitPromise = instance.waitUntilExit();
		instance.unmount();
		await exitPromise;
		instance.cleanup();
	}
});
