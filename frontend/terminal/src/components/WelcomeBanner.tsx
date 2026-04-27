import React from 'react';
import {Box, Text} from 'ink';

import {useTerminalSize} from '../hooks/useTerminalSize.js';

const VERSION = '0.1.6';

// Hermes-inspired palette — fixed brand identity for the welcome screen
const H_WARM = '#ffe6cb'; // warm almond — ASCII art, foreground text
const H_GOLD = '#ffbd38'; // gold — border, version badge, accents
const H_TEAL = '#3d8a7c'; // dim teal — labels, dividers, muted elements

// smslant font · OPENHARNESS · 4 rows × 55 cols
// prettier-ignore
const LOGO: ReadonlyArray<string> = [
'  ____  ___  _____  ____ _____   ___  _  ______________',
' / __ \\/ _ \\/ __/ |/ / // / _ | / _ \\/ |/ / __/ __/ __/',
'/ /_/ / ___/ _//    / _  / __ |/ , _/    / _/_\\ \\_\\ \\  ',
'\\____/_/  /___/_/|_/_//_/_/ |_/_/|_/_/|_/___/___/___/',
];

const COMMANDS: ReadonlyArray<string> = [
'/help', '/model', '/theme', '/provider', '/resume', '/effort', '/turns',
];

const SHORTCUTS: ReadonlyArray<{key: string; hint: string}> = [
{key: 'Ctrl+C', hint: 'exit'},
{key: 'Ctrl+T', hint: 'todos'},
{key: 'PgUp/Dn', hint: 'scroll'},
{key: 'Esc Esc', hint: 'clear'},
];

export function WelcomeBanner(): React.JSX.Element {
const {cols} = useTerminalSize();

const dividerLen = Math.min(Math.max(cols - 10, 36), 72);
const divider = '╌'.repeat(dividerLen);

return (
<Box
borderStyle="double"
borderColor={H_GOLD}
paddingX={2}
marginBottom={1}
flexDirection="column"
>
{/* ── ASCII logo rows 0-2 ── */}
{LOGO.slice(0, 3).map((line, i) => (
<Text key={i} color={H_WARM} bold>{line}</Text>
))}
{/* ── Last logo row + version pinned right ── */}
<Box flexDirection="row">
<Box flexGrow={1}>
<Text color={H_WARM} bold>{LOGO[3]}</Text>
</Box>
<Text color={H_GOLD} dimColor>v{VERSION}{'  '}</Text>
</Box>

{/* ── Tagline ── */}
<Box marginTop={1}>
<Text dimColor>{'  '}autonomous coding agent</Text>
<Text color={H_TEAL}>{' · '}</Text>
<Text dimColor>streaming runtime</Text>
<Text color={H_TEAL}>{' · '}</Text>
<Text dimColor>multi-tool orchestration</Text>
</Box>

{/* ── Commands ── */}
<Box marginTop={1}>
<Text color={H_TEAL} dimColor>{divider}</Text>
</Box>
<Box flexWrap="wrap">
<Text color={H_TEAL}>{'CMD  '}</Text>
{COMMANDS.map((cmd, i) => (
<React.Fragment key={cmd}>
{i > 0 ? <Text color={H_TEAL}>{' · '}</Text> : null}
<Text color={H_WARM}>{cmd}</Text>
</React.Fragment>
))}
</Box>

{/* ── Shortcuts ── */}
<Box>
<Text color={H_TEAL} dimColor>{divider}</Text>
</Box>
<Box flexWrap="wrap">
<Text color={H_TEAL}>{'KEY  '}</Text>
{SHORTCUTS.map((item, i) => (
<React.Fragment key={item.key}>
{i > 0 ? <Text color={H_TEAL}>{'  ·  '}</Text> : null}
<Text color={H_GOLD}>{item.key}</Text>
<Text dimColor>{' '}{item.hint}</Text>
</React.Fragment>
))}
</Box>
</Box>
);
}
