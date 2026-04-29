import assert from 'node:assert/strict';
import test from 'node:test';

import {chunkTerminalTextForInk, createTerminalInputDecoder} from './terminalInput.js';

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

test('splits repeated backspace controls into separate chunks for Ink consumers', () => {
	assert.deepEqual(chunkTerminalTextForInk('\b\b\b'), ['\b', '\b', '\b']);
	assert.deepEqual(chunkTerminalTextForInk('\x7f\x7f'), ['\x7f', '\x7f']);
});

test('preserves normal text chunks while isolating backspace controls', () => {
	assert.deepEqual(chunkTerminalTextForInk('ab\x7f\x7fcd\b'), ['ab', '\x7f', '\x7f', 'cd', '\b']);
	assert.deepEqual(chunkTerminalTextForInk('hello'), ['hello']);
});

test('translates xterm modifyOtherKeys Shift+Enter (\\x1b[27;2;13~) into LF', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('abc\u001b[27;2;13~def');
	assert.equal(result.text, 'abc\ndef');
	assert.deepEqual(result.mouseEvents, []);
});

test('translates kitty Shift+Enter (\\x1b[13;2u) into LF', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('hi\u001b[13;2uok');
	assert.equal(result.text, 'hi\nok');
});

test('translates xterm modifyOtherKeys Shift+letter sequences into printable text', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('say \u001b[27;2;65~bc');
	assert.equal(result.text, 'say Abc');
	assert.deepEqual(result.mouseEvents, []);
});

test('translates kitty Shift+letter sequences into printable text', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('say \u001b[65;2ubc');
	assert.equal(result.text, 'say Abc');
	assert.deepEqual(result.mouseEvents, []);
});

test('translates Alt+Enter (\\x1b\\r) into LF', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('foo\u001b\rbar');
	assert.equal(result.text, 'foo\nbar');
});

test('leaves unmodified Enter modifyOtherKeys sequence as a CR', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('a\u001b[27;1;13~b');
	assert.equal(result.text, 'a\rb');
});

test('does not mangle unrelated escape sequences such as arrow keys', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('\u001b[A\u001b[B');
	assert.equal(result.text, '\u001b[A\u001b[B');
});
