import React, {useMemo} from 'react';
import {Box, Text} from 'ink';

const MAX_VISIBLE = 10;

export function nextSelectIndex(currentIndex: number, delta: number, optionCount: number): number {
	if (optionCount <= 0) {
		return 0;
	}
	return (currentIndex + delta + optionCount) % optionCount;
}

export function nextSelectIndexForWheel(currentIndex: number, delta: number, optionCount: number): number {
	const direction = delta > 0 ? 1 : -1;
	return nextSelectIndex(currentIndex, direction, optionCount);
}

export type SelectOption = {
	value: string;
	label: string;
	description?: string;
	active?: boolean;
	badge?: string;
	badgeTone?: 'accent' | 'warning' | 'muted';
};

function badgeColor(tone?: SelectOption['badgeTone']): 'cyan' | 'yellow' | 'gray' {
	switch (tone) {
		case 'warning':
			return 'yellow';
		case 'muted':
			return 'gray';
		default:
			return 'cyan';
	}
}

export function SelectModal({
	title,
	command,
	options,
	selectedIndex,
	query,
	filterLabel,
	emptyStateLabel,
}: {
	title: string;
	command?: string;
	options: SelectOption[];
	selectedIndex: number;
	query?: string;
	filterLabel?: string;
	emptyStateLabel?: string;
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
	const hasFilter = typeof query === 'string';
	const trimmedQuery = query?.trim() ?? '';
	const isProviderModal = command?.trim().replace(/^\/+/, '').toLowerCase() === 'provider';

	return (
		<Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} marginTop={1}>
			<Text bold color="cyan">{title}{hasMore ? ` (${selectedIndex + 1}/${options.length})` : ''}</Text>
			<Text> </Text>
			{hasFilter ? (
				<>
					<Text dimColor>{filterLabel ?? 'Filter'}: {trimmedQuery || '—'}</Text>
					<Text> </Text>
				</>
			) : null}
			{windowStart > 0 ? <Text dimColor>  {'\u2191'} {windowStart} more</Text> : null}
			{visibleOptions.length === 0 ? (
				<Text dimColor>{emptyStateLabel ?? 'No matching options.'}</Text>
			) : (
				visibleOptions.map((opt, i) => {
					const realIndex = windowStart + i;
					const isSelected = realIndex === selectedIndex;
					const isCurrent = opt.active;
					if (isProviderModal) {
						return (
							<Box key={opt.value} flexDirection="column">
								<Box width="100%" justifyContent="space-between">
									<Text color={isSelected ? 'cyan' : undefined} bold={isSelected}>
										{isSelected ? '\u276F ' : '  '}
										{opt.label}
									</Text>
									{opt.badge ? (
										<Text color={badgeColor(opt.badgeTone)} bold={isSelected || opt.badgeTone === 'warning'}>
											[{opt.badge}]
										</Text>
									) : null}
								</Box>
								{opt.description ? (
									<Box marginLeft={2}>
										<Text dimColor>{opt.description}</Text>
									</Box>
								) : null}
							</Box>
						);
					}
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
				})
			)}
			{windowEnd < options.length ? <Text dimColor>  {'\u2193'} {options.length - windowEnd} more</Text> : null}
			<Text> </Text>
			<Text dimColor>
				{hasFilter ? 'type to filter  backspace delete  ' : ''}
				{'\u2191\u2193'} cycle{'  '}{'\u23CE'} select{'  '}esc cancel
			</Text>
		</Box>
	);
}
