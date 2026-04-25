import assert from 'node:assert/strict';
import test from 'node:test';

import {createTerminalInputDecoder} from './terminalInput.js';

test('strips mouse wheel and click escape sequences before they reach the text composer', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push(`hello\u001b[<64;42;9M\u001b[<0;42;9M world`);

	assert.equal(result.text, 'hello world');
	assert.deepEqual(result.mouseEvents, [
		{kind: 'wheel', direction: 'up', buttonCode: 64},
		{kind: 'button', action: 'press', buttonCode: 0},
	]);
});

test('buffers partial mouse escape sequences across chunks', () => {
	const decoder = createTerminalInputDecoder();
	const first = decoder.push('abc\u001b[<65;10');
	const second = decoder.push(';4Mdef');

	assert.equal(first.text, 'abc');
	assert.deepEqual(first.mouseEvents, []);
	assert.equal(second.text, 'def');
	assert.deepEqual(second.mouseEvents, [
		{kind: 'wheel', direction: 'down', buttonCode: 65},
	]);
});
