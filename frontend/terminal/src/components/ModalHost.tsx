import React, {useState} from 'react';
import {Box, Text, useInput} from 'ink';
import stringWidth from 'string-width';
import ScrollableTextInput from './ScrollableTextInput.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';

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
		return (
			<Box flexDirection="column" marginTop={1}>
				<Text>
					<Text color="yellow" bold>{'\u250C '}</Text>
					<Text bold>Allow </Text>
					<Text color="cyan" bold>{String(modal.tool_name ?? 'tool')}</Text>
					<Text bold>?</Text>
				</Text>
				{modal.reason ? (
					<Text>
						<Text color="yellow">{'\u2502 '}</Text>
						<Text dimColor>{String(modal.reason)}</Text>
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
