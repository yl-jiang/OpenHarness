import React from 'react';
import {Box} from 'ink';

export interface HalfLinePaddedBoxProps {
	/** Color for the horizontal separator lines. */
	lineColor: string;
	children: React.ReactNode;
}

/**
 * A container that renders thin horizontal-line separators at top and bottom.
 * Uses Ink's native Box border (top + bottom only) so separator width
 * auto-sizes to the available parent width — no manual column calculation.
 */
export function HalfLinePaddedBox({lineColor, children}: HalfLinePaddedBoxProps): React.JSX.Element {
	return (
		<Box
			flexDirection="column"
			flexShrink={0}
			borderStyle="single"
			borderLeft={false}
			borderRight={false}
			borderColor={lineColor}
		>
			{children}
		</Box>
	);
}
