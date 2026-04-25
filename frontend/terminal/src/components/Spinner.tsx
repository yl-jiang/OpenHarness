import React from 'react';
import {Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';

/**
 * Static "busy" indicator.
 *
 * Historically this used setInterval to animate spinner frames, but every tick
 * forced Ink to redraw the dynamic frame which in turn made most terminals
 * (Terminal.app, iTerm2, VS Code, Alacritty, …) snap the viewport back to the
 * cursor position — i.e. the bottom of the screen.  That made it impossible
 * to scroll back through history while the agent was busy.
 *
 * Keeping the indicator perfectly static means Ink's log-update layer skips
 * redraws entirely when nothing else changes, leaving the terminal scrollback
 * untouched so the user can browse history with the mouse wheel.  Liveness
 * is still conveyed via streaming transcript updates and the running tool's
 * label.
 */
export function Spinner({label}: {label?: string}): React.JSX.Element {
	const {theme} = useTheme();
	return (
		<Text>
			<Text color={theme.colors.primary}>●</Text>
			<Text dimColor> {label ?? 'Working...'}</Text>
		</Text>
	);
}
