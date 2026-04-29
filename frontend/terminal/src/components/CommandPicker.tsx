import React, {useMemo} from 'react';
import {Box, Text} from 'ink';

const MAX_VISIBLE = 10;

function CommandPickerInner({
	hints,
	selectedIndex,
	title = 'Commands',
}: {
	hints: string[];
	selectedIndex: number;
	title?: string;
}): React.JSX.Element | null {
	if (hints.length === 0) {
		return null;
	}

	const {windowStart, windowEnd} = useMemo(() => {
		if (hints.length <= MAX_VISIBLE) {
			return {windowStart: 0, windowEnd: hints.length};
		}
		let start = selectedIndex - Math.floor(MAX_VISIBLE / 2);
		start = Math.max(0, Math.min(start, hints.length - MAX_VISIBLE));
		return {windowStart: start, windowEnd: start + MAX_VISIBLE};
	}, [hints.length, selectedIndex]);

	const visibleHints = hints.slice(windowStart, windowEnd);
	const hasMore = hints.length > MAX_VISIBLE;

	return (
		<Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} marginBottom={0}>
			<Text dimColor bold> {title}{hasMore ? ` (${selectedIndex + 1}/${hints.length})` : ''}</Text>
			{windowStart > 0 ? <Text dimColor>  ↑ {windowStart} more</Text> : null}
			{visibleHints.map((hint, i) => {
				const realIndex = windowStart + i;
				const isSelected = realIndex === selectedIndex;
				return (
					<Box key={hint}>
						<Text color={isSelected ? 'cyan' : undefined} bold={isSelected}>
							{isSelected ? '\u276F ' : '  '}
							{hint}
						</Text>
						{isSelected ? <Text dimColor> [enter]</Text> : null}
					</Box>
				);
			})}
			{windowEnd < hints.length ? <Text dimColor>  ↓ {hints.length - windowEnd} more</Text> : null}
			<Text dimColor> {'\u2191\u2193'} navigate{'  '}{'\u23CE'} select{'  '}tab complete{'  '}esc dismiss</Text>
		</Box>
	);
}

export const CommandPicker = React.memo(CommandPickerInner);
