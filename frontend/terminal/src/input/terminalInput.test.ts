import assert from 'node:assert/strict';
import test from 'node:test';

import {chunkTerminalTextForInk, createTerminalInputDecoder} from './terminalInput.js';

test('strips mouse wheel and click escape sequences before they reach the text composer', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push(`hello\u001b[<64;42;9M\u001b[<0;42;9M world`);

	assert.equal(result.text, 'hello world');
	assert.deepEqual(result.mouseEvents, [
		{kind: 'wheel', direction: 'up', buttonCode: 64, column: 42, row: 9},
		{kind: 'button', action: 'press', buttonCode: 0, column: 42, row: 9},
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
		{kind: 'wheel', direction: 'down', buttonCode: 65, column: 10, row: 4},
	]);
});

test('splits repeated backspace controls into separate chunks for Ink consumers', () => {
	assert.deepEqual(chunkTerminalTextForInk('\b\b\b'), ['\b', '\b', '\b']);
	assert.deepEqual(chunkTerminalTextForInk('\x7f\x7f'), ['\b', '\b']);
});

test('preserves normal text chunks while isolating backspace controls', () => {
	assert.deepEqual(chunkTerminalTextForInk('ab\x7f\x7fcd\b'), ['ab', '\b', '\b', 'cd', '\b']);
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

test('strips bracketed paste delimiters while preserving surrounding typed text', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('abc\u001b[200~pasted text\u001b[201~');
	assert.equal(result.text, 'abcpasted text');
});

test('buffers split bracketed paste content across chunks until the end marker arrives', () => {
	// Real terminals can split a single paste across many stdin data events.
	// The decoder must buffer the entire paste and emit it once, otherwise the
	// React composer receives multiple input bursts and races on `\n` boundaries.
	const decoder = createTerminalInputDecoder();
	const first = decoder.push('abc\u001b[20');
	const second = decoder.push('0~pasted ');
	const third = decoder.push('text\u001b[201');
	const fourth = decoder.push('~def');

	assert.equal(first.text, 'abc');
	assert.equal(second.text, '');
	assert.equal(third.text, '');
	assert.equal(fourth.text, 'pasted textdef');
});

test('normalises CR and CRLF line endings inside a bracketed paste to LF', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push(
		'\u001b[200~line1\r\nline2\rline3\nline4\u001b[201~',
	);
	assert.equal(result.text, 'line1\nline2\nline3\nline4');
});

test('emits a multi-line paste as a single chunked write to ink', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push(
		'\u001b[200~  line1\n  line2\n  line3\u001b[201~',
	);
	// Buffered paste is returned in one piece; chunkTerminalTextForInk then
	// keeps it as a single chunk because it contains no backspace control bytes.
	assert.equal(result.text, '  line1\n  line2\n  line3');
});

test('does not mangle unrelated escape sequences such as arrow keys', () => {
	const decoder = createTerminalInputDecoder();
	const result = decoder.push('\u001b[A\u001b[B');
	assert.equal(result.text, '\u001b[A\u001b[B');
});
