import {useEffect, useRef, useState} from 'react';

/**
 * Tracks elapsed seconds since the last time `active` became true.
 * While active, the value increments every second.
 * When active becomes false, the value freezes at the final elapsed time.
 * Returns null before the first activation.
 */
export function useElapsedTimer(active: boolean, startedAtSeconds?: number | null): number | null {
	const [elapsed, setElapsed] = useState<number | null>(null);
	const startTimeRef = useRef<number | null>(null);
	const intervalRef = useRef<NodeJS.Timeout | null>(null);

	useEffect(() => {
		if (active) {
			startTimeRef.current = startedAtSeconds != null ? startedAtSeconds * 1000 : (startTimeRef.current ?? Date.now());
			setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000));

			intervalRef.current = setInterval(() => {
				if (startTimeRef.current !== null) {
					setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000));
				}
			}, 1000);
		} else {
			if (intervalRef.current) {
				clearInterval(intervalRef.current);
				intervalRef.current = null;
			}
			if (startTimeRef.current !== null) {
				setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000));
			}
		}

		return () => {
			if (intervalRef.current) {
				clearInterval(intervalRef.current);
				intervalRef.current = null;
			}
		};
	}, [active, startedAtSeconds]);

	return elapsed;
}
