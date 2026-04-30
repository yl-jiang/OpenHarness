import assert from 'node:assert/strict';
import test from 'node:test';

import * as AppModule from './App.js';

import {resolveSelectModalChoice} from './App.js';

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
