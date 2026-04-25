import React, {useEffect} from 'react';
import {useStdout} from 'ink';

const ENTER_ALT = '\x1b[?1049h';
const LEAVE_ALT = '\x1b[?1049l';
// SGR mouse mode (1006) + basic press tracking (1000) lets us receive
// wheel-up/down ESC sequences from xterm-compatible terminals.  We
// deliberately skip 1002 (button-event drag tracking) so the user can still
// click-and-drag to select / copy text in their terminal.
const ENTER_MOUSE = '\x1b[?1000h\x1b[?1006h';
const LEAVE_MOUSE = '\x1b[?1006l\x1b[?1000l';
const HIDE_CURSOR = '\x1b[?25l';
const SHOW_CURSOR = '\x1b[?25h';

let installed = false;
let cleanupCallbacks: Array<() => void> = [];

function installSignalCleanup(cleanup: () => void): () => void {
	cleanupCallbacks.push(cleanup);
	if (installed) {
		return () => {
			cleanupCallbacks = cleanupCallbacks.filter((c) => c !== cleanup);
		};
	}
	installed = true;
	const runAll = (): void => {
		const cbs = cleanupCallbacks.slice();
		cleanupCallbacks = [];
		for (const cb of cbs) {
			try {
				cb();
			} catch {
				/* ignore */
			}
		}
	};
	process.on('exit', runAll);
	// SIGINT/SIGTERM are also handled in index.tsx; we only register cleanup
	// hooks here, never call process.exit() so Ink can still finish unmount.
	process.once('SIGINT', () => {
		runAll();
	});
	process.once('SIGTERM', () => {
		runAll();
	});
	return () => {
		cleanupCallbacks = cleanupCallbacks.filter((c) => c !== cleanup);
	};
}

/**
 * Switches the terminal into the alternate screen buffer for the lifetime of
 * this component and ensures it is restored when the component unmounts or
 * the process exits unexpectedly.
 *
 * Mouse tracking (so wheel events are delivered to the app) is controlled
 * separately via the `mouseTracking` prop.  Disabling it lets the user
 * click-and-drag to select / copy text in their terminal — at the cost of
 * losing in-app wheel scrolling.
 */
export function AlternateScreen({
	children,
	mouseTracking = true,
}: {
	children: React.ReactNode;
	mouseTracking?: boolean;
}): React.JSX.Element {
	const {stdout} = useStdout();

	useEffect(() => {
		if (!stdout) return;
		stdout.write(ENTER_ALT + HIDE_CURSOR);
		const cleanup = (): void => {
			try {
				stdout.write(LEAVE_MOUSE + LEAVE_ALT + SHOW_CURSOR);
			} catch {
				/* ignore */
			}
		};
		const unregister = installSignalCleanup(cleanup);
		return () => {
			cleanup();
			unregister();
		};
	}, [stdout]);

	useEffect(() => {
		if (!stdout) return;
		if (mouseTracking) {
			stdout.write(ENTER_MOUSE);
			return () => {
				try {
					stdout.write(LEAVE_MOUSE);
				} catch {
					/* ignore */
				}
			};
		}
		stdout.write(LEAVE_MOUSE);
		return undefined;
	}, [stdout, mouseTracking]);

	return <>{children}</>;
}
