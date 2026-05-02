import {useEffect} from 'react';
import {useStdin} from 'ink';

import type {TerminalInputStream, TerminalMouseEvent} from '../input/terminalInput.js';

export function useTerminalMouse(handler: (event: TerminalMouseEvent) => void): void {
	const {stdin} = useStdin();

	useEffect(() => {
		if (!stdin) {
			return;
		}
		const stream = stdin as unknown as TerminalInputStream;
		if (typeof stream.on !== 'function') {
			return;
		}
		stream.on('mouse', handler);
		return () => {
			stream.off('mouse', handler);
		};
	}, [stdin, handler]);
}
