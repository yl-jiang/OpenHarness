import assert from 'node:assert/strict';
import test from 'node:test';

import type {TranscriptItem} from '../types.js';
import {computeCommittedCutoff} from './transcriptCutoff.js';

const item = (role: TranscriptItem['role'], text = ''): TranscriptItem => ({role, text});

test('commits the full transcript when there are no pending tool calls', () => {
	const transcript: TranscriptItem[] = [
		item('user', 'hi'),
		item('assistant', 'hello'),
		item('tool', 'bash'),
		item('tool_result', 'ok'),
	];
	assert.equal(computeCommittedCutoff(transcript), 4);
});

test('keeps an unfinished trailing tool call in the live region', () => {
	const transcript: TranscriptItem[] = [
		item('user'),
		item('assistant'),
		item('tool', 'bash'),
	];
	assert.equal(computeCommittedCutoff(transcript), 2);
});

test('keeps a partially completed parallel batch in the live region', () => {
	const transcript: TranscriptItem[] = [
		item('user'),
		item('tool'),
		item('tool'),
		item('tool_result'),
	];
	assert.equal(computeCommittedCutoff(transcript), 1);
});

test('commits a fully completed parallel batch', () => {
	const transcript: TranscriptItem[] = [
		item('user'),
		item('tool'),
		item('tool'),
		item('tool_result'),
		item('tool_result'),
	];
	assert.equal(computeCommittedCutoff(transcript), 5);
});

test('cutoff is monotonically non-decreasing across appends', () => {
	const events: TranscriptItem[] = [
		item('user'),
		item('assistant'),
		item('tool'),
		item('tool_result'),
		item('assistant'),
		item('tool'),
	];
	let last = 0;
	for (let n = 1; n <= events.length; n++) {
		const c = computeCommittedCutoff(events.slice(0, n));
		assert.ok(c >= last, `cutoff regressed at n=${n}: ${last} -> ${c}`);
		last = c;
	}
});

test('treats an empty transcript as fully committed', () => {
	assert.equal(computeCommittedCutoff([]), 0);
});
