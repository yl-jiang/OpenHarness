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

function createTestStdout(columns = 120, rows = 1000): InkTestStdout {
	return Object.assign(new PassThrough(), {
		isTTY: true,
		columns,
		rows,
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

async function renderConversation(
	items: TranscriptItem[],
	{
		showWelcome = false,
		welcomeVersion,
		columns,
		rows,
		strip = true,
	}: {
		showWelcome?: boolean;
		welcomeVersion?: string;
		columns?: number;
		rows?: number;
		strip?: boolean;
	} = {},
): Promise<string> {
	const stdout = createTestStdout(columns, rows);
	let output = '';
	stdout.on('data', (chunk) => {
		output += chunk.toString();
	});

	const instance = renderInk(
		<ThemeProvider initialTheme="default">
			<ConversationView
				transcript={items}
				assistantBuffer=""
				showWelcome={showWelcome}
				welcomeVersion={welcomeVersion}
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

	return strip ? stripAnsi(stableOutput) : stableOutput;
}

const countOccurrences = (value: string, needle: string): number =>
	value.split(needle).length - 1;
const finalFrameFrom = (value: string, marker: string): string => {
	const start = value.lastIndexOf(marker);
	return start >= 0 ? value.slice(start) : value;
};

test('renders transcript items from the beginning when the full history is provided', async () => {
	const items = Array.from({length: 60}, (_, index) => ({
		role: 'user',
		text: `message-${String(index + 1).padStart(3, '0')}`,
	})) satisfies TranscriptItem[];

	const output = await renderConversation(items);

	assert.match(output, /\bmessage-001\b/);
	assert.match(output, /\bmessage-060\b/);
});

test('renders the welcome banner with the runtime version', async () => {
	const output = await renderConversation([], {showWelcome: true, welcomeVersion: '9.9.9'});
	const plainOutput = stripAnsi(output);

	assert.match(plainOutput, /╭────╮ ╷   ╷/);
	assert.match(plainOutput, /v9\.9\.9/);
	assert.match(plainOutput, /plans · tools · skills · memory/);
	assert.match(plainOutput, /@ files/);
	assert.doesNotMatch(plainOutput, /v0\.1\.6/);
});

test('keeps the welcome banner in scrollback after the first transcript item', async () => {
	const output = await renderConversation(
		[{role: 'user', text: 'first turn'}],
		{showWelcome: true, welcomeVersion: '9.9.9', rows: 12},
	);

	assert.match(output, /you · first turn/);
	assert.match(output, /v9\.9\.9/);
	assert.match(output, /autonomous coding agent/);
});

test('keeps an unfinished trailing tool call rendered in the live region', async () => {
	const output = await renderConversation([
		{role: 'user', text: 'do it'},
		{role: 'tool', text: 'bash', tool_name: 'bash'},
	]);

	assert.match(output, /do it/);
	assert.match(output, /bash/);
});

test('renders assistant header before tool runs in a later turn', async () => {
	const output = await renderConversation([
		{role: 'user', text: 'hi'},
		{role: 'assistant', text: 'hello'},
		{role: 'user', text: 'inspect the project'},
		{role: 'tool', text: 'ls -la', tool_name: 'bash', tool_input: {command: 'ls -la'}},
		{role: 'tool_result', text: 'total 0'},
		{role: 'assistant', text: 'Let me look at a few more areas:'},
	]);
	const frame = finalFrameFrom(output, 'you · hi');

	const turnStart = frame.indexOf('you · inspect the project');
	const toolStart = frame.indexOf('bash ls -la');
	const assistantAfterTurn = frame.indexOf('╰─ assistant', turnStart + 1);

	assert.notEqual(turnStart, -1);
	assert.notEqual(toolStart, -1);
	assert.notEqual(assistantAfterTurn, -1);
	assert.ok(assistantAfterTurn < toolStart, `expected assistant header before tool run, got:\n${frame}`);
	assert.equal(countOccurrences(frame, '╰─ assistant'), 2);
});

test('does not render an assistant header for user shell tool runs', async () => {
	const output = await renderConversation([
		{role: 'user_shell', text: 'ls'},
		{role: 'tool', text: 'bash', tool_name: 'bash', tool_input: {command: 'ls', origin: 'user_shell'}},
		{role: 'tool_result', text: 'README.md\nsrc'},
	], {strip: false});
	const plainOutput = stripAnsi(output);
	const frame = finalFrameFrom(plainOutput, '! ls');

	assert.match(frame, /! ls/);
	assert.doesNotMatch(frame, /[╭╮╰╯]/);
	assert.match(frame, /✓\s+Shell Command ls/);
	assert.match(frame, /README\.md/);
	assert.match(frame, /src/);
	assert.match(output, /\u001B\[[0-9;]*2m(?:\u001B\[[0-9;]*m){0,3}README\.md/u);
	assert.match(output, /\u001B\[[0-9;]*2m(?:\u001B\[[0-9;]*m){0,3}src/u);
	assert.doesNotMatch(frame, /╰─ assistant/);
});

test('adds spacing before replies and a divider between completed turns', async () => {
	const output = await renderConversation([
		{role: 'user', text: 'hi'},
		{role: 'assistant', text: 'hello'},
		{role: 'user', text: 'next question'},
	]);
	const frame = finalFrameFrom(output, 'you · hi');

	assert.match(frame, /you · hi\s*\n\s*\n\s*╰─ assistant/);
	const dividerIndex = frame.indexOf('╌╌╌╌');
	const secondUserIndex = frame.indexOf('you · next question');
	assert.notEqual(dividerIndex, -1, `expected turn divider, got:\n${frame}`);
	assert.notEqual(secondUserIndex, -1);
	assert.ok(dividerIndex < secondUserIndex, `expected divider before next user turn, got:\n${frame}`);
});

test('does not duplicate assistant header across tools and reply body in the same turn', async () => {
	const output = await renderConversation([
		{role: 'user', text: 'inspect the project'},
		{role: 'tool', text: 'ls -la', tool_name: 'bash', tool_input: {command: 'ls -la'}},
		{role: 'tool_result', text: 'total 0'},
		{role: 'assistant', text: 'Let me look at a few more areas:'},
	]);
	const frame = finalFrameFrom(output, 'you · inspect the project');

	assert.equal(countOccurrences(frame, '╰─ assistant'), 1, `expected a single assistant header, got:\n${frame}`);
});

test('keeps multiline pasted user turns readable without wrapping away the role label', async () => {
	const output = await renderConversation(
		[
			{role: 'user', text: 'hi'},
			{role: 'assistant', text: 'hello'},
			{
				role: 'user',
				text: 'lsjfld\nfuture = self._question_requests[request.request_id]request.request_id in self._question_requests:\n    future.set_result(request.answer or "")',
			},
			{role: 'assistant', text: 'I can help with that.'},
		],
		{columns: 90, rows: 18},
	);
	const frame = finalFrameFrom(output, 'you · hi');

	assert.match(frame, /you · lsjfld/);
	assert.match(frame, /future = self\._question_requests\[request\.request_id\].+\.\.\./);
	assert.doesNotMatch(frame, /\n  self\._question_requests:/);
});
