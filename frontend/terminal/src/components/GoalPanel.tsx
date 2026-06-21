import React, {useEffect, useRef, useState} from 'react';
import {Box, Text, useInput} from 'ink';

import type {GoalSnapshot} from '../types.js';

const TIMER_INTERVAL_MS = 1_000;

function formatElapsed(ms: number): string {
	const secs = Math.floor(ms / 1000);
	const mins = Math.floor(secs / 60);
	const s = secs % 60;
	if (mins > 0) {
		return `${mins}m${s.toString().padStart(2, '0')}s`;
	}
	return `${s}s`;
}

function formatTokens(n: number): string {
	if (n >= 1000) {
		return `${(n / 1000).toFixed(1)}k`;
	}
	return String(n);
}

function statusDotColor(status: string): string {
	switch (status) {
		case 'active': return 'cyan';
		case 'blocked': return 'yellow';
		case 'paused': return 'gray';
		case 'complete': return 'green';
		default: return 'gray';
	}
}

function statusLabel(status: string): string {
	switch (status) {
		case 'active': return 'active';
		case 'blocked': return 'blocked';
		case 'paused': return 'paused';
		case 'complete': return 'complete';
		default: return status;
	}
}

function GoalPanelInner({
	goal,
	compact: initialCompact = true,
}: {
	goal: GoalSnapshot;
	compact?: boolean;
}): React.JSX.Element | null {
	type Mode = 'auto' | 'expanded' | 'compact';
	const [mode, setMode] = useState<Mode>(initialCompact ? 'compact' : 'auto');

	const prevTurnsRef = useRef(goal.turns_used);
	useEffect(() => {
		if (prevTurnsRef.current !== goal.turns_used) {
			prevTurnsRef.current = goal.turns_used;
			setMode('auto');
		}
	}, [goal.turns_used]);

	// Live timer: tick every second when the goal is active so elapsed time
	// updates between backend refresh events.
	const [tick, setTick] = useState(0);
	const observedAtRef = useRef(Date.now());
	const prevWallClockRef = useRef(goal.wall_clock_ms);
	useEffect(() => {
		// Reset the observation anchor when the backend pushes new stats.
		if (prevWallClockRef.current !== goal.wall_clock_ms) {
			prevWallClockRef.current = goal.wall_clock_ms;
			observedAtRef.current = Date.now();
		}
	}, [goal.wall_clock_ms]);
	useEffect(() => {
		if (goal.status !== 'active') {
			return;
		}
		observedAtRef.current = Date.now();
		const id = setInterval(() => setTick((t) => t + 1), TIMER_INTERVAL_MS);
		return () => clearInterval(id);
	}, [goal.status]);

	const liveElapsedMs = goal.status === 'active'
		? goal.wall_clock_ms + (Date.now() - observedAtRef.current)
		: goal.wall_clock_ms;
	// Reference tick so React re-renders when the interval fires.
	void tick;

	useInput((chunk, key) => {
		if (key.ctrl && chunk === 'g') {
			setMode((m) => {
				const isCollapsed = m === 'compact' || (m === 'auto' && initialCompact);
				return isCollapsed ? 'expanded' : 'compact';
			});
		}
	});

	const color = statusDotColor(goal.status);
	const label = statusLabel(goal.status);
	const elapsed = formatElapsed(liveElapsedMs);
	const turns = goal.budget.turn_budget != null
		? `${goal.turns_used}/${goal.budget.turn_budget}`
		: String(goal.turns_used);
	const tokens = formatTokens(goal.tokens_used);

	const compact = mode === 'compact' || (mode === 'auto' && initialCompact);

	if (compact) {
		return (
			<Box>
				<Text color={color} bold>●</Text>
				<Text> </Text>
				<Text color={color} bold>Goal</Text>
				<Text dimColor>{` ${label} · ${elapsed} · ${turns} turns · ${tokens} tokens`}</Text>
				<Text dimColor> [ctrl+g expand]</Text>
			</Box>
		);
	}

	return (
		<Box flexDirection="column" borderStyle="round" borderColor={color} paddingX={1} marginTop={1}>
			<Box>
				<Text color={color} bold>● Goal · {label}</Text>
				<Text dimColor> [ctrl+g collapse]</Text>
			</Box>
			<Box marginTop={1}>
				<Text>{goal.objective}</Text>
			</Box>
			{goal.completion_criterion && (
				<Box marginTop={1}>
					<Text dimColor>✓ {goal.completion_criterion}</Text>
				</Box>
			)}
			<Box marginTop={1}>
				<Text dimColor>
					Running    {elapsed}
				</Text>
			</Box>
			<Box>
				<Text dimColor>
					Turns      {turns}
				</Text>
			</Box>
			<Box>
				<Text dimColor>
					Tokens     {tokens}
				</Text>
			</Box>
			{goal.budget.over_budget && (
				<Box marginTop={1}>
					<Text color="yellow" bold>⚠ Over budget</Text>
				</Box>
			)}
		</Box>
	);
}

export const GoalPanel = React.memo(GoalPanelInner);
