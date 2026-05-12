import {readdir} from 'node:fs/promises';
import path from 'node:path';

export type MentionQuery = {
	start: number;
	query: string;
};

const SKIPPED_DIRS = new Set(['venv', 'node_modules', '__pycache__', 'dist', 'build']);

export function findMentionQuery(value: string, cursor = value.length): MentionQuery | null {
	const beforeCursor = value.slice(0, cursor);
	const start = beforeCursor.lastIndexOf('@');
	if (start === -1) {
		return null;
	}
	const previous = start === 0 ? '' : value[start - 1];
	if (previous && !/\s/.test(previous)) {
		return null;
	}
	const query = beforeCursor.slice(start + 1);
	if (/\s/.test(query)) {
		return null;
	}
	return {start, query};
}

export function replaceMentionQuery(value: string, mention: MentionQuery, filePath: string): string {
	const end = mention.start + 1 + mention.query.length;
	const prefix = value.slice(0, mention.start);
	const suffix = value.slice(end);
	const replacement = `@${filePath}`;
	const spacer = suffix.startsWith(' ') ? '' : ' ';
	return `${prefix}${replacement}${spacer}${suffix}`;
}

export function filterMentionCandidates(files: string[], query: string): string[] {
	const needle = query.toLowerCase();
	return files
		.filter((file) => {
			if (!needle) {
				return true;
			}
			return file.toLowerCase().includes(needle);
		})
		.sort((a, b) => {
			const aBase = path.posix.basename(a).toLowerCase();
			const bBase = path.posix.basename(b).toLowerCase();
			const aStarts = needle ? Number(aBase.startsWith(needle)) : 0;
			const bStarts = needle ? Number(bBase.startsWith(needle)) : 0;
			if (aStarts !== bStarts) {
				return bStarts - aStarts;
			}
			const aDepth = a.split('/').length;
			const bDepth = b.split('/').length;
			if (aDepth !== bDepth) {
				return aDepth - bDepth;
			}
			return a.localeCompare(b);
		});
}

export async function discoverMentionFiles(cwd: string, limit = 2000): Promise<string[]> {
	const root = path.resolve(cwd);
	const files: string[] = [];

	async function walk(directory: string): Promise<void> {
		if (files.length >= limit) {
			return;
		}
		let entries;
		try {
			entries = await readdir(directory, {withFileTypes: true});
		} catch {
			return;
		}
		for (const entry of entries) {
			if (files.length >= limit) {
				return;
			}
			if (entry.isDirectory()) {
				if (!entry.name.startsWith('.') && !SKIPPED_DIRS.has(entry.name)) {
					await walk(path.join(directory, entry.name));
				}
				continue;
			}
			if (!entry.isFile() || entry.name.startsWith('.')) {
				continue;
			}
			files.push(path.relative(root, path.join(directory, entry.name)).split(path.sep).join('/'));
		}
	}

	await walk(root);
	return files.sort((a, b) => a.localeCompare(b));
}
