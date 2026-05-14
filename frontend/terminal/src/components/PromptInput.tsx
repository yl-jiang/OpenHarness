import React, {useEffect, useRef, useState} from 'react';
import {Box, Text} from 'ink';
import stringWidth from 'string-width';
import ScrollableTextInput from './ScrollableTextInput.js';
import {EXPAND_TRIGGER_SYMBOL} from './ExpandedComposer.js';
import {HalfLinePaddedBox} from './HalfLinePaddedBox.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {useTheme} from '../theme/ThemeContext.js';

const noop = (): void => {};
export const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const SPINNER_STATIC_FRAME = '⠋';
const IDLE_SHORTCUTS = '/ commands · @ files · ↑↓ history · shift+enter newline';
const BUSY_SHORTCUTS = 'esc×2 cancel · ctrl+c stop';

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
	if (platform === 'darwin') {
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
	inputMode?: 'chat' | 'shell';
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
	inputMode = 'chat',
}: PromptInputProps): React.JSX.Element {
	const [frameIndex, setFrameIndex] = useState(0);
	const intervalRef = useRef<NodeJS.Timeout | null>(null);
	const {theme} = useTheme();

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

	const {cols} = useTerminalSize();
	const showBackgroundActivity = !busy && hasBackgroundTasks;

	// Prompt prefix: '! ' when in shell mode (idle), '> ' when chat idle, spinner when busy/bg
	const spinnerFrame = animateSpinner ? SPINNER_FRAMES[frameIndex] : SPINNER_STATIC_FRAME;
	const isShellIdle = inputMode === 'shell' && !busy && !showBackgroundActivity;
	const prefix = busy || showBackgroundActivity ? `${spinnerFrame} ` : isShellIdle ? '! ' : '> ';
	const prefixWidth = stringWidth(prefix);

	// Status text shown inline when busy
	const statusText = busy
		? (statusLabel ?? (toolName ? toolName : 'running'))
		: (showBackgroundActivity ? 'bg tasks running' : '');

	// App's outer Box uses paddingX={1}, so available content width = cols - 2
	const containerWidth = cols - 2;
	const inputAvailableWidth = containerWidth - prefixWidth;
	const previewAvailableWidth = inputAvailableWidth;

	// Colors from theme
	const accentColor = theme.colors.accent;
	const prefixColor = busy || showBackgroundActivity ? theme.colors.warning : isShellIdle ? theme.colors.warning : accentColor;
	const lineColor = theme.colors.muted;

	const placeholder = isShellIdle ? '  Type a shell command (exit to leave)' : '  Type your message or @path/to/file';
	const renderFooterShortcuts = (): React.JSX.Element => {
		if (isShellIdle) {
			return (
				<Text>
					<Text color={theme.colors.warning} bold>shell mode</Text>
					<Text color={theme.colors.muted}> · </Text>
					<Text color={theme.colors.info}>tab complete</Text>
					<Text color={theme.colors.muted}> · type </Text>
					<Text color={theme.colors.accent}>"exit"</Text>
					<Text color={theme.colors.muted}> or </Text>
					<Text color={theme.colors.accent}>esc</Text>
					<Text color={theme.colors.muted}> to leave</Text>
				</Text>
			);
		}
		return <Text dimColor>{busy ? BUSY_SHORTCUTS : IDLE_SHORTCUTS}</Text>;
	};

	return (
		<Box flexDirection="column" marginTop={1} flexShrink={0}>
			{/* Main input area framed by horizontal separators */}
			<HalfLinePaddedBox lineColor={lineColor}>
				<Box flexDirection="column" paddingX={1}>
					{/* Extra input lines (multiline preview) */}
					{extraInputLines && extraInputLines.length > 0 && (
						<Box flexDirection="column">
							{extraInputLines.map((line, i) => (
								<Box key={i}>
									<Text dimColor>{'  '}</Text>
									<Box flexGrow={1} flexShrink={1}>
										<Text dimColor>{clipPromptPreviewLine(line, previewAvailableWidth) || ' '}</Text>
									</Box>
								</Box>
							))}
						</Box>
					)}

					{/* Input row: prefix + text input or status */}
					<Box>
						<Text color={prefixColor} bold>{prefix}</Text>
						{busy ? (
							<Text color={theme.colors.warning} dimColor>{statusText}</Text>
						) : (
							<ScrollableTextInput
								key={inputKey}
								value={input}
								onChange={setInput}
								onSubmit={suppressSubmit ? noop : onSubmit}
								availableWidth={inputAvailableWidth}
								placeholder={placeholder}
								accentColor={accentColor}
							/>
						)}
					</Box>
				</Box>
			</HalfLinePaddedBox>

			{/* Footer: shortcuts + expand trigger */}
			<Box justifyContent="space-between" paddingX={1}>
				{renderFooterShortcuts()}
				<Text color={prefixColor} bold>{EXPAND_TRIGGER_SYMBOL}</Text>
			</Box>
		</Box>
	);
}

export const PromptInput = React.memo(PromptInputInner);
