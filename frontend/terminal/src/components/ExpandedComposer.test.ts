import assert from 'node:assert/strict';
import test from 'node:test';

import {
	applyExpandedComposerInput,
	completeLeadingCommand,
	createExpandedComposerState,
	getExpandedComposerSendHitbox,
	getPromptExpandTriggerHitbox,
	hitboxContainsPoint,
	insertComposerText,
	moveComposerCursor,
	splitExpandedDraft,
} from './ExpandedComposer.js';

test('insertComposerText preserves multiline paste and advances the cursor', () => {
	const state = createExpandedComposerState('hello');
	const next = insertComposerText(state, '\nworld');

	assert.equal(next.draft, 'hello\nworld');
	assert.equal(next.cursorOffset, 'hello\nworld'.length);
});

test('applyExpandedComposerInput treats raw backspace control bytes as deletion', () => {
	const deletedByDel = applyExpandedComposerInput(createExpandedComposerState('hello'), '\x7f');
	const deletedByBs = applyExpandedComposerInput(createExpandedComposerState('hello'), '\b');

	assert.equal(deletedByDel.draft, 'hell');
	assert.equal(deletedByDel.cursorOffset, 4);
	assert.equal(deletedByBs.draft, 'hell');
	assert.equal(deletedByBs.cursorOffset, 4);
});

test('moveComposerCursor preserves the preferred column across shorter lines', () => {
	const start = {
		...createExpandedComposerState('12345\n1\n1234'),
		cursorOffset: 4,
	};

	const downOnce = moveComposerCursor(start, 'down');
	const downTwice = moveComposerCursor(downOnce, 'down');

	assert.equal(downOnce.cursorOffset, 7);
	assert.equal(downTwice.cursorOffset, 12);
});

test('completeLeadingCommand replaces only the opening slash token', () => {
	const completed = completeLeadingCommand('  /sk\nrest of prompt', '/skills');

	assert.deepEqual(completed, {
		draft: '  /skills\nrest of prompt',
		cursorOffset: 9,
	});
});

test('splitExpandedDraft keeps trailing empty lines in the prompt buffer model', () => {
	assert.deepEqual(splitExpandedDraft('first line\nsecond line\n'), {
		extraInputLines: ['first line', 'second line'],
		input: '',
	});
});

test('anchor hitboxes stay pinned to the prompt and fullscreen editor edges', () => {
	const promptHitbox = getPromptExpandTriggerHitbox(120, 40);
	const sendHitbox = getExpandedComposerSendHitbox(120, 40);

	assert.deepEqual(promptHitbox, {column: 116, row: 39, width: 2, height: 1});
	assert.deepEqual(sendHitbox, {column: 117, row: 39, width: 1, height: 1});
	assert.equal(hitboxContainsPoint(promptHitbox, 116, 39), true);
	assert.equal(hitboxContainsPoint(promptHitbox, 117, 39), true);
	assert.equal(hitboxContainsPoint(promptHitbox, 115, 39), false);
	assert.equal(hitboxContainsPoint(sendHitbox, 117, 39), true);
	assert.equal(hitboxContainsPoint(sendHitbox, 117, 2), false);
});
