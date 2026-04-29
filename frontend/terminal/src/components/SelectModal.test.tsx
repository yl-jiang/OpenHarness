import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';

import {nextSelectIndexForWheel, SelectModal, type SelectOption} from './SelectModal.js';

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

async function renderSelectModal(options: SelectOption[], selectedIndex: number, strip = true, columns = 120): Promise<string> {
	const stdout = createTestStdout(columns);
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = render(<SelectModal title="Skills" options={options} selectedIndex={selectedIndex} />, {
		stdout: stdout as unknown as NodeJS.WriteStream,
		debug: true,
		patchConsole: false,
	});

	const exitPromise = instance.waitUntilExit();
	const stableOutput = await waitForOutputToStabilize(() => output);
	instance.unmount();
	await exitPromise;
	instance.cleanup();

	return strip ? stripAnsi(stableOutput) : stableOutput;
}

test('windows long select menus around the selected option', async () => {
	const options = Array.from({length: 12}, (_, index) => {
		const n = String(index + 1).padStart(2, '0');
		return {value: `skill-${n}`, label: `skill-${n}`};
	});

	const output = await renderSelectModal(options, 11);

	assert.match(output, /Skills \(12\/12\)/);
	assert.match(output, /skill-12/);
	assert.match(output, /\u2191 2 more/);
	assert.doesNotMatch(output, /skill-01/);
});

test('maps mouse wheel direction to a bounded select index', () => {
	assert.equal(nextSelectIndexForWheel(0, 1, 12), 1);
	assert.equal(nextSelectIndexForWheel(11, 1, 12), 11);
	assert.equal(nextSelectIndexForWheel(1, -1, 12), 0);
	assert.equal(nextSelectIndexForWheel(0, -1, 12), 0);
});

test('renders each skill description below its name with a stable indent', async () => {
	const output = await renderSelectModal([
		{value: 'skill-creator', label: 'skill-creator', description: 'Guide for creating effective skills.'},
		{value: 'think', label: 'think', description: 'Turns rough ideas into approved plans.'},
	], 1);
	const lines = output.split('\n');
	const creatorNameIndex = lines.findIndex((line) => line.includes('- skill-creator'));
	const thinkNameIndex = lines.findIndex((line) => line.includes('- think'));

	assert.ok(creatorNameIndex >= 0, output);
	assert.ok(thinkNameIndex >= 0, output);
	assert.match(lines[creatorNameIndex + 1] ?? '', /Guide for creating effective skills\./);
	assert.match(lines[thinkNameIndex + 1] ?? '', /Turns rough ideas into approved plans\./);
	assert.ok((lines[creatorNameIndex + 1] ?? '').indexOf('Guide') > lines[creatorNameIndex].indexOf('- skill-creator'));
	assert.ok((lines[thinkNameIndex + 1] ?? '').indexOf('Turns') > lines[thinkNameIndex].indexOf('- think'));
	assert.doesNotMatch(output, /skill-creator\s+Guide for creating effective skills\./);
});

test('renders the selected row with inverse video highlighting', async () => {
	const output = await renderSelectModal([
		{value: 'first', label: 'first'},
		{value: 'second', label: 'second'},
	], 1, false);

	assert.match(output, /\u001B\[7m/);
});

test('wraps long descriptions under the skill name', async () => {
	const output = await renderSelectModal([
		{
			value: 'long',
			label: 'long',
			description: 'This description is intentionally long enough to wrap in a narrow terminal description-tail',
		},
	], 0, true, 48);

	assert.match(output, /description-tail/);
	const lines = output.split('\n');
	const nameIndex = lines.findIndex((line) => line.includes('- long'));
	assert.ok(nameIndex >= 0, output);
	assert.match(lines[nameIndex + 1] ?? '', /This description is intentionally/);
});
