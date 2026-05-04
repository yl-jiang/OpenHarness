import React, {useEffect, useState} from 'react';
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
const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const IDLE_STATIC_FRAME = '◆';
const SPINNER_STATIC_FRAME = '⠋';
const BACKGROUND_STATIC_FRAME = '●';
const STATIC_ELLIPSIS = '...';
// Busy spinner ticks slowly enough that Ink's full-frame redraw doesn't
// produce visible flicker on the bottom panels (PromptInput / TodoPanel /
// StatusBar all sit adjacent and get repainted on every tick).  ~5fps is
// still smooth-feeling while halving the redraw rate of the previous 120ms.
const BUSY_ANIMATION_MS = 220;
const BACKGROUND_ANIMATION_MS = 900;
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
 * Decide whether timer-driven spinner redraws are safe in the current terminal.
 *
 * Ink rewrites the dynamic frame on every state update, which on legacy Windows
 * conhost (cmd.exe / pre-Windows-Terminal PowerShell) and high-latency SSH
 * sessions manifests as visible flicker.  Modern terminals — Windows Terminal,
 * VS Code's integrated terminal, WezTerm, ConEmu, Alacritty, mintty — handle
 * frequent ANSI repaints cleanly, so we opt them in explicitly on Windows
 * rather than blanket-disabling the platform.
 */
export function shouldAnimateSpinner(
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

export const shouldAnimateBackgroundCue = shouldAnimateSpinner;

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
	backgroundTaskCount = 0,
	animateSpinner,
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
	backgroundTaskCount?: number;
	animateSpinner?: boolean;
}): React.JSX.Element {
	const [frameIndex, setFrameIndex] = useState(0);
	const idleTitle = 'ready';
	const busyTitle = statusLabel ?? (toolName ? `[run] ${toolName}` : '[run]');
	const showBackgroundActivity = !busy && backgroundTaskCount > 0;
	const backgroundTitle = `[bg] ${backgroundTaskCount} running`;
	const canAnimate = animateSpinner ?? shouldAnimateSpinner();
	// Only animate while busy or while background tasks are running. Idle
	// animation forces Ink to repaint the whole TUI on every tick which
	// causes visible flicker on most terminals (see commit 5276771 and the
	// note in components/Spinner.tsx).
	const animateNow = canAnimate && (busy || showBackgroundActivity);
	const spinnerFrame = animateNow
		? SPINNER_FRAMES[frameIndex % SPINNER_FRAMES.length]
		: SPINNER_STATIC_FRAME;
	const backgroundFrame = animateNow
		? SPINNER_FRAMES[frameIndex % SPINNER_FRAMES.length]
		: BACKGROUND_STATIC_FRAME;
	const idleFrame = IDLE_STATIC_FRAME;
	// Keep the trailing ellipsis static even while busy: the braille spinner
	// already conveys liveness, and animating both makes the title line churn
	// 4 characters per tick which is the most visually flickery part of the
	// prompt header.
	const dots = STATIC_ELLIPSIS;
	const title = busy ? `${busyTitle}${dots}` : showBackgroundActivity ? backgroundTitle : idleTitle;
	const leadingCue = busy ? `${spinnerFrame} ` : showBackgroundActivity ? backgroundFrame : `${idleFrame} `;

	useEffect(() => {
		if (!animateNow) {
			return;
		}
		const interval = busy ? BUSY_ANIMATION_MS : BACKGROUND_ANIMATION_MS;
		const timer = setInterval(() => {
			setFrameIndex((index) => (index + 1) % SPINNER_FRAMES.length);
		}, interval);
		return () => clearInterval(timer);
	}, [animateNow, busy]);

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
