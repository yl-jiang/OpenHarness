import {useEffect} from 'react';
import {useStdout} from 'ink';
import stringWidth from 'string-width';

import {SPINNER_FRAMES, shouldAnimateBackgroundCue} from './PromptInput.js';

const ACTIVITY_ANIMATION_MS = 220;
const PROMPT_HEADER_ROW_FROM_BOTTOM = 5;
const PROMPT_HEADER_COLUMN = 4;
const PROMPT_ACTIVITY_RIGHT_MARGIN = 4;
const PROMPT_ACTIVITY_SUMMARY_GAP = 2;
const PROMPT_ACTIVITY_SUMMARY_WIDTH = 13;
const ANSI_RESET = '\x1b[0m';
const ANSI_BOLD = '\x1b[1m';
const ANSI_DIM = '\x1b[2m';
const ANSI_GOLD = '\x1b[38;2;255;189;56m';

type InlineActivityIndicatorProps = {
	active: boolean;
	busy: boolean;
	hasBackgroundTasks: boolean;
	activeBackgroundTaskCount: number;
	statusLabel?: string;
	toolName?: string;
	startedAtSeconds?: number | null;
};

export function InlineActivityIndicator({
	active,
	busy,
	hasBackgroundTasks,
	activeBackgroundTaskCount,
	statusLabel,
	toolName,
	startedAtSeconds,
}: InlineActivityIndicatorProps): null {
	const {stdout} = useStdout();

	useEffect(() => {
		if (!active || busy || !hasBackgroundTasks || !stdout?.isTTY || !shouldAnimateBackgroundCue()) {
			return;
		}
		const startedAtMs = startedAtSeconds != null ? startedAtSeconds * 1000 : Date.now();
		let frameIndex = 0;
		let lastText = '';

		const write = (): void => {
			const row = Math.max(1, (stdout.rows ?? 24) - PROMPT_HEADER_ROW_FROM_BOTTOM);
			const elapsed = Math.max(0, Math.floor((Date.now() - startedAtMs) / 1000));
			const summary = activeBackgroundTaskCount > 0 ? formatBackgroundActivitySummary(activeBackgroundTaskCount, elapsed) : null;
			const width = Math.max(1, (stdout.columns ?? 80) - PROMPT_HEADER_COLUMN - PROMPT_ACTIVITY_RIGHT_MARGIN);
			const titleWidth = summary
				? Math.max(1, width - PROMPT_ACTIVITY_SUMMARY_WIDTH - PROMPT_ACTIVITY_SUMMARY_GAP)
				: width;
			const title = busy
				? `${statusLabel ?? (toolName ? `[run] ${toolName}` : '[run]')}...`
				: hasBackgroundTasks
					? '[bg] running'
					: 'ready';
			const text = formatPromptActivityText(
				SPINNER_FRAMES[frameIndex % SPINNER_FRAMES.length],
				title,
				titleWidth,
			);
			frameIndex = (frameIndex + 1) % SPINNER_FRAMES.length;
			const nextText = `${text.plain}\n${summary?.plain ?? ''}`;
			if (nextText === lastText) {
				return;
			}
			lastText = nextText;
			const summaryColumn = summary == null
				? null
				: PROMPT_HEADER_COLUMN + titleWidth + PROMPT_ACTIVITY_SUMMARY_GAP;
			stdout.write(
				`\x1b[s\x1b[${row};${PROMPT_HEADER_COLUMN}H${text.styled}${summary == null ? '' : `\x1b[${row};${summaryColumn}H${summary.styled}`}\x1b[u`,
			);
		};

		write();
		const timer = setInterval(write, ACTIVITY_ANIMATION_MS);
		return () => clearInterval(timer);
	}, [active, activeBackgroundTaskCount, busy, hasBackgroundTasks, statusLabel, stdout, startedAtSeconds, toolName]);

	return null;
}

export function formatPromptActivityText(frame: string, title: string, width: number): {plain: string; styled: string} {
	const prefix = `${frame} | `;
	const titleWidth = Math.max(1, width - stringWidth(prefix));
	const clippedTitle = clipToWidth(title, titleWidth);
	const plain = fitInlineActivityText(`${prefix}${clippedTitle}`, width);
	const padding = ' '.repeat(Math.max(0, width - stringWidth(prefix) - stringWidth(clippedTitle)));
	return {
		plain,
		styled: `${ANSI_GOLD}${ANSI_BOLD}${frame}${ANSI_RESET}${ANSI_DIM} | ${ANSI_RESET}${ANSI_GOLD}${ANSI_BOLD}${clippedTitle}${ANSI_RESET}${padding}`,
	};
}

function formatBackgroundActivitySummary(taskCount: number, elapsedSeconds: number): {plain: string; styled: string} {
	const count = taskCount > 99 ? '99+' : String(taskCount);
	const duration = formatCompactActivityDuration(elapsedSeconds);
	const content = `⚙ ${count} · ${duration}`;
	const plain = fitInlineActivityText(content, PROMPT_ACTIVITY_SUMMARY_WIDTH);
	const padding = ' '.repeat(Math.max(0, PROMPT_ACTIVITY_SUMMARY_WIDTH - stringWidth(content)));

	return {
		plain,
		styled: `${ANSI_DIM}⚙ ${ANSI_RESET}${ANSI_GOLD}${ANSI_BOLD}${count}${ANSI_RESET}${ANSI_DIM} · ${ANSI_RESET}${ANSI_GOLD}${ANSI_BOLD}${duration}${ANSI_RESET}${padding}`,
	};
}

function formatCompactActivityDuration(seconds: number): string {
	const safeSeconds = Math.max(0, seconds);
	if (safeSeconds < 3600) {
		const minutes = Math.floor(safeSeconds / 60);
		const remainingSeconds = safeSeconds % 60;
		return `${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`;
	}

	const hours = Math.floor(safeSeconds / 3600);
	if (hours >= 100) {
		return '99+h';
	}

	const remainingMinutes = Math.floor((safeSeconds % 3600) / 60);
	return `${String(hours).padStart(2, '0')}:${String(remainingMinutes).padStart(2, '0')}`;
}

function fitInlineActivityText(value: string, width: number): string {
	if (stringWidth(value) >= width) {
		const chars = [...value];
		let used = 0;
		let out = '';
		for (const char of chars) {
			const charWidth = stringWidth(char);
			if (used + charWidth > width) {
				break;
			}
			out += char;
			used += charWidth;
		}
		return out;
	}
	return value + ' '.repeat(width - stringWidth(value));
}

function clipToWidth(value: string, width: number): string {
	if (stringWidth(value) <= width) {
		return value;
	}
	let used = 0;
	let out = '';
	for (const char of [...value]) {
		const charWidth = stringWidth(char);
		if (used + charWidth > width) {
			break;
		}
		out += char;
		used += charWidth;
	}
	return out;
}
