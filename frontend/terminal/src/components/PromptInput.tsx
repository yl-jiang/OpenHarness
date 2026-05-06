import React, {useEffect, useRef, useState} from 'react';
import {Box, Text} from 'ink';
import stringWidth from 'string-width';
import ScrollableTextInput from './ScrollableTextInput.js';
import {EXPAND_TRIGGER_SYMBOL} from './ExpandedComposer.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';

// Hermes-inspired palette — matches WelcomeBanner brand identity
const H_WARM = '#ffe6cb'; // warm almond — prompt cursor, input prefix
const H_GOLD = '#ffbd38'; // gold — active border, busy indicator
const H_TEAL = '#3d8a7c'; // dim teal — idle border, leading cue

const noop = (): void => {};
export const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const IDLE_STATIC_FRAME = '◆';
const SPINNER_STATIC_FRAME = '⠋';
const STATIC_ELLIPSIS = '...';
const IDLE_SHORTCUTS = '/ commands · @ files · ↑↓ history · shift/alt+enter newline';
const BUSY_SHORTCUTS = 'PgUp/Dn scroll · End resume · /stop or Ctrl+C cancel';

export function clipPromptPreviewLine(line: string, availableWidth: number): string {
	const safeWidth = Math.max(1, availableWidth);
	if (stringWidth(line) <= safeWidth) {
		return line;
	}

	const ellipsis = safeWidth >= 4 ? '...' : '.'.repeat(safeWidth);
	const chars = [...line];
	const clippedChars: string[] = [];
	let usedWidth = stringWidth(ellipsis);

	for (let i = chars.length - 1; i >= 0; i -= 1) {
		const char = chars[i]!;
		const charWidth = stringWidth(char);
		if (usedWidth + charWidth > safeWidth) {
			break;
		}
		clippedChars.unshift(char);
		usedWidth += charWidth;
	}

	return `${ellipsis}${clippedChars.join('')}`;
}

/**
 * Timer-driven spinner redraws are intentionally disabled for all terminals.
 * Ink repaints the whole prompt on each state update, so even low-frequency
 * animation can produce visible flicker while long-running work is otherwise idle.
 */
export function shouldAnimateSpinner(
	_platform: NodeJS.Platform = process.platform,
	_env: NodeJS.ProcessEnv = process.env,
): boolean {
	return false;
}

export function shouldAnimateBackgroundCue(
	platform: NodeJS.Platform = process.platform,
	env: NodeJS.ProcessEnv = process.env,
): boolean {
	if (env.SSH_TTY || env.SSH_CLIENT || env.SSH_CONNECTION) {
		return false;
	}
	if (platform === 'win32') {
		if (env.WT_SESSION) return true;
		if (env.TERM_PROGRAM === 'vscode') return true;
		if (env.WEZTERM_EXECUTABLE || env.ConEmuPID) return true;
		if (env.TERM === 'alacritty' || env.TERM === 'xterm-256color') return true;
		if (env.MSYSTEM || env.TERM === 'cygwin') return true;
		return false;
	}
	return true;
}

type PromptInputProps = {
	busy: boolean;
	input: string;
	setInput: (value: string) => void;
	onSubmit: (value: string) => void;
	extraInputLines?: string[];
	toolName?: string;
	suppressSubmit?: boolean;
	statusLabel?: string;
	inputKey?: number;
	hasBackgroundTasks?: boolean;
	animateSpinner?: boolean;
};

function PromptInputInner({
	busy,
	input,
	setInput,
	onSubmit,
	extraInputLines,
	toolName,
	suppressSubmit,
	statusLabel,
	inputKey,
	hasBackgroundTasks = false,
	animateSpinner = false,
}: PromptInputProps): React.JSX.Element {
	const [frameIndex, setFrameIndex] = useState(0);
	const intervalRef = useRef<NodeJS.Timeout | null>(null);

	useEffect(() => {
		const shouldAnimate = animateSpinner && (busy || hasBackgroundTasks);
		if (shouldAnimate) {
			intervalRef.current = setInterval(() => {
				setFrameIndex((i) => (i + 1) % SPINNER_FRAMES.length);
			}, 220);
		} else {
			if (intervalRef.current) {
				clearInterval(intervalRef.current);
				intervalRef.current = null;
			}
			setFrameIndex(0);
		}
		return () => {
			if (intervalRef.current) {
				clearInterval(intervalRef.current);
				intervalRef.current = null;
			}
		};
	}, [animateSpinner, busy, hasBackgroundTasks]);

	const idleTitle = 'ready';
	const busyTitle = statusLabel ?? (toolName ? `[run] ${toolName}` : '[run]');
	const showBackgroundActivity = !busy && hasBackgroundTasks;
	const backgroundTitle = '[bg] running';
	const animatedFrame = SPINNER_FRAMES[frameIndex];
	const backgroundFrame = animateSpinner ? animatedFrame : SPINNER_STATIC_FRAME;
	const spinnerFrame = animateSpinner ? animatedFrame : (hasBackgroundTasks ? SPINNER_STATIC_FRAME : SPINNER_STATIC_FRAME);
	const idleFrame = IDLE_STATIC_FRAME;
	// Keep the trailing ellipsis static even while busy: the braille spinner
	// already conveys liveness, and animating both makes the title line churn
	// 4 characters per tick which is the most visually flickery part of the
	// prompt header.
	const dots = STATIC_ELLIPSIS;
	const title = busy ? `${busyTitle}${dots}` : showBackgroundActivity ? backgroundTitle : idleTitle;
	const leadingCue = busy ? `${spinnerFrame} ` : showBackgroundActivity ? backgroundFrame : `${idleFrame} `;

	const {cols} = useTerminalSize();
	const prefix = busy ? '... ' : '> ';
	// Available width for the text input viewport:
	// cols - App paddingX(1)*2 - border*2 - boxPaddingX(1)*2 - prefixWidth
	const inputAvailableWidth = cols - 6 - stringWidth(prefix);
	const footerColor = busy || showBackgroundActivity ? H_GOLD : H_TEAL;
	const footerShortcuts = busy ? BUSY_SHORTCUTS : IDLE_SHORTCUTS;

	return (
		<Box
			flexDirection="column"
			marginTop={1}
			borderStyle="round"
			borderColor={busy || showBackgroundActivity ? H_GOLD : H_TEAL}
			paddingX={1}
			overflow="hidden"
		>
			<Box>
				<Text color={busy || showBackgroundActivity ? H_GOLD : H_TEAL} bold>
					{leadingCue}
				</Text>
				<Text dimColor>{' | '}</Text>
				<Text color={busy || showBackgroundActivity ? H_GOLD : undefined} dimColor={!busy && !showBackgroundActivity}>
					{title}
				</Text>
			</Box>
			{extraInputLines && extraInputLines.length > 0 && (
				<Box flexDirection="column" marginTop={1}>
					{extraInputLines.map((line, i) => (
						<Box key={i}>
							<Text color={H_WARM} bold>{'  '}</Text>
							<Box flexGrow={1} flexShrink={1}>
								<Text dimColor>{clipPromptPreviewLine(line, inputAvailableWidth) || ' '}</Text>
							</Box>
						</Box>
					))}
				</Box>
			)}
			<Box marginTop={1}>
				<Text color={H_WARM} bold>{prefix}</Text>
				<ScrollableTextInput
					key={inputKey}
					value={input}
					onChange={setInput}
					onSubmit={suppressSubmit ? noop : onSubmit}
					availableWidth={inputAvailableWidth}
				/>
			</Box>
			<Box marginTop={1} justifyContent="space-between">
				<Box flexDirection="column" flexGrow={1} flexShrink={1}>
					<Text dimColor>{footerShortcuts}</Text>
				</Box>
				<Box marginLeft={1} flexShrink={0} alignSelf="flex-end">
					<Text color={footerColor} bold>{EXPAND_TRIGGER_SYMBOL}</Text>
				</Box>
			</Box>
		</Box>
	);
}

export const PromptInput = React.memo(PromptInputInner);
