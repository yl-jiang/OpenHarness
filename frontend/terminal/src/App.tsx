import React, {useCallback, useDeferredValue, useEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useApp, useInput, useStdin} from 'ink';

import {AlternateScreen} from './components/AlternateScreen.js';
import {CommandPicker} from './components/CommandPicker.js';
import {ConversationView} from './components/ConversationView.js';
import {ModalHost} from './components/ModalHost.js';
import {PromptInput} from './components/PromptInput.js';
import {SelectModal, type SelectOption} from './components/SelectModal.js';
import {StatusBar} from './components/StatusBar.js';
import {SwarmPanel} from './components/SwarmPanel.js';
import {TodoPanel} from './components/TodoPanel.js';
import type {TerminalInputStream} from './input/terminalInput.js';
import {useBackendSession} from './hooks/useBackendSession.js';
import {useMouseWheel} from './hooks/useMouseWheel.js';
import {useTerminalSize} from './hooks/useTerminalSize.js';
import {ThemeProvider, useTheme} from './theme/ThemeContext.js';
import type {FrontendConfig} from './types.js';

const rawReturnSubmit = process.env.OPENHARNESS_FRONTEND_RAW_RETURN === '1';
const scriptedSteps = (() => {
	const raw = process.env.OPENHARNESS_FRONTEND_SCRIPT;
	if (!raw) {
		return [] as string[];
	}
	try {
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : [];
	} catch {
		return [];
	}
})();

const SELECTABLE_COMMANDS = new Set([
	'/provider',
	'/model',
	'/theme',
	'/output-style',
	'/permissions',
	'/resume',
	'/effort',
	'/passes',
	'/turns',
	'/fast',
	'/vim',
	'/voice',
]);

type SelectModalState = {
	title: string;
	options: SelectOption[];
	onSelect: (value: string) => void;
} | null;

export function App({config}: {config: FrontendConfig}): React.JSX.Element {
	const initialTheme = String((config as Record<string, unknown>).theme ?? 'default');
	return (
		<ThemeProvider initialTheme={initialTheme}>
			<AlternateScreen>
				<AppInner config={config} />
			</AlternateScreen>
		</ThemeProvider>
	);
}

function AppInner({config}: {config: FrontendConfig}): React.JSX.Element {
	const {exit} = useApp();
	const {stdin: _stdin} = useStdin();
	const _terminalInput = _stdin as unknown as TerminalInputStream;
	const {theme, setThemeName} = useTheme();
	const {rows} = useTerminalSize();
	const [input, setInput] = useState('');
	const [completionKey, setCompletionKey] = useState(0);
	const [modalInput, setModalInput] = useState('');
	const [history, setHistory] = useState<string[]>([]);
	const [historyIndex, setHistoryIndex] = useState(-1);
	const [lastEscapeAt, setLastEscapeAt] = useState(0);
	const [scriptIndex, setScriptIndex] = useState(0);
	const [pickerIndex, setPickerIndex] = useState(0);
	const [selectModal, setSelectModal] = useState<SelectModalState>(null);
	const [selectIndex, setSelectIndex] = useState(0);

	// Scroll state (item-level).
	// `pausedAt`: when non-null, the user has scrolled away from the live
	// tail; new agent output cannot move the visible viewport.  `null` =
	// "follow the tail" (newest content always visible).
	// `viewBottom`: the index of the transcript item that is currently
	// pinned to the visual bottom of the viewport.  Only meaningful when
	// `pausedAt !== null`.  Decreasing this index reveals earlier history
	// (the slice gets shorter; column-reverse pins its last item at the
	// bottom; older items stack upward into the visible area).
	const [pausedAt, setPausedAt] = useState<number | null>(null);
	const [viewBottom, setViewBottom] = useState(0);

	const session = useBackendSession(config, () => exit());
	const deferredTranscript = useDeferredValue(session.transcript);
	const deferredAssistantBuffer = useDeferredValue(session.assistantBuffer);
	const deferredStatus = useDeferredValue(session.status);
	const deferredTasks = useDeferredValue(session.tasks);
	const deferredTodoMarkdown = useDeferredValue(session.todoMarkdown);
	const deferredSwarmTeammates = useDeferredValue(session.swarmTeammates);
	const deferredSwarmNotifications = useDeferredValue(session.swarmNotifications);

	useEffect(() => {
		const nextTheme = session.status.theme;
		if (typeof nextTheme === 'string' && nextTheme) {
			setThemeName(nextTheme);
		}
	}, [session.status.theme, setThemeName]);

	const transcriptForView =
		pausedAt === null ? deferredTranscript : deferredTranscript.slice(0, viewBottom + 1);
	const assistantBufferForView = pausedAt === null ? deferredAssistantBuffer : '';

	// Scroll helpers.
	const transcriptLengthRef = useRef(deferredTranscript.length);
	transcriptLengthRef.current = deferredTranscript.length;

	const scrollUp = useCallback((step: number) => {
		const len = transcriptLengthRef.current;
		if (len === 0) return;
		setPausedAt((prev) => (prev === null ? len : prev));
		setViewBottom((b) => {
			const base = pausedAt === null ? len - 1 : b;
			return Math.max(0, base - step);
		});
	}, [pausedAt]);

	const scrollDown = useCallback((step: number) => {
		if (pausedAt === null) return;
		setViewBottom((b) => {
			const next = b + step;
			if (next >= pausedAt - 1) {
				setPausedAt(null);
				return next;
			}
			return next;
		});
	}, [pausedAt]);

	const scrollToBottom = useCallback(() => {
		setPausedAt(null);
	}, []);

	const scrollToTop = useCallback(() => {
		const len = transcriptLengthRef.current;
		if (len === 0) return;
		setPausedAt((prev) => (prev === null ? len : prev));
		setViewBottom(0);
	}, []);

	useMouseWheel((delta) => {
		if (delta < 0) scrollUp(3);
		else scrollDown(3);
	});

	// Current tool name for spinner
	const currentToolName = useMemo(() => {
		for (let i = deferredTranscript.length - 1; i >= 0; i--) {
			const item = deferredTranscript[i];
			if (item.role === 'tool') {
				return item.tool_name ?? 'tool';
			}
			if (item.role === 'tool_result' || item.role === 'assistant') {
				break;
			}
		}
		return undefined;
	}, [deferredTranscript]);

	// Command hints
	const commandHints = useMemo(() => {
		const value = input.trim();
		if (!value.startsWith('/')) {
			return [] as string[];
		}
		return session.commands.filter((cmd) => cmd.startsWith(value)).slice(0, 10);
	}, [session.commands, input]);

	const showPicker = commandHints.length > 0 && !session.busy && !session.modal && !selectModal;
	const outputStyle = String(session.status.output_style ?? 'default');

	useEffect(() => {
		setPickerIndex(0);
	}, [commandHints.length, input]);

	// Handle backend-initiated select requests (e.g. /resume session list)
	useEffect(() => {
		if (!session.selectRequest) {
			return;
		}
		const req = session.selectRequest;
		if (req.options.length === 0) {
			session.setSelectRequest(null);
			return;
		}
		const initialIndex = req.options.findIndex((option) => option.active);
		setSelectIndex(initialIndex >= 0 ? initialIndex : 0);
		setSelectModal({
			title: req.title,
			options: req.options.map((o) => ({value: o.value, label: o.label, description: o.description, active: o.active})),
			onSelect: (value) => {
				session.sendRequest({type: 'apply_select_command', command: req.command, value});
				session.setBusy(true);
				setSelectModal(null);
			},
		});
		session.setSelectRequest(null);
	}, [session.selectRequest]);

	// Intercept special commands that need interactive UI
	const handleCommand = (cmd: string): boolean => {
		const trimmed = cmd.trim();

		if (SELECTABLE_COMMANDS.has(trimmed)) {
			session.sendRequest({type: 'select_command', command: trimmed.slice(1)});
			return true;
		}

		if (trimmed === '/permissions' || trimmed === '/permissions show') {
			session.sendRequest({type: 'select_command', command: 'permissions'});
			return true;
		}

		if (trimmed === '/plan') {
			const currentMode = String(session.status.permission_mode ?? 'default');
			if (currentMode === 'plan') {
				session.sendRequest({type: 'submit_line', line: '/plan off'});
			} else {
				session.sendRequest({type: 'submit_line', line: '/plan on'});
			}
			session.setBusy(true);
			return true;
		}

		if (trimmed === '/resume') {
			session.sendRequest({type: 'select_command', command: 'resume'});
			return true;
		}

		return false;
	};

	useInput((chunk, key) => {
		const isPaste = chunk.length > 1 && !key.ctrl && !key.meta;

		if (key.ctrl && chunk === 'c') {
			session.sendRequest({type: 'shutdown'});
			exit();
			return;
		}

		if (isPaste) {
			return;
		}

		// --- Select modal (permissions picker etc.) ---
		if (selectModal) {
			if (key.upArrow) {
				setSelectIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setSelectIndex((i) => Math.min(selectModal.options.length - 1, i + 1));
				return;
			}
			if (key.return) {
				const selected = selectModal.options[selectIndex];
				if (selected) {
					selectModal.onSelect(selected.value);
				}
				return;
			}
			if (key.escape) {
				setSelectModal(null);
				return;
			}
			const num = parseInt(chunk, 10);
			if (num >= 1 && num <= selectModal.options.length) {
				const selected = selectModal.options[num - 1];
				if (selected) {
					selectModal.onSelect(selected.value);
				}
				return;
			}
			return;
		}

		// --- Scripted raw return ---
		if (rawReturnSubmit && key.return) {
			if (session.modal?.kind === 'question') {
				session.sendRequest({
					type: 'question_response',
					request_id: session.modal.request_id,
					answer: modalInput,
				});
				session.setModal(null);
				setModalInput('');
				return;
			}
			if (!session.modal && !session.busy && input.trim()) {
				onSubmit(input);
				return;
			}
		}

		// --- Permission modal ---
		if (session.modal?.kind === 'permission') {
			if (chunk.toLowerCase() === 'y') {
				session.sendRequest({
					type: 'permission_response',
					request_id: session.modal.request_id,
					allowed: true,
				});
				session.setModal(null);
				return;
			}
			if (chunk.toLowerCase() === 'n' || key.escape) {
				session.sendRequest({
					type: 'permission_response',
					request_id: session.modal.request_id,
					allowed: false,
				});
				session.setModal(null);
				return;
			}
			return;
		}

		// --- Question modal ---
		if (session.modal?.kind === 'question') {
			return;
		}

		// --- Transcript scrolling ---
		// PgUp/PgDn/Home/End/Shift+G work whether or not the agent is busy so
		// the user can browse history while a turn is running.  We use a
		// generous one-screen step (rows / 2) for PgUp/PgDn so navigation feels
		// snappy.
		const pageStep = Math.max(1, Math.floor(rows / 2));
		if (key.pageUp) {
			scrollUp(pageStep);
			return;
		}
		if (key.pageDown) {
			scrollDown(pageStep);
			return;
		}
		// Shift+G jumps to the live tail (vim-ish), Home jumps to top.
		if (chunk === 'G' && !key.ctrl && !key.meta) {
			scrollToBottom();
			return;
		}
		if (chunk === 'g' && !key.ctrl && !key.meta && !input) {
			scrollToTop();
			return;
		}

		if (session.busy) {
			return;
		}

		// --- Command picker ---
		if (showPicker) {
			if (key.upArrow) {
				setPickerIndex((i) => Math.max(0, i - 1));
				return;
			}
			if (key.downArrow) {
				setPickerIndex((i) => Math.min(commandHints.length - 1, i + 1));
				return;
			}
			if (key.return) {
				const selected = commandHints[pickerIndex];
				if (selected) {
					setInput('');
					if (!handleCommand(selected)) {
						onSubmit(selected);
					}
				}
				return;
			}
			if (key.tab) {
				const selected = commandHints[pickerIndex];
				if (selected) {
					setInput(selected);
					setCompletionKey((k) => k + 1);
				}
				return;
			}
			if (key.escape) {
				setInput('');
				return;
			}
		}

		if (key.escape) {
			// ESC also resumes follow mode if we're scrolled away.
			if (pausedAt !== null) {
				scrollToBottom();
				return;
			}
			const now = Date.now();
			if (input && now - lastEscapeAt < 500) {
				setInput('');
				setHistoryIndex(-1);
				setLastEscapeAt(0);
				return;
			}
			setLastEscapeAt(now);
			return;
		}

		// --- History navigation ---
		if (!showPicker && key.upArrow) {
			const nextIndex = Math.min(history.length - 1, historyIndex + 1);
			if (nextIndex >= 0) {
				setHistoryIndex(nextIndex);
				setInput(history[history.length - 1 - nextIndex] ?? '');
			}
			return;
		}
		if (!showPicker && key.downArrow) {
			const nextIndex = Math.max(-1, historyIndex - 1);
			setHistoryIndex(nextIndex);
			setInput(nextIndex === -1 ? '' : (history[history.length - 1 - nextIndex] ?? ''));
			return;
		}
	});

	const onSubmit = (value: string): void => {
		if (session.modal?.kind === 'question') {
			session.sendRequest({
				type: 'question_response',
				request_id: session.modal.request_id,
				answer: value,
			});
			session.setModal(null);
			setModalInput('');
			return;
		}
		if (!value.trim() || session.busy || !session.ready) {
			return;
		}
		// Submitting always returns to the live tail so the user sees their
		// own message and the agent's reply.
		scrollToBottom();
		if (handleCommand(value)) {
			setHistory((items) => [...items, value]);
			setHistoryIndex(-1);
			setInput('');
			return;
		}
		session.sendRequest({type: 'submit_line', line: value});
		setHistory((items) => [...items, value]);
		setHistoryIndex(-1);
		setInput('');
		session.setBusy(true);
	};

	// Scripted automation
	useEffect(() => {
		if (scriptIndex >= scriptedSteps.length) {
			return;
		}
		if (session.busy || session.modal || selectModal) {
			return;
		}
		const step = scriptedSteps[scriptIndex];
		const timer = setTimeout(() => {
			onSubmit(step);
			setScriptIndex((index) => index + 1);
		}, 200);
		return () => clearTimeout(timer);
	}, [scriptIndex, session.busy, session.modal, selectModal]);

	const showWelcome = session.ready && outputStyle !== 'codex';
	const isPaused = pausedAt !== null;

	return (
		<Box flexDirection="column" height={rows} paddingX={1}>
			<ConversationView
				transcript={transcriptForView}
				assistantBuffer={assistantBufferForView}
				showWelcome={showWelcome}
				outputStyle={outputStyle}
			/>

			{isPaused ? (
				<Box flexShrink={0} flexDirection="column">
					<Text color={theme.colors.warning} dimColor>
						— history view (PgDn/End/G to resume) —
					</Text>
				</Box>
			) : null}

			{session.modal ? (
				<Box flexShrink={0} flexDirection="column">
					<ModalHost
						modal={session.modal}
						modalInput={modalInput}
						setModalInput={setModalInput}
						onSubmit={onSubmit}
					/>
				</Box>
			) : null}

			{selectModal ? (
				<Box flexShrink={0} flexDirection="column">
					<SelectModal title={selectModal.title} options={selectModal.options} selectedIndex={selectIndex} />
				</Box>
			) : null}

			{showPicker ? (
				<Box flexShrink={0} flexDirection="column">
					<CommandPicker hints={commandHints} selectedIndex={pickerIndex} />
				</Box>
			) : null}

			{session.ready && deferredTodoMarkdown ? (
				<Box flexShrink={0} flexDirection="column">
					<TodoPanel markdown={deferredTodoMarkdown} />
				</Box>
			) : null}

			{session.ready && (deferredSwarmTeammates.length > 0 || deferredSwarmNotifications.length > 0) ? (
				<Box flexShrink={0} flexDirection="column">
					<SwarmPanel teammates={deferredSwarmTeammates} notifications={deferredSwarmNotifications} />
				</Box>
			) : null}

			{session.ready ? (
				<Box flexShrink={0} flexDirection="column">
					<StatusBar status={deferredStatus} tasks={deferredTasks} activeToolName={session.busy ? currentToolName : undefined} />
				</Box>
			) : null}

			{!session.ready ? (
				<Box flexShrink={0} flexDirection="column">
					<Text color={theme.colors.warning}>Connecting to backend...</Text>
				</Box>
			) : session.modal || selectModal ? null : (
				<Box flexShrink={0} flexDirection="column">
					<PromptInput
						busy={session.busy}
						input={input}
						setInput={setInput}
						onSubmit={onSubmit}
						toolName={session.busy ? currentToolName : undefined}
						statusLabel={session.busy ? (session.busyLabel ?? (currentToolName ? `Running ${currentToolName}...` : 'Running agent loop...')) : undefined}
						suppressSubmit={showPicker}
						inputKey={completionKey}
					/>
				</Box>
			)}
		</Box>
	);
}
