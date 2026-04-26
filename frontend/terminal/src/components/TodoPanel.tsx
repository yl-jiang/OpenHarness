import React, {useEffect, useRef, useState} from 'react';
import {Box, Text, useInput} from 'ink';

export type TodoItem = {
	text: string;
	checked: boolean;
};

function parseTodoItems(markdown: string): TodoItem[] {
	const lines = markdown.split('\n');
	const items: TodoItem[] = [];
	for (const line of lines) {
		const m = line.match(/^\s*-\s+\[([ xX])\]\s+(.+)/);
		if (m) {
			items.push({checked: m[1].toLowerCase() === 'x', text: m[2].trim()});
		}
	}
	return items;
}

function TodoPanelInner({
	markdown,
	compact: initialCompact = false,
}: {
	markdown: string;
	compact?: boolean;
}): React.JSX.Element | null {
	const items = parseTodoItems(markdown);
	const total = items.length;
	const done = items.filter((i) => i.checked).length;
	const allDone = total > 0 && done === total;

	// User-controlled override: 'auto' follows the all-done heuristic, while
	// explicit toggles (ctrl+t) lock the panel in expanded/collapsed state
	// until the next time the todo list changes.
	type Mode = 'auto' | 'expanded' | 'compact';
	const [mode, setMode] = useState<Mode>(initialCompact ? 'compact' : 'auto');

	// When the underlying todo list changes (item count shifts or progress
	// flips), reset back to auto so the panel can react to the new state.
	const lastSignatureRef = useRef('');
	useEffect(() => {
		const signature = `${total}:${done}`;
		if (lastSignatureRef.current && lastSignatureRef.current !== signature) {
			setMode('auto');
		}
		lastSignatureRef.current = signature;
	}, [total, done]);

	useInput((chunk, key) => {
		if (key.ctrl && chunk === 't') {
			setMode((m) => {
				const isCollapsed = m === 'compact' || (m === 'auto' && allDone);
				return isCollapsed ? 'expanded' : 'compact';
			});
		}
	});

	if (total === 0) {
		return null;
	}

	const compact = mode === 'compact' || (mode === 'auto' && allDone);

	if (compact) {
		return (
			<Box>
				<Text color={allDone ? 'green' : 'yellow'} bold>
					{allDone ? '✓ ' : '☑ '}
				</Text>
				<Text dimColor>
					{allDone ? `Todos: all done (${total})` : `Todos: ${done}/${total} done`}
				</Text>
				<Text dimColor> [ctrl+t expand]</Text>
			</Box>
		);
	}

	return (
		<Box flexDirection="column" borderStyle="round" borderColor={allDone ? 'green' : 'yellow'} paddingX={1} marginTop={1}>
			<Box>
				<Text color={allDone ? 'green' : 'yellow'} bold>
					{allDone ? '✓ ' : '☑ '}
				</Text>
				<Text bold>
					Todo List{' '}
				</Text>
				<Text dimColor>
					({done}/{total}{allDone ? ' · done' : ''})
				</Text>
				<Text dimColor> [ctrl+t collapse]</Text>
			</Box>
			{items.map((item, i) => (
				<Box key={i}>
					<Text color={item.checked ? 'green' : 'white'}>
						{item.checked ? '  ☑ ' : '  ☐ '}
					</Text>
					<Text
						color={item.checked ? 'green' : undefined}
						dimColor={item.checked}
					>
						{item.text}
					</Text>
				</Box>
			))}
		</Box>
	);
}

export const TodoPanel = React.memo(TodoPanelInner);

export {parseTodoItems};
