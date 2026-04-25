import assert from 'node:assert/strict';
import test from 'node:test';

import {
	advanceViewportForNewItems,
	clampViewportOffset,
	parseMouseWheelDirection,
	selectTranscriptWindow,
} from './transcriptViewport.js';

test('keeps the same visible transcript window when new items arrive while reviewing history', () => {
	assert.equal(advanceViewportForNewItems({offsetFromBottom: 12, followOutput: false}, 3).offsetFromBottom, 15);
	assert.equal(advanceViewportForNewItems({offsetFromBottom: 0, followOutput: true}, 3).offsetFromBottom, 0);
});

test('selects earlier transcript items when the viewport is scrolled away from the bottom', () => {
	const items = Array.from({length: 60}, (_, index) => `message-${index + 1}`);
	const window = selectTranscriptWindow(items, {offsetFromBottom: 40, followOutput: false}, 20);

	assert.deepEqual(window, items.slice(0, 20));
});

test('clamps overscrolled viewports back into the valid transcript range', () => {
	assert.equal(clampViewportOffset(999, 60, 20), 40);
	assert.equal(clampViewportOffset(-5, 60, 20), 0);
});

test('parses SGR mouse wheel escape sequences', () => {
	assert.equal(parseMouseWheelDirection('\u001b[<64;42;9M'), 'up');
	assert.equal(parseMouseWheelDirection('\u001b[<65;42;9M'), 'down');
	assert.equal(parseMouseWheelDirection('plain text'), null);
});
