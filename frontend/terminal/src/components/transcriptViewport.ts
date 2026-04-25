export type TranscriptViewport = {
	offsetFromBottom: number;
	followOutput: boolean;
};

export function clampViewportOffset(
	offsetFromBottom: number,
	itemCount: number,
	windowSize: number,
): number {
	const safeWindowSize = Math.max(1, windowSize);
	const maxOffset = Math.max(0, itemCount - safeWindowSize);
	return Math.min(Math.max(0, offsetFromBottom), maxOffset);
}

export function selectTranscriptWindow<T>(
	items: readonly T[],
	viewport: TranscriptViewport,
	windowSize: number,
): T[] {
	const {start, end} = getTranscriptWindowRange(items.length, viewport, windowSize);
	return items.slice(start, end);
}

export function getTranscriptWindowRange(
	itemCount: number,
	viewport: TranscriptViewport,
	windowSize: number,
): {start: number; end: number; offsetFromBottom: number} {
	const safeWindowSize = Math.max(1, windowSize);
	const offsetFromBottom = clampViewportOffset(
		viewport.followOutput ? 0 : viewport.offsetFromBottom,
		itemCount,
		safeWindowSize,
	);
	const end = Math.max(0, itemCount - offsetFromBottom);
	const start = Math.max(0, end - safeWindowSize);
	return {start, end, offsetFromBottom};
}

export function advanceViewportForNewItems(
	viewport: TranscriptViewport,
	appendedCount: number,
): TranscriptViewport {
	if (appendedCount <= 0 || viewport.followOutput) {
		return {
			offsetFromBottom: viewport.followOutput ? 0 : viewport.offsetFromBottom,
			followOutput: viewport.followOutput,
		};
	}

	return {
		offsetFromBottom: viewport.offsetFromBottom + appendedCount,
		followOutput: false,
	};
}

export function scrollTranscriptViewport(
	viewport: TranscriptViewport,
	direction: 'up' | 'down',
	amount: number,
	itemCount: number,
	windowSize: number,
): TranscriptViewport {
	const delta = Math.max(1, amount);
	const rawOffset = direction === 'up'
		? viewport.offsetFromBottom + delta
		: viewport.offsetFromBottom - delta;
	const offsetFromBottom = clampViewportOffset(rawOffset, itemCount, windowSize);
	return {
		offsetFromBottom,
		followOutput: offsetFromBottom === 0,
	};
}

export function parseMouseWheelDirection(input: string): 'up' | 'down' | null {
	const match = /\u001b\[<(\d+);\d+;\d+[Mm]/.exec(input);
	if (!match) {
		return null;
	}
	const buttonCode = Number(match[1]);
	if (buttonCode === 64) {
		return 'up';
	}
	if (buttonCode === 65) {
		return 'down';
	}
	return null;
}
