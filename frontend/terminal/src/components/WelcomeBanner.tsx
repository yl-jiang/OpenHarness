import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';

// "OH" pixel mark — 5 rows × 7 cols, Unicode quadrant + half-block chars
//   O: smooth oval (▛▀▜ / ▌ ▐ / ▙▄▟ for curved corners)
//   H: two pillars with crossbar centered at row 2 of 5
const ICON: ReadonlyArray<string> = [
	'▛▀▜ ▌ ▐',
	'▌ ▐ ▌ ▐',
	'▌ ▐ ███',
	'▌ ▐ ▌ ▐',
	'▙▄▟ ▌ ▐',
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
			{/* compact icon */}
			<Box flexDirection="column" marginRight={2}>
				{ICON.map((row, i) => (
					<Text key={i} color={theme.colors.accent}>{row}</Text>
				))}
			</Box>

			{/* name · version · tagline · shortcuts */}
			<Box flexDirection="column">
				<Box>
					<Text bold color={theme.colors.primary}>OpenHarness</Text>
					{version ? <Text color={theme.colors.muted}>{'  '}v{version}</Text> : null}
				</Box>
				<Text dimColor>autonomous agent</Text>
				<Box marginTop={1}>
					<Text color={theme.colors.accent}>/help</Text>
					<Text color={theme.colors.muted}>{'  ·  '}</Text>
					<Text color={theme.colors.primary}>Ctrl+C</Text>
					<Text dimColor>{' exit'}</Text>
					<Text color={theme.colors.muted}>{'  ·  '}</Text>
					<Text color={theme.colors.primary}>Esc Esc</Text>
					<Text dimColor>{' clear'}</Text>
				</Box>
			</Box>
		</Box>
	);
}
