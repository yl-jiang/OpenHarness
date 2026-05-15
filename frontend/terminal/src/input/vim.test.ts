import assert from 'node:assert/strict';
import test from 'node:test';

import {applyVimNormalMode} from './vim.js';

test('applyVimNormalMode routes o/O through open-line bindings and switches to insert mode', () => {
	const start = {value: 'tail', cursorOffset: 2};

	const below = applyVimNormalMode(start, 'o', {}, {
		moveLeft: (state) => state,
		moveRight: (state) => state,
		moveHome: (state) => state,
		moveEnd: (state) => state,
		movePrevWord: (state) => state,
		moveNextWord: (state) => state,
		deleteChar: (state) => state,
		openLineBelow: (state) => ({...state, value: '', cursorOffset: 0}),
	});
	assert.deepEqual(below, {
		handled: true,
		state: {value: '', cursorOffset: 0},
		mode: 'insert',
	});

	const above = applyVimNormalMode(start, 'O', {}, {
		moveLeft: (state) => state,
		moveRight: (state) => state,
		moveHome: (state) => state,
		moveEnd: (state) => state,
		movePrevWord: (state) => state,
		moveNextWord: (state) => state,
		deleteChar: (state) => state,
		openLineAbove: (state) => ({...state, value: '', cursorOffset: 0}),
	});
	assert.deepEqual(above, {
		handled: true,
		state: {value: '', cursorOffset: 0},
		mode: 'insert',
	});
});
