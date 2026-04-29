import React, {useEffect, useState} from 'react';
import {Box, Text} from 'ink';
import TextInput from 'ink-text-input';

// Hermes-inspired palette — matches WelcomeBanner brand identity
const H_WARM = '#ffe6cb'; // warm almond — prompt cursor, input prefix
const H_GOLD = '#ffbd38'; // gold — active border, busy indicator
const H_TEAL = '#3d8a7c'; // dim teal — idle border, leading cue

const noop = (): void => {};
const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const IDLE_FRAMES = ['◇', '◈', '◆', '◈'];
const IDLE_STATIC_FRAME = '◆';
const SPINNER_STATIC_FRAME = '⠋';
const BACKGROUND_STATIC_FRAME = '●';
const STATIC_ELLIPSIS = '...';
const ELLIPSIS_FRAMES = ['   ', '.  ', '.. ', '...'];
const IDLE_ANIMATION_MS = 600;
const BUSY_ANIMATION_MS = 120;
const BACKGROUND_ANIMATION_MS = 900;

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
	const animateNow = canAnimate && (busy || showBackgroundActivity);
	const animateIdle = canAnimate && !busy && !showBackgroundActivity;
	const spinnerFrame = animateNow
		? SPINNER_FRAMES[frameIndex % SPINNER_FRAMES.length]
		: SPINNER_STATIC_FRAME;
	const backgroundFrame = animateNow
		? SPINNER_FRAMES[frameIndex % SPINNER_FRAMES.length]
		: BACKGROUND_STATIC_FRAME;
	const idleFrame = animateIdle
		? IDLE_FRAMES[frameIndex % IDLE_FRAMES.length]
		: IDLE_STATIC_FRAME;
	const dots = busy && animateNow
		? ELLIPSIS_FRAMES[frameIndex % ELLIPSIS_FRAMES.length]
		: STATIC_ELLIPSIS;
	const title = busy ? `${busyTitle}${dots}` : showBackgroundActivity ? backgroundTitle : idleTitle;
	const leadingCue = busy ? `${spinnerFrame} ` : showBackgroundActivity ? backgroundFrame : `${idleFrame} `;

	useEffect(() => {
		if (!animateNow && !animateIdle) {
			return;
		}
		const interval = busy ? BUSY_ANIMATION_MS : showBackgroundActivity ? BACKGROUND_ANIMATION_MS : IDLE_ANIMATION_MS;
		const timer = setInterval(() => {
			setFrameIndex((index) => (index + 1) % (SPINNER_FRAMES.length * IDLE_FRAMES.length));
		}, interval);
		return () => clearInterval(timer);
	}, [animateNow, animateIdle, busy, showBackgroundActivity]);

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
								<Text dimColor>{line.length > 0 ? line : ' '}</Text>
							</Box>
						</Box>
					))}
				</Box>
			)}
			<Box marginTop={1}>
				<Text color={H_WARM} bold>{busy ? '... ' : '> '}</Text>
				<Box flexGrow={1} flexShrink={1}>
					<TextInput key={inputKey} value={input} onChange={setInput} onSubmit={suppressSubmit || busy ? noop : onSubmit} />
				</Box>
			</Box>
			<Text dimColor>/ commands · ↑↓ history · shift+enter newline · wheel/PgUp scroll · End resume · ctrl+x select-mode · ctrl+c clear · ctrl+c ctrl+c exit</Text>
		</Box>
	);
}
