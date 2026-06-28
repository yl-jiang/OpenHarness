import React, {useEffect} from 'react';
import {useStdout} from 'ink';

const ENTER_ALT = '\x1b[?1049h';
const LEAVE_ALT = '\x1b[?1049l';
// Mouse tracking is disabled by default.  Enabling even basic button tracking
// (mode 1000) causes most terminal emulators to route mouse events to the
// application and disable native click-drag text selection.  Keeping all mouse
// modes off lets users select / copy text with the terminal's native behavior
// (Cmd+C / Ctrl+Shift+C) — matching kimi-code.  The mouseTracking prop is kept
// so wheel/click handling can be opted back in when selection is not required.
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
 * Mouse tracking is disabled by default so the terminal's native click-drag
 * selection and copy behavior (Cmd+C / Ctrl+Shift+C) works — matching
 * kimi-code.  Pass `mouseTracking={true}` to opt into wheel/click events at
 * the cost of losing native selection.
 */
export function AlternateScreen({
	children,
	mouseTracking = false,
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
