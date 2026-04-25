import {useEffect, useState} from 'react';
import {useStdout} from 'ink';

export type TerminalSize = {rows: number; cols: number};

export function useTerminalSize(): TerminalSize {
	const {stdout} = useStdout();
	const [size, setSize] = useState<TerminalSize>(() => ({
		rows: stdout?.rows ?? 24,
		cols: stdout?.columns ?? 80,
	}));

	useEffect(() => {
		if (!stdout) return;
		const handler = (): void => {
			setSize({
				rows: stdout.rows ?? 24,
				cols: stdout.columns ?? 80,
			});
		};
		stdout.on('resize', handler);
		return () => {
			stdout.off('resize', handler);
		};
	}, [stdout]);

	return size;
}
