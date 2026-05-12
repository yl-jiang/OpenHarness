export type ThemeConfig = {
	name: string;
	colors: {
		primary: string;
		secondary: string;
		accent: string;
		foreground: string;
		background: string;
		/** Tinted background for the input area (used by HalfLinePaddedBox). */
		inputBackground: string;
		muted: string;
		success: string;
		warning: string;
		error: string;
		info: string;
	};
	icons: {
		spinner: string[];
		tool: string;
		assistant: string;
		user: string;
		system: string;
		success: string;
		error: string;
	};
};

export const defaultTheme: ThemeConfig = {
	name: 'default',
	colors: {
		primary: 'cyan',
		secondary: 'white',
		accent: 'cyan',
		foreground: 'white',
		background: 'black',
		inputBackground: '#333333',
		muted: 'gray',
		success: 'green',
		warning: 'yellow',
		error: 'red',
		info: 'blue',
	},
	icons: {
		spinner: ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],
		tool: '  ⏵ ',
		assistant: '⏺ ',
		user: '> ',
		system: 'ℹ ',
		success: '✓ ',
		error: '✗ ',
	},
};

export const darkTheme: ThemeConfig = {
	name: 'dark',
	colors: {
		primary: '#7aa2f7',
		secondary: '#c0caf5',
		accent: '#bb9af7',
		foreground: '#c0caf5',
		background: '#1a1b26',
		inputBackground: '#2a2b3d',
		muted: '#565f89',
		success: '#9ece6a',
		warning: '#e0af68',
		error: '#f7768e',
		info: '#7dcfff',
	},
	icons: {
		spinner: ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],
		tool: '  ⏵ ',
		assistant: '⏺ ',
		user: '> ',
		system: 'ℹ ',
		success: '✓ ',
		error: '✗ ',
	},
};

export const minimalTheme: ThemeConfig = {
	name: 'minimal',
	colors: {
		primary: 'white',
		secondary: 'white',
		accent: 'white',
		foreground: 'white',
		background: 'black',
		inputBackground: '#222222',
		muted: 'gray',
		success: 'white',
		warning: 'white',
		error: 'white',
		info: 'white',
	},
	icons: {
		spinner: ['-', '\\', '|', '/'],
		tool: '  > ',
		assistant: ': ',
		user: '> ',
		system: '# ',
		success: '+ ',
		error: '! ',
	},
};

export const cyberpunkTheme: ThemeConfig = {
	name: 'cyberpunk',
	colors: {
		primary: '#ff007c',
		secondary: '#00fff9',
		accent: '#ffe600',
		foreground: '#00fff9',
		background: '#0d0d0d',
		inputBackground: '#1a1a2e',
		muted: '#444444',
		success: '#00ff41',
		warning: '#ffe600',
		error: '#ff003c',
		info: '#00fff9',
	},
	icons: {
		spinner: ['◐', '◓', '◑', '◒'],
		tool: '  ▶ ',
		assistant: '◆ ',
		user: '▸ ',
		system: '⚡ ',
		success: '✦ ',
		error: '✖ ',
	},
};

export const solarizedTheme: ThemeConfig = {
	name: 'solarized',
	colors: {
		primary: '#268bd2',
		secondary: '#839496',
		accent: '#2aa198',
		foreground: '#839496',
		background: '#002b36',
		inputBackground: '#073642',
		muted: '#586e75',
		success: '#859900',
		warning: '#b58900',
		error: '#dc322f',
		info: '#268bd2',
	},
	icons: {
		spinner: ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'],
		tool: '  ⏵ ',
		assistant: '⏺ ',
		user: '> ',
		system: 'ℹ ',
		success: '✓ ',
		error: '✗ ',
	},
};

export const hermesTheme: ThemeConfig = {
	name: 'hermes',
	colors: {
		primary: '#ffe6cb',   // warm almond — canonical Hermes midground
		secondary: '#d4b896', // muted almond
		accent: '#ffbd38',    // gold — Hermes warmGlow base
		foreground: '#ffe6cb',
		background: '#041c1c', // deep dark teal — Hermes background
		inputBackground: '#0a2e2e', // slightly lighter teal for input
		muted: '#3d8a7c',      // mid teal
		success: '#5fba8a',
		warning: '#ffbd38',
		error: '#e0604a',
		info: '#5fc8c8',
	},
	icons: {
		spinner: ['◐', '◓', '◑', '◒'],
		tool: '  ◈ ',
		assistant: '◆ ',
		user: '▸ ',
		system: '◇ ',
		success: '◉ ',
		error: '✖ ',
	},
};

export const BUILTIN_THEMES: Record<string, ThemeConfig> = {
	default: defaultTheme,
	dark: darkTheme,
	minimal: minimalTheme,
	cyberpunk: cyberpunkTheme,
	solarized: solarizedTheme,
	hermes: hermesTheme,
};

export function getTheme(name: string): ThemeConfig {
	return BUILTIN_THEMES[name] ?? defaultTheme;
}
