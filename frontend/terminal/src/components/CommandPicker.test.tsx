import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import {CommandPicker} from './CommandPicker.js';

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

async function renderCommandPicker(
	hints: string[],
	selectedIndex: number,
	subHintsByHint: Record<string, string[]> = {},
): Promise<string> {
	const stdout = createTestStdout();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = render(
		<CommandPicker
			hints={hints}
			selectedIndex={selectedIndex}
			subHintsByHint={subHintsByHint}
		/>,
		{stdout: stdout as unknown as NodeJS.WriteStream, debug: true, patchConsole: false},
	);

	const exitPromise = instance.waitUntilExit();
	const stableOutput = await waitForOutputToStabilize(() => output);
	instance.unmount();
	await exitPromise;
	instance.cleanup();

	return stripAnsi(stableOutput);
}

test('shows a side submenu for the selected command with child commands', async () => {
	const output = await renderCommandPicker(['/memory', '/clear'], 0, {
		'/memory': ['list', 'show', 'add', 'remove'],
	});

	assert.match(output, /Commands/);
	assert.match(output, /\/memory\s+›/);
	assert.doesNotMatch(output, /\/clear\s+›/);
	assert.match(output, /Subcommands/);
	assert.match(output, /list/);
	assert.match(output, /show/);
	assert.doesNotMatch(output, /remove\s+\/clear/s);
});

test('does not show the side submenu when the selected command has no child commands', async () => {
	const output = await renderCommandPicker(['/memory', '/clear'], 1, {
		'/memory': ['list', 'show'],
	});

	assert.match(output, /Commands/);
	assert.doesNotMatch(output, /Subcommands/);
	assert.doesNotMatch(output, /\blist\b/);
});

test('groups child command variants under their root command at the first menu level', async () => {
	const module = await import('./CommandPicker.js') as {
		createCommandPickerModel?: (
			commands: string[],
			input: string,
		) => {hints: string[]; subHintsByHint: Record<string, string[]>};
	};

	assert.equal(typeof module.createCommandPickerModel, 'function');
	const model = module.createCommandPickerModel([
		'/memory',
		'/memory list',
		'/memory show',
		'/clear',
	], '/');

	assert.deepEqual(model.hints, ['/memory', '/clear']);
	assert.deepEqual(model.subHintsByHint, {
		'/memory': ['list', 'show'],
	});
});

test('keeps full child command completion after the user types a command and space', async () => {
	const module = await import('./CommandPicker.js') as {
		createCommandPickerModel?: (
			commands: string[],
			input: string,
			skills?: string[],
		) => {hints: string[]; subHintsByHint: Record<string, string[]>};
	};

	assert.equal(typeof module.createCommandPickerModel, 'function');
	const model = module.createCommandPickerModel([
		'/memory',
		'/memory list',
		'/memory show',
		'/clear',
	], '/memory ');

	assert.deepEqual(model.hints, ['/memory list', '/memory show']);
	assert.deepEqual(model.subHintsByHint, {});
});

test('includes direct skill aliases in slash-command hints', async () => {
	const module = await import('./CommandPicker.js') as {
		createCommandPickerModel?: (
			commands: string[],
			input: string,
			skills?: string[],
		) => {hints: string[]; subHintsByHint: Record<string, string[]>};
	};

	assert.equal(typeof module.createCommandPickerModel, 'function');
	const model = module.createCommandPickerModel([
		'/memory',
		'/skills',
	], '/w', ['weekly-report', 'write']);

	assert.deepEqual(model.hints, ['/weekly-report', '/write']);
	assert.deepEqual(model.subHintsByHint, {});
});
