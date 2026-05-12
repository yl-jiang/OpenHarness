import React, {useState} from 'react';
import {Box, Text, useInput} from 'ink';
import stringWidth from 'string-width';
import ScrollableTextInput from './ScrollableTextInput.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';
import {truncateWithEllipsis} from '../textLayout.js';

const MIN_REASON_WIDTH = 20;
const MAX_PERMISSION_REASON_LINES = 4;
const MAX_DIFF_LINES = 40;

type WrappedPreview = {
	lines: string[];
	hiddenLineCount: number;
};

function wrapLine(line: string, maxWidth: number): string[] {
	if (maxWidth <= 0) {
		return [];
	}
	if (!line) {
		return [''];
	}

	const wrapped: string[] = [];
	let remaining = line;
	while (remaining) {
		if (stringWidth(remaining) <= maxWidth) {
			wrapped.push(remaining);
			break;
		}

		let sliceEnd = 0;
		let usedWidth = 0;
		let lastWhitespace = -1;
		let offset = 0;
		for (const char of remaining) {
			const charWidth = stringWidth(char);
			if (usedWidth + charWidth > maxWidth) {
				break;
			}
			usedWidth += charWidth;
			offset += char.length;
			sliceEnd = offset;
			if (/\s/.test(char)) {
				lastWhitespace = offset;
			}
		}

		if (sliceEnd === 0) {
			break;
		}

		const breakAt = lastWhitespace > 0 ? lastWhitespace : sliceEnd;
		const nextLine = remaining.slice(0, breakAt).trimEnd();
		wrapped.push(nextLine);
		remaining = remaining.slice(breakAt).trimStart();
	}

	return wrapped.length > 0 ? wrapped : [truncateWithEllipsis(line, maxWidth)];
}

function buildWrappedPreview(text: string, maxWidth: number, maxLines: number): WrappedPreview {
	const wrapped = text
		.replace(/\r/g, '')
		.split('\n')
		.flatMap((line) => wrapLine(line, maxWidth));

	if (wrapped.length <= maxLines) {
		return {lines: wrapped, hiddenLineCount: 0};
	}

	const visibleLines = wrapped.slice(0, maxLines);
	visibleLines[maxLines - 1] = truncateWithEllipsis(visibleLines[maxLines - 1] ?? '', maxWidth);
	return {
		lines: visibleLines,
		hiddenLineCount: wrapped.length - maxLines,
	};
}

function WaitingAnimation(): React.JSX.Element {
	// Kept as a static label to avoid forcing Ink redraws while waiting for
	// user input — repeated stdout writes make most terminals snap the
	// viewport back to the bottom, breaking scrollback.
	return (
		<Text color="magenta" dimColor>
			Agent is waiting for your input...
		</Text>
	);
}

function QuestionModal({
	modal,
	modalInput,
	setModalInput,
	onSubmit,
}: {
	modal: Record<string, unknown>;
	modalInput: string;
	setModalInput: (value: string) => void;
	onSubmit: (value: string) => void;
}): React.JSX.Element {
	const [extraLines, setExtraLines] = useState<string[]>([]);

	useInput((_chunk, key) => {
		if (key.shift && key.return) {
			setExtraLines((lines) => [...lines, modalInput]);
			setModalInput('');
		}
	});

	const handleSubmit = (value: string): void => {
		const allLines = [...extraLines, value];
		setExtraLines([]);
		onSubmit(allLines.join('\n'));
	};

	const toolName = modal.tool_name ? String(modal.tool_name) : null;
	const reason = modal.reason ? String(modal.reason) : null;
	const question = String(modal.question ?? 'Question');

	const {cols} = useTerminalSize();
	const modalPrefix = '> ';
	// cols - App paddingX(1)*2 - doubleBorder*2 - boxPaddingX(1)*2 - prefixWidth
	const modalInputWidth = cols - 6 - stringWidth(modalPrefix);

	return (
		<Box flexDirection="column" marginTop={1} borderStyle="double" borderColor="magenta" paddingX={1} overflow="hidden">
			<WaitingAnimation />
			<Box marginTop={1}>
				<Text color="magenta" bold>{'\u2753 '}</Text>
				<Box flexGrow={1} flexShrink={1}>
					<Text bold>{question}</Text>
				</Box>
			</Box>
			{toolName ? (
				<Text dimColor>
					{'  '}Tool: <Text color="cyan">{toolName}</Text>
				</Text>
			) : null}
			{reason ? (
				<Text dimColor>{'  '}Reason: {reason}</Text>
			) : null}
			{extraLines.length > 0 && (
				<Box flexDirection="column" marginTop={1} marginLeft={2}>
					{extraLines.map((line, i) => (
						<Text key={i} dimColor>
							{line}
						</Text>
					))}
				</Box>
			)}
			<Box marginTop={1}>
				<Text color="cyan">{modalPrefix}</Text>
				<ScrollableTextInput
					value={modalInput}
					onChange={setModalInput}
					onSubmit={handleSubmit}
					availableWidth={modalInputWidth}
				/>
			</Box>
			<Text dimColor>{'  '}shift+enter: newline | enter: submit</Text>
		</Box>
	);
}

type DiffLineType = 'add' | 'del' | 'hunk' | 'context';

type ParsedDiffLine = {
	type: DiffLineType;
	content: string;
};

function parseDiffLines(diffText: string): ParsedDiffLine[] {
	const result: ParsedDiffLine[] = [];
	for (const raw of diffText.split('\n')) {
		if (raw.startsWith('+++') || raw.startsWith('---')) continue;
		if (raw.startsWith('@@')) {
			result.push({type: 'hunk', content: raw});
		} else if (raw.startsWith('+')) {
			result.push({type: 'add', content: raw.slice(1)});
		} else if (raw.startsWith('-')) {
			result.push({type: 'del', content: raw.slice(1)});
		} else if (raw) {
			result.push({type: 'context', content: raw.startsWith(' ') ? raw.slice(1) : raw});
		}
	}
	return result;
}

function EditDiffModal({modal}: {modal: Record<string, unknown>}): React.JSX.Element {
	const {cols} = useTerminalSize();
	const path = String(modal.path ?? '');
	const diffText = String(modal.diff ?? '');
	const added = Number(modal.added ?? 0);
	const removed = Number(modal.removed ?? 0);

	const allLines = parseDiffLines(diffText);
	const visibleLines = allLines.slice(0, MAX_DIFF_LINES);
	const hiddenCount = allLines.length - visibleLines.length;
	const maxContentWidth = Math.max(20, cols - 6);

	return (
		<Box flexDirection="column" marginTop={1}>
			<Text>
				<Text color="yellow" bold>{'\u250C '}</Text>
				<Text bold>Edit </Text>
				<Text color="cyan" bold>{path}</Text>
				<Text bold>{'  '}</Text>
				<Text color="green">{`+${added}`}</Text>
				<Text>{' '}</Text>
				<Text color="red">{`-${removed}`}</Text>
			</Text>
			{visibleLines.map((line, i) => {
				if (line.type === 'hunk') {
					return (
						<Text key={i}>
							<Text color="yellow">{'\u2502 '}</Text>
							<Text color="cyan" dimColor>{truncateWithEllipsis(line.content, maxContentWidth)}</Text>
						</Text>
					);
				}
				if (line.type === 'add') {
					return (
						<Text key={i}>
							<Text color="yellow">{'\u2502 '}</Text>
							<Text color="green">{'+'}</Text>
							<Text color="green">{truncateWithEllipsis(line.content, maxContentWidth - 1)}</Text>
						</Text>
					);
				}
				if (line.type === 'del') {
					return (
						<Text key={i}>
							<Text color="yellow">{'\u2502 '}</Text>
							<Text color="red">{'-'}</Text>
							<Text color="red">{truncateWithEllipsis(line.content, maxContentWidth - 1)}</Text>
						</Text>
					);
				}
				return (
					<Text key={i}>
						<Text color="yellow">{'\u2502 '}</Text>
						<Text dimColor>{' '}{truncateWithEllipsis(line.content, maxContentWidth - 1)}</Text>
					</Text>
				);
			})}
			{hiddenCount > 0 && (
				<Text>
					<Text color="yellow">{'\u2502 '}</Text>
					<Text dimColor>... {hiddenCount} more lines hidden</Text>
				</Text>
			)}
			<Text>
				<Text color="yellow">{'\u2514 '}</Text>
				<Text color="green">[y] Once</Text>
				<Text>{'  '}</Text>
				<Text color="green">[a] Always</Text>
				<Text>{'  '}</Text>
				<Text color="red">[n] Deny</Text>
			</Text>
		</Box>
	);
}

function ModalHostInner({
	modal,
	modalInput,
	setModalInput,
	onSubmit,
}: {
	modal: Record<string, unknown> | null;
	modalInput: string;
	setModalInput: (value: string) => void;
	onSubmit: (value: string) => void;
}): React.JSX.Element | null {
	const {cols} = useTerminalSize();

	if (modal?.kind === 'permission') {
		const reason = modal.reason ? String(modal.reason) : '';
		const reasonWidth = Math.max(MIN_REASON_WIDTH, cols - 6);
		const reasonPreview = reason
			? buildWrappedPreview(reason, reasonWidth, MAX_PERMISSION_REASON_LINES)
			: null;
		return (
			<Box flexDirection="column" marginTop={1}>
				<Text>
					<Text color="yellow" bold>{'\u250C '}</Text>
					<Text bold>Allow </Text>
					<Text color="cyan" bold>{String(modal.tool_name ?? 'tool')}</Text>
					<Text bold>?</Text>
				</Text>
				{reasonPreview?.lines.map((line, index) => (
					<Text key={index}>
						<Text color="yellow">{'\u2502 '}</Text>
						<Text dimColor>{line}</Text>
					</Text>
				))}
				{reasonPreview && reasonPreview.hiddenLineCount > 0 ? (
					<Text>
						<Text color="yellow">{'\u2502 '}</Text>
						<Text dimColor>... {reasonPreview.hiddenLineCount} more lines hidden</Text>
					</Text>
				) : null}
				<Text>
					<Text color="yellow">{'\u2514 '}</Text>
					<Text color="green">[y] Once</Text>
					<Text>{'  '}</Text>
					<Text color="green">[a] Always</Text>
					<Text>{'  '}</Text>
					<Text color="red">[n] Deny</Text>
				</Text>
			</Box>
		);
	}
	if (modal?.kind === 'edit_diff') {
		return <EditDiffModal modal={modal} />;
	}
	if (modal?.kind === 'question') {
		return (
			<QuestionModal
				modal={modal}
				modalInput={modalInput}
				setModalInput={setModalInput}
				onSubmit={onSubmit}
			/>
		);
	}
	if (modal?.kind === 'mcp_auth') {
		const authPrefix = '> ';
		// No border box, just App paddingX(1)*2 + prefixWidth
		const authInputWidth = cols - 2 - stringWidth(authPrefix);
		return (
			<Box flexDirection="column" marginTop={1}>
				<Text>
					<Text color="yellow" bold>{'\u{1F511} '}</Text>
					<Text bold>MCP Authentication</Text>
				</Text>
				<Text dimColor>{String(modal.prompt ?? 'Provide auth details')}</Text>
				<Box>
					<Text color="cyan">{authPrefix}</Text>
					<ScrollableTextInput value={modalInput} onChange={setModalInput} onSubmit={onSubmit} availableWidth={authInputWidth} />
				</Box>
			</Box>
		);
	}
	return null;
}

export const ModalHost = React.memo(ModalHostInner);
