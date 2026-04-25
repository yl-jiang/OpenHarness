import os from 'node:os';

function getHomeDir(): string | null {
	const homeDir = process.env.HOME || process.env.USERPROFILE || os.homedir();
	if (!homeDir) {
		return null;
	}
	return homeDir.replace(/[\\/]+$/, '');
}

export function formatDisplayPath(value: unknown): string {
	const path = String(value ?? '.');
	const homeDir = getHomeDir();
	if (!homeDir || !path) {
		return path;
	}
	if (path === homeDir) {
		return '~';
	}
	for (const separator of ['/', '\\']) {
		const prefix = `${homeDir}${separator}`;
		if (path.startsWith(prefix)) {
			return `~${path.slice(homeDir.length)}`;
		}
	}
	return path;
}
