import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {discoverMentionFiles, filterMentionCandidates, findMentionQuery, replaceMentionQuery} from './mentions.js';

test('findMentionQuery detects @ only when it starts a whitespace-delimited token', () => {
	assert.deepEqual(findMentionQuery('read @src/app'), {start: 5, query: 'src/app'});
	assert.deepEqual(findMentionQuery('@README'), {start: 0, query: 'README'});
	assert.equal(findMentionQuery('email a@b'), null);
	assert.equal(findMentionQuery('read @src app'), null);
});

test('replaceMentionQuery replaces just the active @ token and appends a space', () => {
	assert.equal(replaceMentionQuery('read @sr please', {start: 5, query: 'sr'}, 'src/app.py'), 'read @src/app.py please');
	assert.equal(replaceMentionQuery('read @sr', {start: 5, query: 'sr'}, 'src/app.py'), 'read @src/app.py ');
});

test('filterMentionCandidates ranks starts-with matches before substring matches', () => {
	assert.deepEqual(filterMentionCandidates(['docs/guide.md', 'src/app.py', 'tests/test_app.py'], 'app'), [
		'src/app.py',
		'tests/test_app.py',
	]);
});

test('filterMentionCandidates returns all matching files without truncation', () => {
	const files = Array.from({length: 20}, (_, i) => `src/file${i}.ts`);
	const results = filterMentionCandidates(files, 'file');
	assert.equal(results.length, 20);
});

test('discoverMentionFiles lists workspace files while skipping dependency, vcs, and hidden directories', async () => {
	const root = await mkdtemp(path.join(tmpdir(), 'openharness-mentions-'));
	await mkdir(path.join(root, 'src'), {recursive: true});
	await mkdir(path.join(root, 'node_modules', 'pkg'), {recursive: true});
	await mkdir(path.join(root, '.git'), {recursive: true});
	await mkdir(path.join(root, '.github'), {recursive: true});
	await writeFile(path.join(root, 'src', 'app.ts'), '');
	await writeFile(path.join(root, '.gitignore'), '');
	await writeFile(path.join(root, 'node_modules', 'pkg', 'index.js'), '');
	await writeFile(path.join(root, '.git', 'HEAD'), '');
	await writeFile(path.join(root, '.github', 'ci.yml'), '');

	assert.deepEqual(await discoverMentionFiles(root), ['src/app.ts']);
});
