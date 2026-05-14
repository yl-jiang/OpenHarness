import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
	applyPastePlaceholder,
	buildPastePlaceholder,
	detectSummarizablePasteInsertion,
	listPasteStageNotices,
	prunePasteReferences,
	resolvePasteSubmission,
	stagePasteInTempFile,
	type PasteReference,
} from './pastePlaceholders.js';

test('detects a 10-line paste in the current line and replaces it with a placeholder', () => {
	const pastedText = Array.from({length: 10}, (_value, index) => `line-${index + 1}`).join('\n');
	const detected = detectSummarizablePasteInsertion('before-after', `before${pastedText}-after`);

	assert.ok(detected);
	assert.equal(detected?.lineCount, 10);
	assert.equal(detected?.pastedText, pastedText);

	const placeholder = buildPastePlaceholder(1, detected!.lineCount);
	assert.equal(
		applyPastePlaceholder('before-after', detected!, placeholder),
		'before[Paste #1 - 10 lines]-after',
	);
});

test('expands in-memory paste placeholders before submission while preserving the transcript summary', () => {
	const placeholder = buildPastePlaceholder(1, 12);
	const pastedText = Array.from({length: 12}, (_value, index) => `row-${index + 1}`).join('\n');
	const references: Record<string, PasteReference> = {
		[placeholder]: {
			label: placeholder,
			lineCount: 12,
			storage: 'memory',
			content: pastedText,
		},
	};

	assert.deepEqual(resolvePasteSubmission(`inspect ${placeholder}`, references), {
		line: `inspect ${pastedText}`,
		transcriptLine: `inspect ${placeholder}`,
	});
	assert.deepEqual(prunePasteReferences('inspect plain text', references), {});
});

test('stages very large pastes in a temp file and surfaces an in-composer notice', () => {
	const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'openharness-paste-test-'));
	try {
		const placeholder = buildPastePlaceholder(2, 101);
		const pastedText = Array.from({length: 101}, (_value, index) => `block-${index + 1}`).join('\n');
		const staged = stagePasteInTempFile({
			label: placeholder,
			lineCount: 101,
			content: pastedText,
			baseDir: tempRoot,
		});

		assert.equal(staged.storage, 'temp_file');
		assert.equal(fs.readFileSync(staged.tempFilePath, 'utf8'), pastedText);
		assert.deepEqual(listPasteStageNotices(`review ${placeholder}`, {[placeholder]: staged}), [
			`${placeholder} saved to ${staged.tempFilePath}`,
		]);
		assert.deepEqual(resolvePasteSubmission(`review ${placeholder}`, {[placeholder]: staged}), {
			line: `review ${pastedText}`,
			transcriptLine: `review ${placeholder}`,
		});
	} finally {
		fs.rmSync(tempRoot, {recursive: true, force: true});
	}
});
