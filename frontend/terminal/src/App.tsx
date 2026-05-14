import React, {useCallback, useDeferredValue, useEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useApp, useInput, useStdin} from 'ink';

import {AlternateScreen} from './components/AlternateScreen.js';
import {CommandPicker, createCommandPickerModel} from './components/CommandPicker.js';
import {ConversationView, type ConversationViewHandle} from './components/ConversationView.js';
import {
	ExpandedComposer,
	applyExpandedComposerInput,
	completeLeadingCommand,
	composePromptDraft,
	createExpandedComposerState,
	deleteComposerBackward,
	deleteComposerForward,
	getExpandedComposerSendHitbox,
	getPromptExpandTriggerHitbox,
	hitboxContainsPoint,
	insertComposerText,
	moveComposerCursor,
	splitExpandedDraft,
	type ExpandedComposerState,
} from './components/ExpandedComposer.js';
import {ModalHost} from './components/ModalHost.js';
import {InlineActivityIndicator} from './components/InlineActivityIndicator.js';
import {PromptInput, shouldAnimateBackgroundCue} from './components/PromptInput.js';
import {nextSelectIndex, nextSelectIndexForWheel, SelectModal, type SelectOption} from './components/SelectModal.js';
import {StatusBar} from './components/StatusBar.js';
import {SwarmPanel} from './components/SwarmPanel.js';
import {TodoPanel} from './components/TodoPanel.js';
import type {TerminalInputStream} from './input/terminalInput.js';
import {useBackendSession} from './hooks/useBackendSession.js';
import {useElapsedTimer} from './hooks/useElapsedTimer.js';
import {useMouseWheel} from './hooks/useMouseWheel.js';
import {useTerminalMouse} from './hooks/useTerminalMouse.js';
import {useTerminalSize} from './hooks/useTerminalSize.js';
import {discoverMentionFiles, filterMentionCandidates, findMentionQuery, replaceMentionQuery} from './input/mentions.js';
import {
	advanceShellCompletionCycle,
	discoverShellCommandCandidates,
	discoverShellPathCandidates,
	filterShellCommandCandidates,
	findShellCompletionQuery,
	type ShellCompletionCycle,
} from './input/shellCompletions.js';
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
	'/skills',
]);
const ACTIVE_BACKGROUND_TASK_STATUSES = new Set(['pending', 'running']);
const DOUBLE_ESCAPE_WINDOW_MS = 500;

type SelectModalState = {
	title: string;
	command: string;
	options: SelectOption[];
	onSelect: (value: string) => void;
} | null;

function normalizeSelectModalCommand(command: string): string {
	return command.trim().replace(/^\/+/, '').toLowerCase();
}

function supportsSelectModalFilter(command: string): boolean {
	return normalizeSelectModalCommand(command) === 'skills';
}

export function filterSelectModalOptions(
	command: string,
	options: SelectOption[],
	query: string,
): SelectOption[] {
	if (!supportsSelectModalFilter(command)) {
		return options;
	}

	const normalizedQuery = query.trim().toLowerCase();
	if (!normalizedQuery) {
		return options;
	}

	return options.filter((option) => {
		const skillName = (option.label || option.value).trim().toLowerCase();
		return skillName.includes(normalizedQuery);
	});
}

export function resolveSelectModalChoice(
	command: string,
	value: string,
): {kind: 'prefill'; input: string} | {kind: 'apply'; command: string; value: string} {
	const normalizedCommand = normalizeSelectModalCommand(command);
	const normalizedValue = value.trim();
	if (normalizedCommand === 'skills') {
		return {kind: 'prefill', input: normalizedValue ? `/${normalizedValue} ` : '/'};
	}
	return {kind: 'apply', command, value};
}

export function buildSubmittedValue(value: string, extraInputLines: string[]): string | null {
	const fullValue =
		extraInputLines.length > 0 ? [...extraInputLines, value].join('\n') : value;
	return fullValue.trim() ? fullValue : null;
}

export function shouldEnterShellModeFromInput(
	value: string,
	inputMode: 'chat' | 'shell',
	extraInputLineCount: number,
): boolean {
	return inputMode === 'chat' && extraInputLineCount === 0 && value === '!';
}

export function cyclePickerIndex(currentIndex: number, delta: number, itemCount: number): number {
	if (itemCount <= 0) {
		return 0;
	}
	return (currentIndex + delta + itemCount) % itemCount;
}

export function buildSlashCommandSelection(rootCommand: string, subcommand?: string): string {
	const trimmedRoot = rootCommand.trim();
	const trimmedSubcommand = subcommand?.trim();
	if (!trimmedSubcommand) {
		return trimmedRoot;
	}
	return `${trimmedRoot} ${trimmedSubcommand} `;
}

export function resolveEscapeAction({
	busy,
	paused,
	hasInput,
	now,
	lastEscapeAt,
}: {
	busy: boolean;
	paused: boolean;
	hasInput: boolean;
	now: number;
	lastEscapeAt: number;
}): {action: 'resume_follow' | 'cancel_busy_turn' | 'clear_input' | 'arm_escape'; nextLastEscapeAt: number} {
	if (paused) {
		return {
			action: 'resume_follow',
			nextLastEscapeAt: busy ? now : lastEscapeAt,
		};
	}

	const doublePressed = lastEscapeAt > 0 && now - lastEscapeAt < DOUBLE_ESCAPE_WINDOW_MS;
	if (busy && doublePressed) {
		return {action: 'cancel_busy_turn', nextLastEscapeAt: 0};
	}
	if (hasInput && doublePressed) {
		return {action: 'clear_input', nextLastEscapeAt: 0};
	}
	return {action: 'arm_escape', nextLastEscapeAt: now};
}

export function App({config}: {config: FrontendConfig}): React.JSX.Element {
	const initialTheme = String((config as Record<string, unknown>).theme ?? 'default');
	return (
		<ThemeProvider initialTheme={initialTheme}>
			<AppShell config={config} />
		</ThemeProvider>
	);
}

function AppShell({config}: {config: FrontendConfig}): React.JSX.Element {
	const [mouseTracking, setMouseTracking] = useState(true);
	return (
		<AlternateScreen mouseTracking={mouseTracking}>
			<AppInner config={config} mouseTracking={mouseTracking} setMouseTracking={setMouseTracking} />
		</AlternateScreen>
	);
}

function AppInner({
	config,
	mouseTracking,
	setMouseTracking,
}: {
	config: FrontendConfig;
	mouseTracking: boolean;
	setMouseTracking: (value: boolean | ((prev: boolean) => boolean)) => void;
}): React.JSX.Element {
	const {exit} = useApp();
	const {stdin: _stdin} = useStdin();
	const _terminalInput = _stdin as unknown as TerminalInputStream;
	const {theme, setThemeName} = useTheme();
	const {rows, cols} = useTerminalSize();
	const [input, setInput] = useState('');
	const [inputMode, setInputMode] = useState<'chat' | 'shell'>('chat');
	const inputRef = useRef(input);
	const inputModeRef = useRef(inputMode);
	const [completionKey, setCompletionKey] = useState(0);
	const [modalInput, setModalInput] = useState('');
	const [history, setHistory] = useState<string[]>([]);
	const [historyIndex, setHistoryIndex] = useState(-1);
	const [lastEscapeAt, setLastEscapeAt] = useState(0);
	const [lastCtrlCAt, setLastCtrlCAt] = useState(0);
	const [extraInputLines, setExtraInputLines] = useState<string[]>([]);
	const [pickerSubIndex, setPickerSubIndex] = useState(0);
	const [pickerSubmenuFocused, setPickerSubmenuFocused] = useState(false);
	// Used to skip ink-text-input's onSubmit when Shift+Enter was just handled
	const shiftEnterHandledRef = useRef(false);
	const onSubmitRef = useRef<(value: string) => void>(() => {});
	const onSubmit = useCallback((value: string): void => {
		onSubmitRef.current(value);
	}, []);
	// When App's useInput handles a Ctrl+<letter> shortcut that isn't filtered
	// by ink-text-input (which only filters Ctrl+C), we record the letter so
	// handleInputChange can strip the spurious insert from the composer.
	const suppressNextCharRef = useRef<string | null>(null);
	const [scriptIndex, setScriptIndex] = useState(0);
	const [pickerIndex, setPickerIndex] = useState(0);
	const [selectModal, setSelectModal] = useState<SelectModalState>(null);
	const [selectIndex, setSelectIndex] = useState(0);
	const [selectQuery, setSelectQuery] = useState('');
	const [mentionFiles, setMentionFiles] = useState<string[]>([]);
	const [shellCommands, setShellCommands] = useState<string[]>([]);
	const [shellPathHints, setShellPathHints] = useState<string[]>([]);
	const [shellCompletionCycle, setShellCompletionCycle] = useState<ShellCompletionCycle | null>(null);
	const shellCompletionCycleRef = useRef<ShellCompletionCycle | null>(null);
	const [expandedComposer, setExpandedComposer] = useState<ExpandedComposerState | null>(null);

	const setInputValue = useCallback((next: string): void => {
		inputRef.current = next;
		setInput(next);
	}, []);

	const setInputModeValue = useCallback((next: 'chat' | 'shell'): void => {
		inputModeRef.current = next;
		setInputMode(next);
	}, []);

	const setShellCompletionCycleValue = useCallback((next: ShellCompletionCycle | null): void => {
		shellCompletionCycleRef.current = next;
		setShellCompletionCycle(next);
	}, []);

	const visibleSelectOptions = useMemo(
		() => (selectModal ? filterSelectModalOptions(selectModal.command, selectModal.options, selectQuery) : []),
		[selectModal, selectQuery],
	);
	const selectModalSupportsFilter = selectModal ? supportsSelectModalFilter(selectModal.command) : false;

	// Scroll state (line-level via ref to ConversationView).
	// `paused` is mirrored from ConversationView via onPauseChange; we keep
	// it here only so the App can render a "history view" indicator and
	// ESC can resume the live tail.  The actual scroll math (line offset,
	// content/viewport measurement, clamping) lives inside ConversationView
	// where Ink's measureElement gives us pixel-accurate dimensions.
	const conversationRef = useRef<ConversationViewHandle | null>(null);
	const [paused, setPaused] = useState(false);

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

	// Scroll helpers — thin wrappers around the ref API.
	const scrollUp = useCallback((step: number) => {
		conversationRef.current?.scrollUp(step);
	}, []);

	const scrollDown = useCallback((step: number) => {
		conversationRef.current?.scrollDown(step);
	}, []);

	const scrollToBottom = useCallback(() => {
		conversationRef.current?.scrollToBottom();
	}, []);

	const scrollToTop = useCallback(() => {
		conversationRef.current?.scrollToTop();
	}, []);

	useMouseWheel((delta) => {
		if (selectModal) {
			setSelectIndex((i) => nextSelectIndexForWheel(i, delta, visibleSelectOptions.length));
			return;
		}
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
	const activeBackgroundTaskCount = useMemo(
		() => deferredTasks.filter((task) => ACTIVE_BACKGROUND_TASK_STATUSES.has(task.status)).length,
		[deferredTasks],
	);
	const oldestActiveBackgroundStartedAt = useMemo(() => {
		const startedTimes = deferredTasks
			.filter((task) => ACTIVE_BACKGROUND_TASK_STATUSES.has(task.status) && typeof task.started_at === 'number')
			.map((task) => task.started_at as number);
		return startedTimes.length > 0 ? Math.min(...startedTimes) : undefined;
	}, [deferredTasks]);
	const hasActiveWork = session.busy || activeBackgroundTaskCount > 0;
	const inlineActivityEnabled = shouldAnimateBackgroundCue();
	const isFullAuto = String(deferredStatus.permission_mode ?? 'default') === 'Auto';
	const elapsedSeconds = useElapsedTimer(
		session.busy && isFullAuto && !inlineActivityEnabled,
	);

	// Command hints
	const commandPickerModel = useMemo(
		() => createCommandPickerModel(session.commands, input, session.skills),
		[session.commands, session.skills, input],
	);
	const expandedCommandPickerModel = useMemo(
		() => expandedComposer
			? createCommandPickerModel(session.commands, expandedComposer.draft, session.skills)
			: {hints: [] as string[], subHintsByHint: {} as Record<string, string[]>},
		[session.commands, session.skills, expandedComposer],
	);
	const commandHints = commandPickerModel.hints;
	const mentionQuery = useMemo(() => findMentionQuery(input), [input]);
	const mentionHints = useMemo(() => {
		if (!mentionQuery) {
			return [] as string[];
		}
		return filterMentionCandidates(mentionFiles, mentionQuery.query);
	}, [mentionFiles, mentionQuery]);
	const shellCompletionQuery = useMemo(
		() => inputMode === 'shell' ? findShellCompletionQuery(input) : null,
		[input, inputMode],
	);
	const shellHints = useMemo(() => {
		if (inputMode !== 'shell' || !shellCompletionQuery) {
			return [] as string[];
		}
		if (shellCompletionQuery.kind === 'path') {
			return shellPathHints;
		}
		return filterShellCommandCandidates(shellCommands, shellCompletionQuery.query);
	}, [inputMode, shellCommands, shellCompletionQuery, shellPathHints]);
	const pickerHints = mentionHints.length > 0 ? mentionHints : commandHints;
	const pickerTitle = mentionHints.length > 0 ? 'Files' : 'Commands & Skills';
	const selectedPickerHint = pickerHints[pickerIndex];
	const pickerSubHints = mentionHints.length > 0
		? [] as string[]
		: selectedPickerHint ? (commandPickerModel.subHintsByHint[selectedPickerHint] ?? []) : [];

	const showPicker = inputMode === 'chat' && !expandedComposer && pickerHints.length > 0 && !session.busy && !session.modal && !selectModal;
	const outputStyle = String(session.status.output_style ?? 'default');

	useEffect(() => {
		setPickerIndex(0);
		setPickerSubIndex(0);
		setPickerSubmenuFocused(false);
	}, [pickerHints.length, input]);

	useEffect(() => {
		if (pickerSubHints.length === 0 && pickerSubmenuFocused) {
			setPickerSubmenuFocused(false);
		}
		if (pickerSubIndex >= pickerSubHints.length) {
			setPickerSubIndex(Math.max(0, pickerSubHints.length - 1));
		}
	}, [pickerSubHints.length, pickerSubIndex, pickerSubmenuFocused]);

	useEffect(() => {
		let cancelled = false;
		const cwd = String(session.status.cwd ?? process.cwd());
		void discoverMentionFiles(cwd).then((files) => {
			if (!cancelled) {
				setMentionFiles(files);
			}
		});
		return () => {
			cancelled = true;
		};
	}, [session.status.cwd]);

	useEffect(() => {
		let cancelled = false;
		void discoverShellCommandCandidates().then((commands) => {
			if (!cancelled) {
				setShellCommands(commands);
			}
		});
		return () => {
			cancelled = true;
		};
	}, []);

	useEffect(() => {
		if (inputMode !== 'shell' || !shellCompletionQuery || shellCompletionQuery.kind !== 'path') {
			setShellPathHints([]);
			return;
		}
		let cancelled = false;
		const cwd = String(session.status.cwd ?? process.cwd());
		void discoverShellPathCandidates(cwd, shellCompletionQuery.query).then((hints) => {
			if (!cancelled) {
				setShellPathHints(hints);
			}
		});
		return () => {
			cancelled = true;
		};
	}, [inputMode, session.status.cwd, shellCompletionQuery]);

	useEffect(() => {
		if (!shellCompletionCycle) {
			return;
		}
		if (inputMode !== 'shell' || input !== shellCompletionCycle.value) {
			setShellCompletionCycleValue(null);
		}
	}, [input, inputMode, shellCompletionCycle, setShellCompletionCycleValue]);

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
		setSelectQuery('');
		setSelectModal({
			title: req.title,
			command: req.command,
			options: req.options.map((o) => ({
				value: o.value,
				label: o.label,
				description: o.description,
				active: o.active,
				badge: o.badge,
				badgeTone: o.badgeTone,
			})),
			onSelect: (value) => {
				const selection = resolveSelectModalChoice(req.command, value);
				if (selection.kind === 'prefill') {
					setInputValue(selection.input);
					setExtraInputLines([]);
					setHistoryIndex(-1);
					setCompletionKey((key) => key + 1);
					setSelectModal(null);
					return;
				}
				session.sendRequest({type: 'apply_select_command', command: selection.command, value: selection.value});
				session.setBusy(true);
				setSelectModal(null);
			},
		});
		session.setSelectRequest(null);
	}, [session.selectRequest]);

	useEffect(() => {
		if (!selectModal) {
			return;
		}
		if (visibleSelectOptions.length === 0) {
			if (selectIndex !== 0) {
				setSelectIndex(0);
			}
			return;
		}
		if (selectIndex >= visibleSelectOptions.length) {
			setSelectIndex(visibleSelectOptions.length - 1);
		}
	}, [selectIndex, selectModal, visibleSelectOptions.length]);

	const restoreExpandedDraftToPrompt = useCallback((draft: string) => {
		const split = splitExpandedDraft(draft);
		setInputValue(split.input);
		setExtraInputLines(split.extraInputLines);
		setHistoryIndex(-1);
		setCompletionKey((key) => key + 1);
	}, [setInputValue]);

	const closeExpandedComposer = useCallback((draft?: string) => {
		if (draft != null) {
			restoreExpandedDraftToPrompt(draft);
		}
		setExpandedComposer(null);
	}, [restoreExpandedDraftToPrompt]);

	const openExpandedComposer = useCallback(() => {
		if (expandedComposer || !session.ready || session.busy || session.modal || selectModal) {
			return;
		}
		setExpandedComposer(createExpandedComposerState(composePromptDraft(input, extraInputLines)));
	}, [expandedComposer, extraInputLines, input, selectModal, session.busy, session.modal, session.ready]);

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

	const submitSubmittedValue = useCallback((submittedValue: string): boolean => {
		if (!submittedValue || !session.ready) {
			return false;
		}
		if (session.busy) {
			if (submittedValue.trim() === '/stop') {
				session.sendRequest({type: 'cancel_line'});
				return true;
			}
			return false;
		}
		const trimmed = submittedValue.trim();
		// Bare "!" toggles shell mode on (only meaningful in chat mode).
		if (inputMode === 'chat' && trimmed === '!') {
			setInputModeValue('shell');
			setShellCompletionCycleValue(null);
			return true;
		}
		// "exit"/"quit" inside shell mode returns to chat without sending.
		if (inputMode === 'shell' && (trimmed === 'exit' || trimmed === 'quit')) {
			setInputModeValue('chat');
			setShellCompletionCycleValue(null);
			return true;
		}
		scrollToBottom();
		if (inputMode === 'chat' && handleCommand(submittedValue)) {
			setHistory((items) => [...items, submittedValue]);
			setHistoryIndex(-1);
			return true;
		}
		const payload: Record<string, unknown> = {type: 'submit_line', line: submittedValue};
		if (inputMode === 'shell') {
			payload.input_mode = 'shell';
		}
		session.sendRequest(payload);
		setHistory((items) => [...items, submittedValue]);
		setHistoryIndex(-1);
		session.setBusy(true);
		return true;
	}, [handleCommand, inputMode, scrollToBottom, session]);

	const submitExpandedComposer = useCallback(() => {
		if (!expandedComposer) {
			return;
		}
		const submittedValue = expandedComposer.draft.trim() ? expandedComposer.draft : null;
		if (!submittedValue || !submitSubmittedValue(submittedValue)) {
			return;
		}
		setExpandedComposer(null);
		setInputValue('');
		setExtraInputLines([]);
		setCompletionKey((key) => key + 1);
	}, [expandedComposer, setInputValue, submitSubmittedValue]);

	const applyShellTabCompletion = useCallback(() => {
		const currentInput = inputRef.current;
		const currentCycle = shellCompletionCycleRef.current;
		if (inputModeRef.current !== 'shell') {
			return;
		}

		if (currentCycle && currentCycle.value === currentInput) {
			const completion = advanceShellCompletionCycle(currentInput, null, [], currentCycle);
			setShellCompletionCycleValue(completion.nextCycle);
			if (completion.nextValue != null) {
				setInputValue(completion.nextValue);
				setHistoryIndex(-1);
				setCompletionKey((key) => key + 1);
			}
			return;
		}

		const currentQuery = findShellCompletionQuery(currentInput);
		if (!currentQuery) {
			setShellCompletionCycleValue(null);
			return;
		}

		void (async () => {
			let candidates: string[] = [];

			if (currentQuery.kind === 'command') {
				const commands = shellCommands.length > 0
					? shellCommands
					: await discoverShellCommandCandidates(process.env.PATH ?? '', 2000, currentQuery.query);
				if (commands !== shellCommands) {
					setShellCommands(commands);
				}
				candidates = filterShellCommandCandidates(commands, currentQuery.query);
			} else {
				const cwd = String(session.status.cwd ?? process.cwd());
				candidates = await discoverShellPathCandidates(cwd, currentQuery.query);
				setShellPathHints(candidates);
			}

			if (inputModeRef.current !== 'shell' || inputRef.current !== currentInput) {
				return;
			}

			const completion = advanceShellCompletionCycle(currentInput, currentQuery, candidates, null);
			setShellCompletionCycleValue(completion.nextCycle);
			if (completion.nextValue != null) {
				setInputValue(completion.nextValue);
				setHistoryIndex(-1);
				setCompletionKey((key) => key + 1);
			}
		})();
	}, [session.status.cwd, setInputValue, setShellCompletionCycleValue, shellCommands]);
	useTerminalMouse(useCallback((event) => {
		if (event.kind !== 'button' || event.action !== 'release' || event.buttonCode !== 0) {
			return;
		}
		if (expandedComposer) {
			if (hitboxContainsPoint(getExpandedComposerSendHitbox(cols, rows), event.column, event.row)) {
				submitExpandedComposer();
			}
			return;
		}
		if (!session.ready || session.busy || session.modal || selectModal) {
			return;
		}
		if (hitboxContainsPoint(getPromptExpandTriggerHitbox(cols, rows), event.column, event.row)) {
			openExpandedComposer();
		}
	}, [cols, expandedComposer, openExpandedComposer, rows, selectModal, session.busy, session.modal, session.ready, submitExpandedComposer]));

	useInput((chunk, key) => {
		const isPaste = chunk.length > 1 && !key.ctrl && !key.meta;

		if (expandedComposer) {
			if (key.escape) {
				closeExpandedComposer(expandedComposer.draft);
				return;
			}
			if (key.leftArrow) {
				setExpandedComposer((current) => (current ? moveComposerCursor(current, 'left') : current));
				return;
			}
			if (key.rightArrow) {
				setExpandedComposer((current) => (current ? moveComposerCursor(current, 'right') : current));
				return;
			}
			if (key.upArrow) {
				setExpandedComposer((current) => (current ? moveComposerCursor(current, 'up') : current));
				return;
			}
			if (key.downArrow) {
				setExpandedComposer((current) => (current ? moveComposerCursor(current, 'down') : current));
				return;
			}
			if (key.backspace) {
				setExpandedComposer((current) => (current ? deleteComposerBackward(current) : current));
				return;
			}
			if (key.delete) {
				setExpandedComposer((current) => (current ? deleteComposerForward(current) : current));
				return;
			}
			if (key.tab) {
				const selected = expandedCommandPickerModel.hints[0];
				if (selected) {
					setExpandedComposer((current) => {
						if (!current) {
							return current;
						}
						const completed = completeLeadingCommand(current.draft, selected);
						return {
							draft: completed.draft,
							cursorOffset: completed.cursorOffset,
							preferredColumn: null,
						};
					});
					return;
				}
				setExpandedComposer((current) => (current ? insertComposerText(current, '\t') : current));
				return;
			}
			if (key.return) {
				setExpandedComposer((current) => (current ? insertComposerText(current, '\n') : current));
				return;
			}
			if (key.ctrl && chunk === 'a') {
				setExpandedComposer((current) => (current ? moveComposerCursor(current, 'home') : current));
				return;
			}
			if (key.ctrl && chunk === 'e') {
				setExpandedComposer((current) => (current ? moveComposerCursor(current, 'end') : current));
				return;
			}
			if (key.ctrl || key.meta) {
				return;
			}
			if (chunk) {
				setExpandedComposer((current) => (current ? applyExpandedComposerInput(current, chunk) : current));
			}
			return;
		}

		if (key.ctrl && chunk === 'c') {
			if (session.busy) {
				session.sendRequest({type: 'cancel_line'});
				return;
			}
			const now = Date.now();
			if (now - lastCtrlCAt < 1000) {
				// Two Ctrl+C within 1 s → shut down and exit
				session.sendRequest({type: 'shutdown'});
				exit();
				return;
			}
			// First Ctrl+C → clear input and record timestamp
			setInputValue('');
			setExtraInputLines([]);
			setHistoryIndex(-1);
			setShellCompletionCycleValue(null);
			setLastCtrlCAt(now);
			return;
		}

		// Ctrl+X toggles mouse capture so the user can drag-select text in
		// their terminal to copy.  When disabled, in-app wheel scrolling is
		// unavailable until re-enabled (PgUp/PgDn still work).
		if (key.ctrl && chunk === 'x') {
			suppressNextCharRef.current = 'x';
			setMouseTracking((prev) => !prev);
			return;
		}

		// Ctrl+T is consumed by TodoPanel (toggles compact/expand). Suppress
		// ink-text-input's spurious 't' insertion into the composer.
		if (key.ctrl && chunk === 't') {
			suppressNextCharRef.current = 't';
			return;
		}

		// --- Select modal (permissions picker etc.) ---
		if (selectModal) {
			if (key.upArrow) {
				setSelectIndex((i) => nextSelectIndex(i, -1, visibleSelectOptions.length));
				return;
			}
			if (key.downArrow) {
				setSelectIndex((i) => nextSelectIndex(i, 1, visibleSelectOptions.length));
				return;
			}
			if (key.return) {
				const selected = visibleSelectOptions[selectIndex];
				if (selected) {
					selectModal.onSelect(selected.value);
				}
				return;
			}
			if (key.escape) {
				setSelectQuery('');
				setSelectModal(null);
				return;
			}
			if (selectModalSupportsFilter) {
				if (key.backspace || key.delete) {
					setSelectQuery((current) => current.slice(0, -1));
					setSelectIndex(0);
					return;
				}
				if (!key.ctrl && !key.meta && !key.tab && chunk) {
					setSelectQuery((current) => current + chunk);
					setSelectIndex(0);
					return;
				}
			} else {
				const num = parseInt(chunk, 10);
				if (num >= 1 && num <= selectModal.options.length) {
					const selected = selectModal.options[num - 1];
					if (selected) {
						selectModal.onSelect(selected.value);
					}
					return;
				}
			}
			return;
		}

		if (isPaste) {
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
			if (!session.modal && !session.busy && buildSubmittedValue(input, extraInputLines)) {
				onSubmit(input);
				return;
			}
		}

		// --- Permission modal ---
		if (session.modal?.kind === 'permission' || session.modal?.kind === 'edit_diff') {
			if (chunk.toLowerCase() === 'y') {
				session.sendRequest({
					type: 'permission_response',
					request_id: session.modal.request_id,
					permission_reply: 'once',
					allowed: true,
				});
				session.setModal(null);
				return;
			}
			if (chunk.toLowerCase() === 'a') {
				session.sendRequest({
					type: 'permission_response',
					request_id: session.modal.request_id,
					permission_reply: 'always',
					allowed: true,
				});
				session.setModal(null);
				return;
			}
			if (chunk.toLowerCase() === 'n' || key.escape) {
				session.sendRequest({
					type: 'permission_response',
					request_id: session.modal.request_id,
					permission_reply: 'reject',
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

		if (key.escape) {
			if (showPicker && !session.busy) {
				setInputValue('');
				return;
			}
			// Esc in shell mode with empty buffer returns to chat mode.
			if (inputMode === 'shell' && !session.busy && !input && extraInputLines.length === 0) {
				setInputModeValue('chat');
				setShellCompletionCycleValue(null);
				return;
			}
			const now = Date.now();
			const escapeAction = resolveEscapeAction({
				busy: session.busy,
				paused,
				hasInput: Boolean(input || extraInputLines.length > 0),
				now,
				lastEscapeAt,
			});
			setLastEscapeAt(escapeAction.nextLastEscapeAt);
			if (escapeAction.action === 'resume_follow') {
				scrollToBottom();
				return;
			}
			if (escapeAction.action === 'cancel_busy_turn') {
				session.sendRequest({type: 'cancel_line'});
				return;
			}
			if (escapeAction.action === 'clear_input') {
				setInputValue('');
				setExtraInputLines([]);
				setHistoryIndex(-1);
				setShellCompletionCycleValue(null);
				return;
			}
			return;
		}

		if (session.busy) {
			return;
		}

		if (inputMode === 'chat' && !showPicker && key.tab && input.trim() === '' && extraInputLines.length === 0) {
			session.sendRequest({type: 'select_command', command: 'permissions'});
			return;
		}

		if (inputMode === 'shell' && key.tab) {
			applyShellTabCompletion();
			return;
		}

		// --- Command picker ---
		if (showPicker) {
			if (key.upArrow) {
				if (pickerSubmenuFocused && pickerSubHints.length > 0) {
					setPickerSubIndex((i) => cyclePickerIndex(i, -1, pickerSubHints.length));
					return;
				}
				setPickerIndex((i) => cyclePickerIndex(i, -1, pickerHints.length));
				setPickerSubIndex(0);
				return;
			}
			if (key.downArrow) {
				if (pickerSubmenuFocused && pickerSubHints.length > 0) {
					setPickerSubIndex((i) => cyclePickerIndex(i, 1, pickerSubHints.length));
					return;
				}
				setPickerIndex((i) => cyclePickerIndex(i, 1, pickerHints.length));
				setPickerSubIndex(0);
				return;
			}
			if (key.rightArrow) {
				if (!mentionHints.length && pickerSubHints.length > 0) {
					setPickerSubmenuFocused(true);
					return;
				}
			}
			if (key.leftArrow) {
				if (pickerSubmenuFocused) {
					setPickerSubmenuFocused(false);
					return;
				}
			}
			if (key.return) {
				const selected = pickerHints[pickerIndex];
				if (selected) {
					if (!mentionHints.length && pickerSubmenuFocused && pickerSubHints.length > 0) {
						const selectedSubHint = pickerSubHints[pickerSubIndex];
						if (selectedSubHint) {
							setInputValue(buildSlashCommandSelection(selected, selectedSubHint));
							setHistoryIndex(-1);
							setCompletionKey((k) => k + 1);
						}
						return;
					}
					if (mentionQuery && mentionHints.length > 0) {
						setInputValue(replaceMentionQuery(input, mentionQuery, selected));
						setCompletionKey((k) => k + 1);
						return;
					}
					setInputValue('');
					if (!handleCommand(selected)) {
						onSubmit(selected);
					}
				}
				return;
			}
			if (key.tab) {
				const selected = pickerHints[pickerIndex];
				if (selected) {
					if (!mentionHints.length && pickerSubmenuFocused && pickerSubHints.length > 0) {
						const selectedSubHint = pickerSubHints[pickerSubIndex];
						if (selectedSubHint) {
							setInputValue(buildSlashCommandSelection(selected, selectedSubHint));
							setHistoryIndex(-1);
							setCompletionKey((k) => k + 1);
						}
						return;
					}
					if (mentionQuery && mentionHints.length > 0) {
						setInputValue(replaceMentionQuery(input, mentionQuery, selected));
						setCompletionKey((k) => k + 1);
						return;
					}
					setInputValue(selected);
					setCompletionKey((k) => k + 1);
				}
				return;
			}
			if (key.escape) {
				setInputValue('');
				return;
			}
		}

		// Shift+Enter appends current line to pending lines and starts a new one
		if (key.shift && key.return) {
			shiftEnterHandledRef.current = true;
			setExtraInputLines((lines) => [...lines, input]);
			setInputValue('');
			setShellCompletionCycleValue(null);
			return;
		}

		// --- History navigation ---
		if (!showPicker && key.upArrow) {
			const nextIndex = Math.min(history.length - 1, historyIndex + 1);
			if (nextIndex >= 0) {
				setHistoryIndex(nextIndex);
				setInputValue(history[history.length - 1 - nextIndex] ?? '');
				setShellCompletionCycleValue(null);
			}
			return;
		}
		if (!showPicker && key.downArrow) {
			const nextIndex = Math.max(-1, historyIndex - 1);
			setHistoryIndex(nextIndex);
			setInputValue(nextIndex === -1 ? '' : (history[history.length - 1 - nextIndex] ?? ''));
			setShellCompletionCycleValue(null);
			return;
		}
	});

	// Intercept \n inserted directly via onChange (terminals that send a bare
	// newline for Shift+Enter instead of a recognized key.return+key.shift combo).
	const handleInputChange = useCallback(
		(value: string) => {
			// Drop the character that ink-text-input inserted in the same tick
			// as a Ctrl+<letter> shortcut handled by App / TodoPanel.
			let next = value;
			const suppress = suppressNextCharRef.current;
			if (suppress) {
				suppressNextCharRef.current = null;
				const idx = next.lastIndexOf(suppress);
				if (idx >= 0) {
					next = next.slice(0, idx) + next.slice(idx + 1);
				}
			}
			if (!next.includes('\n')) {
				if (shouldEnterShellModeFromInput(next, inputMode, extraInputLines.length)) {
					setInputModeValue('shell');
					setInputValue('');
					setShellCompletionCycleValue(null);
					setCompletionKey((key) => key + 1);
					return;
				}
				setInputValue(next);
				if (shellCompletionCycleRef.current?.value !== next) {
					setShellCompletionCycleValue(null);
				}
				return;
			}
			const parts = next.split('\n');
			const lastPart = parts.pop() ?? '';
			setExtraInputLines((prev) => [...prev, ...parts]);
			setInputValue(lastPart);
			setShellCompletionCycleValue(null);
		},
		[extraInputLines.length, inputMode, setInputModeValue, setInputValue, setShellCompletionCycleValue],
	);

	onSubmitRef.current = (value: string): void => {
		// ink-text-input fires onSubmit for any key.return including shift+return;
		// skip if Shift+Enter was already handled by our useInput handler above.
		if (shiftEnterHandledRef.current) {
			shiftEnterHandledRef.current = false;
			return;
		}
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
		const submittedValue = buildSubmittedValue(value, extraInputLines);
		if (!submittedValue) {
			return;
		}
		if (!submitSubmittedValue(submittedValue)) {
			return;
		}
		setInputValue('');
		setExtraInputLines([]);
		setShellCompletionCycleValue(null);
		setCompletionKey((key) => key + 1);
	};

	// Scripted automation
	useEffect(() => {
		if (scriptIndex >= scriptedSteps.length) {
			return;
		}
		if (expandedComposer || session.busy || session.modal || selectModal) {
			return;
		}
		const step = scriptedSteps[scriptIndex];
		const timer = setTimeout(() => {
			onSubmit(step);
			setScriptIndex((index) => index + 1);
		}, 200);
		return () => clearTimeout(timer);
	}, [expandedComposer, scriptIndex, session.busy, session.modal, selectModal]);

	const showWelcome = session.ready && outputStyle !== 'codex';
	const isPaused = paused;

	if (expandedComposer) {
		return (
			<Box flexDirection="column" height={rows} paddingX={1}>
				<ExpandedComposer
					state={expandedComposer}
					commandHints={expandedCommandPickerModel.hints}
					subHintsByHint={expandedCommandPickerModel.subHintsByHint}
				/>
			</Box>
		);
	}

	return (
		<Box flexDirection="column" height={rows} paddingX={1}>
			<ConversationView
				ref={conversationRef}
				transcript={deferredTranscript}
				assistantBuffer={deferredAssistantBuffer}
				showWelcome={showWelcome}
				welcomeVersion={config.version}
				outputStyle={outputStyle}
				onPauseChange={setPaused}
			/>

			{isPaused ? (
				<Box flexShrink={0} flexDirection="column">
					<Text color={theme.colors.warning} dimColor>
						— history view (PgDn/End/G to resume) —
					</Text>
				</Box>
			) : null}

			{!mouseTracking ? (
				<Box flexShrink={0} flexDirection="column">
					<Text color={theme.colors.warning} dimColor>
						— select mode: drag to copy · ctrl+x to re-enable wheel scroll —
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
					<SelectModal
						title={selectModal.title}
						command={selectModal.command}
						options={visibleSelectOptions}
						selectedIndex={selectIndex}
						query={selectModalSupportsFilter ? selectQuery : undefined}
						filterLabel={selectModalSupportsFilter ? 'Skill name filter' : undefined}
						emptyStateLabel={selectModalSupportsFilter ? 'No matching skills.' : undefined}
					/>
				</Box>
			) : null}

			{showPicker ? (
				<Box flexShrink={0} flexDirection="column">
					<CommandPicker
						hints={pickerHints}
						selectedIndex={pickerIndex}
						title={pickerTitle}
						subHintsByHint={mentionHints.length > 0 ? {} : commandPickerModel.subHintsByHint}
						subSelectedIndex={pickerSubIndex}
						submenuFocused={pickerSubmenuFocused}
					/>
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
					<StatusBar
						status={deferredStatus}
						tasks={deferredTasks}
						activeToolName={session.busy ? currentToolName : undefined}
						elapsedSeconds={elapsedSeconds}
						busy={hasActiveWork}
						showTaskSegment={!inlineActivityEnabled || activeBackgroundTaskCount === 0}
					/>
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
						setInput={handleInputChange}
						onSubmit={onSubmit}
						extraInputLines={extraInputLines}
						toolName={session.busy ? currentToolName : undefined}
						statusLabel={session.busy ? (session.busyLabel ?? (currentToolName ? `Running ${currentToolName}` : 'Running agent loop')) : undefined}
						hasBackgroundTasks={activeBackgroundTaskCount > 0}
						suppressSubmit={showPicker}
						inputKey={completionKey}
						animateSpinner={!inlineActivityEnabled}
						inputMode={inputMode}
					/>
					<InlineActivityIndicator
						active={hasActiveWork}
						busy={session.busy}
						hasBackgroundTasks={activeBackgroundTaskCount > 0}
						activeBackgroundTaskCount={activeBackgroundTaskCount}
						statusLabel={session.busy ? (session.busyLabel ?? (currentToolName ? `Running ${currentToolName}` : 'Running agent loop')) : undefined}
						toolName={session.busy ? currentToolName : undefined}
						startedAtSeconds={activeBackgroundTaskCount > 0 ? oldestActiveBackgroundStartedAt : undefined}
					/>
				</Box>
			)}
		</Box>
	);
}
