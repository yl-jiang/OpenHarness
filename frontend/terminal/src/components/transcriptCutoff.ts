import type {TranscriptItem} from '../types.js';

/**
 * Compute the prefix length of `transcript` that is safe to commit to Ink's
 * <Static> region (i.e. flushed to the terminal's native scrollback).
 *
 * A prefix is "safe" when every `tool` item inside it has a matching
 * `tool_result` item (also inside the prefix).  Unfinished tool calls — and
 * everything after them — are kept in the live (dynamic) region so that the
 * UI can update them in place when the result finally arrives.
 *
 * The function is monotonically non-decreasing as new items are appended:
 *   - appending a `tool` only extends the open trailing block, so cutoff stays;
 *   - appending a `tool_result` may close the open block, advancing cutoff;
 *   - appending any other item closes any open trailing block and pushes
 *     cutoff to the very end (assuming tool/result counts balance, which is
 *     the protocol contract).
 *
 * That monotonic property is required by `<Static>` whose `items` array must
 * grow append-only.
 */
export function computeCommittedCutoff(transcript: readonly TranscriptItem[]): number {
	let i = transcript.length - 1;
	while (i >= 0) {
		const role = transcript[i].role;
		if (role !== 'tool' && role !== 'tool_result') {
			break;
		}
		i--;
	}
	const tailStart = i + 1;
	let openTools = 0;
	for (let k = tailStart; k < transcript.length; k++) {
		if (transcript[k].role === 'tool') {
			openTools++;
		} else if (transcript[k].role === 'tool_result' && openTools > 0) {
			openTools--;
		}
	}
	return openTools > 0 ? tailStart : transcript.length;
}
