import {useEffect} from 'react';
import {useStdin} from 'ink';

import type {TerminalInputStream, TerminalMouseEvent} from '../input/terminalInput.js';

/**
 * Subscribe to wheel events from the wrapped terminal input stream.
 *
 * `createTerminalInputStream` (see src/input/terminalInput.ts) decodes SGR
 * mouse sequences out-of-band and emits them as `'mouse'` events on the
 * stream so they never reach the Ink text input.  We hook into that channel
 * instead of re-parsing raw stdin, which would race with the decoder and
 * miss events that the decoder swallowed mid-chunk.
 *
 * `delta` is -1 for wheel-up (scroll back / show earlier history) and +1 for
 * wheel-down (scroll forward / toward the live tail).
 */
export function useMouseWheel(handler: (delta: number) => void): void {
const {stdin} = useStdin();

useEffect(() => {
if (!stdin) return;
const stream = stdin as unknown as TerminalInputStream;
if (typeof stream.on !== 'function') return;
const onMouse = (event: TerminalMouseEvent): void => {
if (event.kind !== 'wheel') return;
handler(event.direction === 'up' ? -1 : 1);
};
stream.on('mouse', onMouse);
return () => {
stream.off('mouse', onMouse);
};
}, [stdin, handler]);
}
