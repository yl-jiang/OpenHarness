import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import {ThemeProvider} from '../theme/ThemeContext.js';
import type {TaskSnapshot} from '../types.js';
import {StatusBar} from './StatusBar.js';

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

async function renderStatusBar(
	tasks: TaskSnapshot[],
	status: Record<string, unknown> = {model: 'test-model', permission_mode: 'default'},
): Promise<string> {
	const stdout = createTestStdout();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = render(
		<ThemeProvider initialTheme="default">
			<StatusBar
				status={status}
				tasks={tasks}
			/>
		</ThemeProvider>,
		{stdout: stdout as unknown as NodeJS.WriteStream, debug: true, patchConsole: false},
	);

	const exitPromise = instance.waitUntilExit();
	const stableOutput = await waitForOutputToStabilize(() => output);
	instance.unmount();
	await exitPromise;
	instance.cleanup();

	return stripAnsi(stableOutput);
}

test('counts only active background tasks in the status bar', async () => {
	const output = await renderStatusBar([
		{id: 'task-1', type: 'local_agent', status: 'pending', description: 'pending', metadata: {}},
		{id: 'task-2', type: 'local_agent', status: 'running', description: 'running', metadata: {}},
		{id: 'task-3', type: 'local_agent', status: 'completed', description: 'completed', metadata: {}},
		{id: 'task-4', type: 'local_agent', status: 'failed', description: 'failed', metadata: {}},
		{id: 'task-5', type: 'local_agent', status: 'killed', description: 'killed', metadata: {}},
	]);

	assert.match(output, /⚙️  2/u);
	assert.doesNotMatch(output, /⚙️  5/u);
});

test('shows cwd and git branch in the status bar', async () => {
	const output = await renderStatusBar(
		[],
		{
			model: 'test-model',
			permission_mode: 'default',
			cwd: '/tmp/demo',
			git_branch: 'main',
		},
	);

	assert.match(output, />ˍ \/tmp\/demo/u);
	assert.match(output, / main/u);
});

test('shortens home directory cwd in the status bar', async () => {
	const homeDir = process.env.HOME;
	assert.ok(homeDir, 'HOME must be set for the status bar path test');

	const output = await renderStatusBar(
		[],
		{
			model: 'test-model',
			permission_mode: 'default',
			cwd: `${homeDir}/project/demo`,
		},
	);

	assert.match(output, />ˍ ~\/project\/demo/u);
	assert.doesNotMatch(output, new RegExp(`>ˍ ${homeDir.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
});

test('uses symbolic labels instead of textual status prefixes', async () => {
	const output = await renderStatusBar(
		[{id: 'task-1', type: 'local_agent', status: 'running', description: 'running', metadata: {}}],
		{
			model: 'test-model',
			permission_mode: 'default',
			cwd: '/tmp/demo',
			git_branch: 'dev',
			input_tokens: 1200,
			output_tokens: 3400,
			mcp_connected: 2,
		},
	);

	assert.doesNotMatch(output, /\bmodel:|\bmode:|\bcwd:|\bbranch:|\btokens:|\btasks:|\bmcp:/);
	assert.match(output, /@ test-model/u);
	assert.match(output, /⎇  default/u);
	assert.match(output, />ˍ \/tmp\/demo/u);
	assert.match(output, / dev/u);
	assert.match(output, /\$ 1\.2k ↓ 3\.4k ↑/);
	assert.match(output, /⚙️  1/u);
	assert.match(output, /🔌 2/u);
});
