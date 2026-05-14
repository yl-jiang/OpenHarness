import {access, constants, readdir} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

export type ShellCompletionQuery = {
	kind: 'command' | 'path';
	start: number;
	query: string;
};

export type ShellCompletionCycle = {
	baseInput: string;
	query: ShellCompletionQuery;
	candidates: string[];
	index: number;
	value: string;
};

const WINDOWS_EXECUTABLE_EXTENSIONS = new Set(
	(process.env.PATHEXT ?? '.COM;.EXE;.BAT;.CMD')
		.split(';')
		.map((ext) => ext.trim().toLowerCase())
		.filter(Boolean),
);

export function findShellCompletionQuery(value: string): ShellCompletionQuery | null {
	if (!value || /\s$/.test(value)) {
		return null;
	}

	const match = /(?:^|\s)([^\s]+)$/.exec(value);
	if (!match) {
		return null;
	}

	const query = match[1] ?? '';
	if (!query) {
		return null;
	}

	const start = value.length - query.length;
	const leading = value.slice(0, start).trim();
	const kind = leading.length === 0 && !looksLikePath(query) ? 'command' : 'path';
	return {kind, start, query};
}

export function replaceShellCompletionQuery(
	value: string,
	query: ShellCompletionQuery,
	completion: string,
): string {
	const end = query.start + query.query.length;
	const prefix = value.slice(0, query.start);
	const suffix = value.slice(end);
	const spacer = completion.endsWith('/') || suffix.startsWith(' ') ? '' : ' ';
	return `${prefix}${completion}${spacer}${suffix}`;
}

export function advanceShellCompletionCycle(
	value: string,
	query: ShellCompletionQuery | null,
	candidates: string[],
	cycle: ShellCompletionCycle | null,
): {nextValue: string | null; nextCycle: ShellCompletionCycle | null} {
	if (cycle && cycle.value === value && cycle.candidates.length > 0) {
		const nextIndex = (cycle.index + 1) % cycle.candidates.length;
		const nextValue = replaceShellCompletionQuery(cycle.baseInput, cycle.query, cycle.candidates[nextIndex]!);
		return {
			nextValue,
			nextCycle: {
				...cycle,
				index: nextIndex,
				value: nextValue,
			},
		};
	}

	if (!query || candidates.length === 0) {
		return {nextValue: null, nextCycle: null};
	}

	const nextValue = replaceShellCompletionQuery(value, query, candidates[0]!);
	return {
		nextValue,
		nextCycle: {
			baseInput: value,
			query,
			candidates: [...candidates],
			index: 0,
			value: nextValue,
		},
	};
}

export function filterShellCommandCandidates(commands: string[], query: string): string[] {
	const needle = query.trim().toLowerCase();
	if (!needle) {
		return [];
	}

	return commands
		.filter((command) => command.toLowerCase().startsWith(needle))
		.sort((left, right) => {
			if (left.length !== right.length) {
				return left.length - right.length;
			}
			return left.localeCompare(right);
		});
}

export async function discoverShellCommandCandidates(
	pathEnv: string = process.env.PATH ?? '',
	limit = 2000,
	query = '',
): Promise<string[]> {
	const commands = new Set<string>();
	const needle = query.trim().toLowerCase();
	const directories = pathEnv
		.split(path.delimiter)
		.map((directory) => directory.trim())
		.filter(Boolean);

	for (const directory of directories) {
		if (commands.size >= limit) {
			break;
		}

		let entries;
		try {
			entries = await readdir(directory, {withFileTypes: true});
		} catch {
			continue;
		}

		for (const entry of entries) {
			if (commands.size >= limit) {
				break;
			}
			if (!entry.isFile() && !entry.isSymbolicLink()) {
				continue;
			}
			if (needle && !entry.name.toLowerCase().startsWith(needle)) {
				continue;
			}
			if (!await isExecutableFile(path.join(directory, entry.name))) {
				continue;
			}
			commands.add(entry.name);
		}
	}

	return [...commands].sort((left, right) => left.localeCompare(right));
}

export async function discoverShellPathCandidates(
	cwd: string,
	query: string,
	limit = 200,
): Promise<string[]> {
	if (!query) {
		return [];
	}
	if (query === '~') {
		return ['~/'];
	}

	const slashIndex = query.lastIndexOf('/');
	const basePrefix = slashIndex >= 0 ? query.slice(0, slashIndex + 1) : '';
	const namePrefix = slashIndex >= 0 ? query.slice(slashIndex + 1) : query;
	const lookupDirectory = resolveLookupDirectory(cwd, basePrefix);

	let entries;
	try {
		entries = await readdir(lookupDirectory, {withFileTypes: true});
	} catch {
		return [];
	}

	const candidates = entries
		.filter((entry) => matchesPathPrefix(entry.name, namePrefix))
		.map((entry) => ({
			value: `${basePrefix}${entry.name}${entry.isDirectory() ? '/' : ''}`,
			isDirectory: entry.isDirectory(),
		}))
		.sort((left, right) => {
			if (left.isDirectory !== right.isDirectory) {
				return left.isDirectory ? -1 : 1;
			}
			return left.value.localeCompare(right.value);
		})
		.slice(0, limit)
		.map((entry) => entry.value);

	return candidates;
}

function looksLikePath(query: string): boolean {
	return (
		query.startsWith('./') ||
		query.startsWith('../') ||
		query.startsWith('/') ||
		query === '~' ||
		query.startsWith('~/') ||
		query.includes('/')
	);
}

async function isExecutableFile(filePath: string): Promise<boolean> {
	if (process.platform === 'win32') {
		return WINDOWS_EXECUTABLE_EXTENSIONS.has(path.extname(filePath).toLowerCase());
	}
	try {
		await access(filePath, constants.X_OK);
		return true;
	} catch {
		return false;
	}
}

function matchesPathPrefix(candidate: string, prefix: string): boolean {
	if (process.platform === 'win32') {
		return candidate.toLowerCase().startsWith(prefix.toLowerCase());
	}
	return candidate.startsWith(prefix);
}

function resolveLookupDirectory(cwd: string, basePrefix: string): string {
	if (!basePrefix) {
		return cwd;
	}
	if (basePrefix === '~/') {
		return os.homedir();
	}
	if (basePrefix.startsWith('~/')) {
		return path.resolve(os.homedir(), basePrefix.slice(2));
	}
	if (path.isAbsolute(basePrefix)) {
		return path.resolve(basePrefix);
	}
	return path.resolve(cwd, basePrefix);
}
