import assert from 'node:assert/strict';
import test from 'node:test';
import {PassThrough} from 'node:stream';
import React from 'react';
import {render as renderInk} from 'ink';

import type {TranscriptItem} from '../types.js';
import {ThemeProvider} from '../theme/ThemeContext.js';
import {ConversationView} from './ConversationView.js';

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
		rows: 1000,
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

async function renderConversation(items: TranscriptItem[]): Promise<string> {
	const stdout = createTestStdout();
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = renderInk(
		<ThemeProvider initialTheme="default">
			<ConversationView
				transcript={items}
				assistantBuffer=""
				showWelcome={false}
				outputStyle="default"
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

test('renders transcript items from the beginning when the full history is provided', async () => {
	const items = Array.from({length: 60}, (_, index) => ({
		role: 'user',
		text: `message-${String(index + 1).padStart(3, '0')}`,
	})) satisfies TranscriptItem[];

	const output = await renderConversation(items);

	assert.match(output, /\bmessage-001\b/);
	assert.match(output, /\bmessage-060\b/);
});

test('keeps an unfinished trailing tool call rendered in the live region', async () => {
	const output = await renderConversation([
		{role: 'user', text: 'do it'},
		{role: 'tool', text: 'bash', tool_name: 'bash'},
	]);

	assert.match(output, /do it/);
	assert.match(output, /bash/);
});
