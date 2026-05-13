import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';

const MARK: ReadonlyArray<string> = [
	'╭────╮ ╷   ╷',
	'│╭──╮│ │   │',
	'││  ││ ├───┤',
	'│╰──╯│ │   │',
	'╰────╯ ╵   ╵',
];

export function WelcomeBanner({version}: {version?: string | null}): React.JSX.Element {
	const {theme} = useTheme();

	return (
		<Box
			flexDirection="row"
			alignItems="flex-start"
			marginTop={1}
			marginBottom={1}
			paddingLeft={1}
		>
			<Box flexDirection="column" marginRight={2}>
				{MARK.map((row, i) => (
					<Text key={i} color={i === 2 ? theme.colors.accent : theme.colors.muted}>
						{row}
					</Text>
				))}
			</Box>

			<Box flexDirection="column">
				<Box>
					<Text bold color={theme.colors.primary}>OpenHarness</Text>
					{version ? <Text color={theme.colors.muted}>{'  '}v{version}</Text> : null}
				</Box>
				<Text dimColor>autonomous coding agent</Text>
				<Text color={theme.colors.muted}>plans · tools · skills · memory</Text>
				<Box marginTop={1}>
					<Text color={theme.colors.accent}>/help</Text>
					<Text color={theme.colors.muted}>{'  ·  '}</Text>
					<Text color={theme.colors.primary}>@ files</Text>
					<Text color={theme.colors.muted}>{'  ·  '}</Text>
					<Text color={theme.colors.primary}>Esc Esc</Text>
					<Text dimColor>{' clear'}</Text>
				</Box>
			</Box>
		</Box>
	);
}
