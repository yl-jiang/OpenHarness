import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render} from 'ink';
import stringWidth from 'string-width';

import {ThemeProvider} from '../theme/ThemeContext.js';
import {MarkdownText} from './MarkdownText.js';

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

async function renderMarkdownLines(content: string, columns = 120): Promise<string[]> {
	const stdout = createTestStdout(columns);

	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = render(
		<ThemeProvider initialTheme="default">
			<MarkdownText content={content} />
		</ThemeProvider>,
		{stdout: stdout as unknown as NodeJS.WriteStream, debug: true, patchConsole: false},
	);

	const exitPromise = instance.waitUntilExit();
	const stableOutput = await waitForOutputToStabilize(() => output);
	instance.unmount();
	await exitPromise;
	await waitForOutputToStabilize(() => output);
	instance.cleanup();

	return stripAnsi(stableOutput)
		.split('\n')
		.filter(Boolean);
}

async function renderTableLines(content: string, columns = 120): Promise<string[]> {
	return (await renderMarkdownLines(content, columns))
		.filter((line) => /[┌├│└]/.test(line))
		.slice(0, 5);
}

test('keeps table borders aligned when cells contain inline markdown', async () => {
	const lines = await renderTableLines('| `aa` | bb |\n|------|----|\n| c | **ddd** |');

	assert.equal(lines.length, 5);

	const widths = lines.map((line) => [...line].length);
	assert.ok(
		widths.every((width) => width === widths[0]),
		`Expected table lines to share a width, got ${JSON.stringify(
			lines.map((line, index) => ({line, width: widths[index]})),
		)}`,
	);
});

test('renders unknown inline table tokens using the visible token text fallback', async () => {
	const lines = await renderTableLines('| ![alt](https://example.com/img.png) | ok |\n|---|---|\n| x | y |');

	assert.equal(lines.length, 5);
	assert.match(lines[1] ?? '', /\balt\b/);
	assert.doesNotMatch(lines[1] ?? '', /!\[alt\]/);

	const widths = lines.map((line) => [...line].length);
	assert.ok(
		widths.every((width) => width === widths[0]),
		`Expected fallback-token table lines to share a width, got ${JSON.stringify(
			lines.map((line, index) => ({line, width: widths[index]})),
		)}`,
	);
});

test('truncates wide table columns to fit within narrow terminal width', async () => {
	// 3 wide columns that would overflow a 40-col terminal
	const wideTable = [
		'| Commit message with long text | Content description | Files changed |',
		'|-------------------------------|---------------------|---------------|',
		'| chore: rename directories     | Reorganize files    | 16 files      |',
	].join('\n');
	const lines = await renderTableLines(wideTable, 40);

	assert.equal(lines.length, 5);
	// All table border lines must be the same width (no wrapping)
	const widths = lines.map((line) => stringWidth(line));
	assert.ok(
		widths.every((w) => w === widths[0]),
		`Table lines have inconsistent widths in narrow terminal: ${JSON.stringify(
			lines.map((line, index) => ({line, width: widths[index]})),
		)}`,
	);
	// Total width must fit within terminal (40 cols minus left margin of 1)
	assert.ok(
		(widths[0] ?? 0) <= 39,
		`Table is wider than terminal allows: width=${widths[0]}`,
	);
});

test('preserves nested markdown structure inside blockquotes', async () => {
	const lines = await renderMarkdownLines('> - first\n> - second');

	assert.ok(lines.some((line) => line.includes('• first')), `Expected blockquote output to include a rendered bullet: ${JSON.stringify(lines)}`);
	assert.ok(lines.some((line) => line.includes('• second')), `Expected blockquote output to include the second rendered bullet: ${JSON.stringify(lines)}`);
});
