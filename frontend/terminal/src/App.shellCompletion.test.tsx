import assert from 'node:assert/strict';
import {chmod, mkdir, mkdtemp, rm, writeFile} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import {App} from './App.js';

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

test('tabs shell commands in shell mode inline and cycles matches without showing a picker', async () => {
	const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'oh-shell-tab-'));
	const binDir = path.join(tempRoot, 'bin');
	const firstCommand = 'openharness-shell-cycle-one';
	const secondCommand = 'openharness-shell-cycle-two';
	const originalPath = process.env.PATH ?? '';

	await mkdir(binDir, {recursive: true});
	await writeFile(path.join(binDir, firstCommand), '#!/bin/sh\nexit 0\n');
	await chmod(path.join(binDir, firstCommand), 0o755);
	await writeFile(path.join(binDir, secondCommand), '#!/bin/sh\nexit 0\n');
	await chmod(path.join(binDir, secondCommand), 0o755);
	process.env.PATH = `${binDir}${path.delimiter}${originalPath}`;

	const stdout = createTestStdout();
	const stdin = createTestStdin();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const backendScript = `
const emit = (event) => process.stdout.write('OHJSON:' + JSON.stringify(event) + '\\n');
emit({type: 'ready', state: {model: 'test-model', permission_mode: 'default', cwd: process.cwd()}, tasks: []});
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
		await sleep(150);
		output = '';
		stdin.write('!');
		await sleep(120);
		stdin.write('openharness-shell-cycle-');
		await sleep(200);
		stdin.write('\t');
		await sleep(250);
		await nextLoopTurn();
		const firstFrame = stripAnsi(output);
		assert.match(firstFrame, /shell mode · tab complete · type "exit" or esc to leave/u);
		assert.match(firstFrame, new RegExp(`! ${firstCommand}\\s`, 'u'));
		assert.doesNotMatch(firstFrame, /Shell completions/u);

		output = '';
		stdin.write('\t');
		await sleep(250);
		await nextLoopTurn();
		const secondFrame = stripAnsi(output);
		assert.match(secondFrame, new RegExp(`! ${secondCommand}\\s`, 'u'));
		assert.doesNotMatch(secondFrame, /Shell completions/u);
	} finally {
		process.env.PATH = originalPath;
		await rm(tempRoot, {recursive: true, force: true});
		const exitPromise = instance.waitUntilExit();
		instance.unmount();
		await exitPromise;
		instance.cleanup();
	}
});

test('uses the newest shell query instead of a stale completion cycle when tab is pressed quickly', async () => {
	const tempRoot = await mkdtemp(path.join(os.tmpdir(), 'oh-shell-stale-tab-'));
	const binDir = path.join(tempRoot, 'bin');
	const originalPath = process.env.PATH ?? '';

	await mkdir(binDir, {recursive: true});
	for (const command of ['containerd', 'tail', 'taillog']) {
		await writeFile(path.join(binDir, command), '#!/bin/sh\nexit 0\n');
		await chmod(path.join(binDir, command), 0o755);
	}
	process.env.PATH = `${binDir}${path.delimiter}${originalPath}`;

	const stdout = createTestStdout();
	const stdin = createTestStdin();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const backendScript = `
const emit = (event) => process.stdout.write('OHJSON:' + JSON.stringify(event) + '\\n');
emit({type: 'ready', state: {model: 'test-model', permission_mode: 'default', cwd: process.cwd()}, tasks: []});
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
		await sleep(150);
		stdin.write('!');
		await sleep(120);
		stdin.write('conta');
		await sleep(180);
		stdin.write('\t');
		await sleep(250);

		stdin.write('\u0003');
		await sleep(120);
		await nextLoopTurn();

		output = '';
		stdin.write('tai');
		await sleep(30);
		stdin.write('\t');
		await sleep(300);
		await nextLoopTurn();

		const frame = stripAnsi(output);
		assert.match(frame, /! tail\s/u);
		assert.doesNotMatch(frame, /! containerd\s/u);
		assert.doesNotMatch(frame, /Shell completions/u);
	} finally {
		process.env.PATH = originalPath;
		await rm(tempRoot, {recursive: true, force: true});
		const exitPromise = instance.waitUntilExit();
		instance.unmount();
		await exitPromise;
		instance.cleanup();
	}
});
