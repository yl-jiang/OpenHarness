/**
 * Synchronized Output (DEC private mode 2026).
 *
 * Wraps stdout writes in begin/end markers so terminals that support this mode
 * buffer all output and render it atomically.  This eliminates flicker caused
 * by partial frame display, which is especially noticeable on high-latency
 * connections (SSH from Windows to Linux, for example).
 *
 * Terminals that do not support mode 2026 simply ignore the escape sequences.
 */

const BSU = '\x1b[?2026h'; // Begin Synchronized Update
const ESU = '\x1b[?2026l'; // End Synchronized Update

type WriteCallback = (error?: Error | null) => void;
type WriteFn = (chunk: any, encodingOrCallback?: BufferEncoding | WriteCallback, callback?: WriteCallback) => boolean;

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
		if (!frameOpen) {
			frameOpen = true;
			originalWrite(BSU);
		}
		if (!closeScheduled) {
			closeScheduled = true;
			setImmediate(closeFrame);
		}
		return originalWrite(chunk, encodingOrCallback as any, callback);
	} as any;
}
