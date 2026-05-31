/**
 * Synchronized Output (DEC private mode 2026) + full-screen clear elimination.
 *
 * 1. Wraps stdout writes in begin/end markers so terminals that support mode
 *    2026 buffer all output and render it atomically.
 *
 * 2. Rewrites Ink's full-screen clear sequence (clearTerminal = \x1b[2J\x1b[3J\x1b[H)
 *    to cursor-home + per-line clear-to-end-of-line.  This avoids the blank-screen
 *    flash that occurs when the terminal processes the screen clear before the
 *    replacement content arrives.
 *
 * 3. Maintains a screen buffer (plain text lines) of the last rendered frame
 *    for app-level text selection in copy mode.
 *
 * Terminals that do not support mode 2026 simply ignore the BSU/ESU escapes.
 */

const BSU = '\x1b[?2026h'; // Begin Synchronized Update
const ESU = '\x1b[?2026l'; // End Synchronized Update

// Ink emits this prefix when outputHeight >= stdout.rows (always true for
// full-height apps).  It clears the entire screen + scrollback, causing a
// visible flash even with sync output on many terminals.
const CLEAR_TERMINAL = '\x1b[2J\x1b[3J\x1b[H';
const CURSOR_HOME = '\x1b[H';
const CLEAR_TO_EOL = '\x1b[K';
const CLEAR_TO_EOS = '\x1b[J';

// Strip ANSI escape sequences to get plain text
const ANSI_ESCAPE_RE = /\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?(?:\x07|\x1b\\)/g;

let screenLines: string[] = [];

/**
 * Returns the plain-text lines currently displayed on the terminal.
 * Each array entry corresponds to one terminal row.
 */
export function getScreenLines(): readonly string[] {
	return screenLines;
}

type WriteCallback = (error?: Error | null) => void;
type WriteFn = (chunk: any, encodingOrCallback?: BufferEncoding | WriteCallback, callback?: WriteCallback) => boolean;

/**
 * Replace Ink's `clearTerminal + output` with an in-place overwrite:
 *   cursor-home → line₁ clear-EOL ↵ line₂ clear-EOL ↵ … lineₙ clear-EOL clear-EOS
 *
 * Each line's leftover characters from the previous frame are erased by
 * clear-to-end-of-line, and any extra lines at the bottom are erased by
 * clear-to-end-of-screen.  The screen is never blank between frames.
 *
 * Also captures the frame content as plain text lines for copy-mode selection.
 */
function rewriteFullScreenClear(chunk: string): string {
	if (!chunk.startsWith(CLEAR_TERMINAL)) {
		return chunk;
	}
	const content = chunk.slice(CLEAR_TERMINAL.length);
	const lines = content.split('\n');
	// Capture plain-text screen buffer (strip ANSI escape sequences)
	screenLines = lines.map((line) => line.replace(ANSI_ESCAPE_RE, ''));
	return CURSOR_HOME + lines.join(CLEAR_TO_EOL + '\n') + CLEAR_TO_EOL + CLEAR_TO_EOS;
}

/**
 * Install a stdout write wrapper that batches all writes within a single
 * event-loop tick into a synchronized update frame.  This is safe to call
 * multiple times — only the first call installs the patch.
 */
export function installSyncOutput(stdout: NodeJS.WriteStream): void {
	if (!stdout?.isTTY) return;
	if ((stdout as any).__syncOutputInstalled) return;
	(stdout as any).__syncOutputInstalled = true;

	const originalWrite: WriteFn = stdout.write.bind(stdout);
	let frameOpen = false;
	let closeScheduled = false;

	const closeFrame = (): void => {
		closeScheduled = false;
		if (frameOpen) {
			frameOpen = false;
			originalWrite(ESU);
		}
	};

	stdout.write = function patchedWrite(
		chunk: any,
		encodingOrCallback?: BufferEncoding | WriteCallback,
		callback?: WriteCallback,
	): boolean {
		if (typeof chunk === 'string') {
			chunk = rewriteFullScreenClear(chunk);
		}
		if (!frameOpen) {
			frameOpen = true;
			originalWrite(BSU);
		}
		if (!closeScheduled) {
			closeScheduled = true;
			// Use setTimeout(0) instead of setImmediate so the BSU frame stays
			// open until ALL cascading React renders (scheduled via setImmediate
			// by React's scheduler) have flushed.  setImmediate would fire
			// before those renders, exposing an intermediate wrong-scroll frame.
			// setTimeout(0) fires in the timers phase, after the check phase
			// where all setImmediate callbacks run, so every cascading re-render
			// is batched into the same synchronized update block.
			setTimeout(closeFrame, 0);
		}
		return originalWrite(chunk, encodingOrCallback as any, callback);
	} as any;
}
