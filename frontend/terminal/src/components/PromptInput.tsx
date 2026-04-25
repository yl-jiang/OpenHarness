import React from 'react';
import {Box, Text} from 'ink';
import TextInput from 'ink-text-input';

import {useTheme} from '../theme/ThemeContext.js';
import {Spinner} from './Spinner.js';

const noop = (): void => {};

export function PromptInput({
	busy,
	input,
	setInput,
	onSubmit,
	toolName,
	suppressSubmit,
	statusLabel,
	inputKey,
}: {
	busy: boolean;
	input: string;
	setInput: (value: string) => void;
	onSubmit: (value: string) => void;
	toolName?: string;
	suppressSubmit?: boolean;
	statusLabel?: string;
	inputKey?: number;
}): React.JSX.Element {
	const {theme} = useTheme();

	return (
		<Box
			flexDirection="column"
			marginTop={1}
			borderStyle="round"
			borderColor={busy ? theme.colors.primary : theme.colors.muted}
			paddingX={1}
		>
			<Box>
				<Text color={theme.colors.primary} bold>Prompt</Text>
				<Text dimColor> · </Text>
				{busy ? (
					<Spinner label={statusLabel ?? (toolName ? `Running ${toolName}...` : 'Running...')} />
				) : (
					<Text dimColor>Ready · enter sends immediately</Text>
				)}
			</Box>
			<Box marginTop={1}>
				<Text color={theme.colors.primary} bold>{busy ? '… ' : '› '}</Text>
				<TextInput key={inputKey} value={input} onChange={setInput} onSubmit={suppressSubmit || busy ? noop : onSubmit} />
			</Box>
			<Text dimColor>/ commands · ↑↓ history · scroll terminal for history · ctrl+c exit</Text>
		</Box>
	);
}
