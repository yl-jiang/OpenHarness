import React, {useMemo} from 'react';
import {Box, Text} from 'ink';

const MAX_VISIBLE = 10;

export function nextSelectIndexForWheel(currentIndex: number, delta: number, optionCount: number): number {
	const direction = delta > 0 ? 1 : -1;
	return Math.max(0, Math.min(optionCount - 1, currentIndex + direction));
}

export type SelectOption = {
	value: string;
	label: string;
	description?: string;
	active?: boolean;
};

export function SelectModal({
	title,
	options,
	selectedIndex,
}: {
	title: string;
	options: SelectOption[];
	selectedIndex: number;
}): React.JSX.Element {
	const {windowStart, windowEnd} = useMemo(() => {
		if (options.length <= MAX_VISIBLE) {
			return {windowStart: 0, windowEnd: options.length};
		}
		let start = selectedIndex - Math.floor(MAX_VISIBLE / 2);
		start = Math.max(0, Math.min(start, options.length - MAX_VISIBLE));
		return {windowStart: start, windowEnd: start + MAX_VISIBLE};
	}, [options.length, selectedIndex]);
	const visibleOptions = options.slice(windowStart, windowEnd);
	const hasMore = options.length > MAX_VISIBLE;

	return (
		<Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} marginTop={1}>
			<Text bold color="cyan">{title}{hasMore ? ` (${selectedIndex + 1}/${options.length})` : ''}</Text>
			<Text> </Text>
			{windowStart > 0 ? <Text dimColor>  {'\u2191'} {windowStart} more</Text> : null}
			{visibleOptions.map((opt, i) => {
				const realIndex = windowStart + i;
				const isSelected = realIndex === selectedIndex;
				const isCurrent = opt.active;
				return (
					<Box key={opt.value} flexDirection="column">
						<Text color={isSelected ? 'cyan' : undefined} bold={isSelected} inverse={isSelected}>
							{isSelected ? '\u276F ' : '  '}
							- {opt.label}
							{isCurrent ? ' (current)' : ''}
						</Text>
						{opt.description ? (
							<Box marginLeft={6}>
								<Text dimColor>{opt.description}</Text>
							</Box>
						) : null}
					</Box>
				);
			})}
			{windowEnd < options.length ? <Text dimColor>  {'\u2193'} {options.length - windowEnd} more</Text> : null}
			<Text> </Text>
			<Text dimColor>{'\u2191\u2193'} navigate{'  '}{'\u23CE'} select{'  '}esc cancel</Text>
		</Box>
	);
}
