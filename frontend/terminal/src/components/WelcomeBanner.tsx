import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import {useTerminalSize} from '../hooks/useTerminalSize.js';

// smslant font · OPENHARNESS · 4 rows × 55 cols
// prettier-ignore
const LOGO: ReadonlyArray<string> = [
'  ____  ___  _____  ____ _____   ___  _  ______________',
' / __ \\/ _ \\/ __/ |/ / // / _ | / _ \\/ |/ / __/ __/ __/',
'/ /_/ / ___/ _//    / _  / __ |/ , _/    / _/_\\ \\_\\ \\  ',
'\\____/_/  /___/_/|_/_//_/_/ |_/_/|_/_/|_/___/___/___/',
];
const LOGO_WIDTH = 55;

const COMMANDS: ReadonlyArray<string> = [
	'/help', '/model', '/theme', '/provider', '/resume', '/effort', '/turns',
];

const SHORTCUTS: ReadonlyArray<{key: string; hint: string}> = [
	{key: 'Ctrl+C', hint: 'exit'},
	{key: 'Ctrl+T', hint: 'todos'},
	{key: 'PgUp/Dn', hint: 'scroll'},
	{key: 'Esc Esc', hint: 'clear'},
];

export function WelcomeBanner({version}: {version?: string | null}): React.JSX.Element {
	const {theme} = useTheme();
	const {cols} = useTerminalSize();
	const showLogo = cols >= LOGO_WIDTH + 6;

	return (
		<Box
			flexDirection="column"
			marginTop={1}
			marginBottom={1}
			paddingLeft={1}
		>
			{/* ── ASCII logo ── */}
			{showLogo && (
				<Box flexDirection="column">
					{LOGO.map((line, i) => (
						<Text key={i} color={theme.colors.accent} bold>{line}</Text>
					))}
				</Box>
			)}

			{/* ── Product name + version ── */}
			<Box marginTop={showLogo ? 1 : 0}>
				<Text bold color={theme.colors.primary}>
					OpenHarness
				</Text>
				{version ? (
					<Text color={theme.colors.muted}> v{version}</Text>
				) : null}
			</Box>

			{/* ── Tagline ── */}
			<Box>
				<Text dimColor>autonomous coding agent</Text>
				<Text color={theme.colors.muted}>{' · '}</Text>
				<Text dimColor>streaming runtime</Text>
				<Text color={theme.colors.muted}>{' · '}</Text>
				<Text dimColor>multi-tool orchestration</Text>
			</Box>

			{/* ── Commands ── */}
			<Box marginTop={1} flexWrap="wrap">
				{COMMANDS.map((cmd, i) => (
					<React.Fragment key={cmd}>
						{i > 0 ? <Text color={theme.colors.muted}>{' · '}</Text> : null}
						<Text color={theme.colors.accent}>{cmd}</Text>
					</React.Fragment>
				))}
			</Box>

			{/* ── Keyboard shortcuts ── */}
			<Box flexWrap="wrap">
				{SHORTCUTS.map((item, i) => (
					<React.Fragment key={item.key}>
						{i > 0 ? <Text color={theme.colors.muted}>{'  ·  '}</Text> : null}
						<Text color={theme.colors.primary}>{item.key}</Text>
						<Text dimColor>{' '}{item.hint}</Text>
					</React.Fragment>
				))}
			</Box>
		</Box>
	);
}
