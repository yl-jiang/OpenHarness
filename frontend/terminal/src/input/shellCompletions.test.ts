import assert from 'node:assert/strict';
import {chmod, mkdir, mkdtemp, rm, writeFile} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import {
	advanceShellCompletionCycle,
	discoverShellCommandCandidates,
	discoverShellPathCandidates,
	filterShellCommandCandidates,
	findShellCompletionQuery,
	replaceShellCompletionQuery,
} from './shellCompletions.js';

test('findShellCompletionQuery treats the first shell token as a command lookup', () => {
	assert.deepEqual(findShellCompletionQuery('pyth'), {kind: 'command', start: 0, query: 'pyth'});
	assert.deepEqual(findShellCompletionQuery('  git'), {kind: 'command', start: 2, query: 'git'});
});

test('findShellCompletionQuery treats later shell tokens as path lookups', () => {
	assert.deepEqual(findShellCompletionQuery('cat REA'), {kind: 'path', start: 4, query: 'REA'});
	assert.deepEqual(findShellCompletionQuery('./scr'), {kind: 'path', start: 0, query: './scr'});
	assert.equal(findShellCompletionQuery('cat '), null);
});

test('replaceShellCompletionQuery appends a space for files and keeps directory suffixes open', () => {
	assert.equal(
		replaceShellCompletionQuery('cat REA', {kind: 'path', start: 4, query: 'REA'}, 'README.md'),
		'cat README.md ',
	);
	assert.equal(
		replaceShellCompletionQuery('cd sr', {kind: 'path', start: 3, query: 'sr'}, 'src/'),
		'cd src/',
	);
});

test('filterShellCommandCandidates uses bash-style prefix matching', () => {
	assert.deepEqual(
		filterShellCommandCandidates(['tail', 'containerd', 'taillog'], 'tai'),
		['tail', 'taillog'],
	);
	assert.deepEqual(
		filterShellCommandCandidates(['python', 'ipython', 'ptpython'], 'py'),
		['python'],
	);
});

test('advanceShellCompletionCycle cycles shell completions in place without needing a new query', () => {
	const first = advanceShellCompletionCycle(
		'openharness-shell-cycle-',
		{kind: 'command', start: 0, query: 'openharness-shell-cycle-'},
		['openharness-shell-cycle-alpha', 'openharness-shell-cycle-beta'],
		null,
	);
	assert.equal(first.nextValue, 'openharness-shell-cycle-alpha ');
	assert.equal(first.nextCycle?.index, 0);

	const second = advanceShellCompletionCycle(
		first.nextValue ?? '',
		null,
		[],
		first.nextCycle ?? null,
	);
	assert.equal(second.nextValue, 'openharness-shell-cycle-beta ');
	assert.equal(second.nextCycle?.index, 1);

	const third = advanceShellCompletionCycle(
		second.nextValue ?? '',
		null,
		[],
		second.nextCycle ?? null,
	);
	assert.equal(third.nextValue, 'openharness-shell-cycle-alpha ');
	assert.equal(third.nextCycle?.index, 0);
});

test('discoverShellCommandCandidates scans PATH for executables and deduplicates names', async () => {
	const root = await mkdtemp(path.join(os.tmpdir(), 'oh-shell-cmds-'));
	const binA = path.join(root, 'bin-a');
	const binB = path.join(root, 'bin-b');

	await mkdir(binA);
	await mkdir(binB);
	await writeFile(path.join(binA, 'demo-tool'), '#!/bin/sh\n');
	await chmod(path.join(binA, 'demo-tool'), 0o755);
	await writeFile(path.join(binA, 'not-executable'), '#!/bin/sh\n');
	await chmod(path.join(binA, 'not-executable'), 0o644);
	await writeFile(path.join(binB, 'demo-tool'), '#!/bin/sh\n');
	await chmod(path.join(binB, 'demo-tool'), 0o755);
	await writeFile(path.join(binB, 'other-tool'), '#!/bin/sh\n');
	await chmod(path.join(binB, 'other-tool'), 0o755);

	try {
		const commands = await discoverShellCommandCandidates(`${binA}${path.delimiter}${binB}`);
		assert.deepEqual(commands, ['demo-tool', 'other-tool']);
	} finally {
		await rm(root, {recursive: true, force: true});
	}
});

test('discoverShellPathCandidates completes relative paths from the current working directory', async () => {
	const root = await mkdtemp(path.join(os.tmpdir(), 'oh-shell-paths-'));

	await mkdir(path.join(root, 'src', 'nested'), {recursive: true});
	await writeFile(path.join(root, 'src', 'app.ts'), 'console.log("hi");\n');
	await writeFile(path.join(root, 'README.md'), '# hi\n');

	try {
		assert.deepEqual(await discoverShellPathCandidates(root, 'REA'), ['README.md']);
		assert.deepEqual(await discoverShellPathCandidates(root, 'sr'), ['src/']);
		assert.deepEqual(await discoverShellPathCandidates(root, 'src/a'), ['src/app.ts']);
	} finally {
		await rm(root, {recursive: true, force: true});
	}
});
