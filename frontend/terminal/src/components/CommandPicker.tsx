import React, {useMemo} from 'react';
import {Box, Text} from 'ink';

const MAX_VISIBLE = 10;
const SUBMENU_MARKER = '›';

export type CommandPickerModel = {
	hints: string[];
	subHintsByHint: Record<string, string[]>;
};

export function createCommandPickerModel(commands: string[], input: string): CommandPickerModel {
	const value = input.trimStart();
	if (!value.startsWith('/')) {
		return {hints: [], subHintsByHint: {}};
	}

	if (/\s/.test(value.slice(1))) {
		return {
			hints: commands.filter((command) => command.startsWith(value)),
			subHintsByHint: {},
		};
	}

	const hints: string[] = [];
	const seen = new Set<string>();
	const subHintsByHint: Record<string, string[]> = {};

	for (const command of commands) {
		const [root, ...rest] = command.split(/\s+/);
		if (!root?.startsWith(value)) {
			continue;
		}
		if (!seen.has(root)) {
			seen.add(root);
			hints.push(root);
		}
		if (rest.length > 0) {
			const subHint = rest.join(' ');
			subHintsByHint[root] = [...(subHintsByHint[root] ?? []), subHint];
		}
	}

	return {hints, subHintsByHint};
}

function CommandPickerInner({
	hints,
	selectedIndex,
	title = 'Commands',
	subHintsByHint = {},
}: {
	hints: string[];
	selectedIndex: number;
	title?: string;
	subHintsByHint?: Record<string, string[]>;
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
	const selectedHint = hints[selectedIndex];
	const subHints = selectedHint ? (subHintsByHint[selectedHint] ?? []) : [];
	const visibleSubHints = subHints.slice(0, MAX_VISIBLE);

	return (
		<Box flexDirection="row" marginBottom={0}>
			<Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1}>
				<Text dimColor bold> {title}{hasMore ? ` (${selectedIndex + 1}/${hints.length})` : ''}</Text>
				{windowStart > 0 ? <Text dimColor>  ↑ {windowStart} more</Text> : null}
				{visibleHints.map((hint, i) => {
					const realIndex = windowStart + i;
					const isSelected = realIndex === selectedIndex;
					const hasSubHints = (subHintsByHint[hint] ?? []).length > 0;
					return (
						<Box key={hint}>
							<Text color={isSelected ? 'cyan' : undefined} bold={isSelected}>
								{isSelected ? '\u276F ' : '  '}
								{hint}
								{hasSubHints ? ` ${SUBMENU_MARKER}` : ''}
							</Text>
							{isSelected ? <Text dimColor> [enter]</Text> : null}
						</Box>
					);
				})}
				{windowEnd < hints.length ? <Text dimColor>  ↓ {hints.length - windowEnd} more</Text> : null}
				<Text dimColor> {'\u2191\u2193'} navigate{'  '}{'\u23CE'} select{'  '}tab complete{'  '}esc dismiss</Text>
			</Box>
			{subHints.length > 0 ? (
				<Box flexDirection="column" borderStyle="round" borderColor="cyan" paddingX={1} marginLeft={1}>
					<Text dimColor bold> Subcommands</Text>
					{visibleSubHints.map((hint) => (
						<Text key={hint}>  {hint}</Text>
					))}
					{subHints.length > MAX_VISIBLE ? <Text dimColor>  ↓ {subHints.length - MAX_VISIBLE} more</Text> : null}
				</Box>
			) : null}
		</Box>
	);
}

export const CommandPicker = React.memo(CommandPickerInner);
