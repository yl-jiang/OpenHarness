import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import {ModalHost} from './ModalHost.js';

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

function createTestStdout(columns = 80): InkTestStdout {
	return Object.assign(new PassThrough(), {
		isTTY: true,
		columns,
		rows: 24,
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

async function renderModal(reason: string, columns = 56): Promise<string> {
	const stdout = createTestStdout(columns);
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = render(
		<ModalHost
			modal={{kind: 'permission', tool_name: 'bash', reason}}
			modalInput=""
			setModalInput={() => {}}
			onSubmit={() => {}}
		/>,
		{
			stdout: stdout as unknown as NodeJS.WriteStream,
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

test('keeps permission actions visible when the reason is very long', async () => {
	const reason = [
		'Mutating tools require user confirmation in default mode.',
		'Choosing Always will allow this session pattern:',
		'bash -lc "python scripts/really_long_tool.py --input /tmp/source.json --output /tmp/result.json --flag tail-marker-999"',
	].join(' ');

	const output = await renderModal(reason, 52);

	assert.match(output, /\[y\] Once/);
	assert.match(output, /\[a\] Always/);
	assert.match(output, /\[n\] Deny/);
	assert.match(output, /\.\.\. \d+ more lines hidden/);
	assert.doesNotMatch(output, /tail-marker-999/);
});
