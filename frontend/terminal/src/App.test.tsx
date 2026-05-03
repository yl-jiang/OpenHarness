import assert from 'node:assert/strict';
import test from 'node:test';

import * as AppModule from './App.js';

import {buildSubmittedValue, resolveSelectModalChoice} from './App.js';

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
