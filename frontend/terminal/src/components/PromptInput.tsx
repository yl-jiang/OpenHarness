import React, {useEffect, useState} from 'react';
import {Box, Text} from 'ink';
import TextInput from 'ink-text-input';

import {useTheme} from '../theme/ThemeContext.js';

const noop = (): void => {};

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

function useSpinnerFrame(active: boolean, intervalMs = 100): string {
	const [frame, setFrame] = useState(0);
	useEffect(() => {
		if (!active) {
			setFrame(0);
			return;
		}
		const id = setInterval(() => {
			setFrame((f) => (f + 1) % SPINNER_FRAMES.length);
		}, intervalMs);
		return () => clearInterval(id);
	}, [active, intervalMs]);
	return SPINNER_FRAMES[frame] ?? SPINNER_FRAMES[0];
}

function useEllipsis(active: boolean, intervalMs = 400): string {
	const [step, setStep] = useState(0);
	useEffect(() => {
		if (!active) {
			setStep(0);
			return;
		}
		const id = setInterval(() => {
			setStep((s) => (s + 1) % 4);
		}, intervalMs);
		return () => clearInterval(id);
	}, [active, intervalMs]);
	return '.'.repeat(step);
}

export function PromptInput({
	busy,
	input,
	setInput,
	onSubmit,
	extraInputLines,
	toolName,
	suppressSubmit,
	statusLabel,
	inputKey,
}: {
	busy: boolean;
	input: string;
	setInput: (value: string) => void;
	onSubmit: (value: string) => void;
	extraInputLines?: string[];
	toolName?: string;
	suppressSubmit?: boolean;
	statusLabel?: string;
	inputKey?: number;
}): React.JSX.Element {
	const {theme} = useTheme();
	const idleTitle = '[idle]';
	const busyTitle = statusLabel ?? (toolName ? `[run] ${toolName}` : '[run]');
	const spinnerFrame = useSpinnerFrame(busy);
	const dots = useEllipsis(busy);

	return (
		<Box
			flexDirection="column"
			marginTop={1}
			borderStyle="round"
			borderColor={busy ? theme.colors.primary : theme.colors.muted}
			paddingX={1}
		>
			<Box>
				<Text color={theme.colors.primary} bold>
					{busy ? `${spinnerFrame} ` : '>>'}
				</Text>
				<Text dimColor>{' | '}</Text>
				<Text color={busy ? theme.colors.primary : undefined} dimColor={!busy}>
					{busy ? `${busyTitle}${dots}` : idleTitle}
				</Text>
			</Box>
			{extraInputLines && extraInputLines.length > 0 && (
				<Box flexDirection="column" marginTop={1}>
					{extraInputLines.map((line, i) => (
						<Box key={i}>
							<Text color={theme.colors.primary} bold>{'  '}</Text>
							<Text dimColor>{line.length > 0 ? line : ' '}</Text>
						</Box>
					))}
				</Box>
			)}
			<Box marginTop={1}>
				<Text color={theme.colors.primary} bold>{busy ? '... ' : '> '}</Text>
				<TextInput key={inputKey} value={input} onChange={setInput} onSubmit={suppressSubmit || busy ? noop : onSubmit} />
			</Box>
			<Text dimColor>/ commands · ↑↓ history · shift+enter newline · wheel/PgUp scroll · End resume · ctrl+x select-mode · ctrl+c clear · ctrl+c ctrl+c exit</Text>
		</Box>
	);
}
