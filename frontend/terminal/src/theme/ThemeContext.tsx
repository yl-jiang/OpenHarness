import React, {createContext, useCallback, useContext, useMemo, useState} from 'react';

import {type ThemeConfig, BUILTIN_THEMES, defaultTheme, getTheme} from './builtinThemes.js';

export type {ThemeConfig};

type ThemeContextValue = {
	theme: ThemeConfig;
	setThemeName: (name: string) => void;
};

const ThemeContext = createContext<ThemeContextValue>({
	theme: defaultTheme,
	setThemeName: () => undefined,
});

export function ThemeProvider({
	children,
	initialTheme = 'default',
}: {
	children: React.ReactNode;
	initialTheme?: string;
}): React.JSX.Element {
	const [theme, setTheme] = useState<ThemeConfig>(() => getTheme(initialTheme));

	const setThemeName = useCallback((name: string): void => {
		const resolved = BUILTIN_THEMES[name] ?? defaultTheme;
		setTheme(resolved);
	}, []);

	const value = useMemo(() => ({theme, setThemeName}), [theme, setThemeName]);

	return (
		<ThemeContext.Provider value={value}>
			{children}
		</ThemeContext.Provider>
	);
}

export function useTheme(): ThemeContextValue {
	return useContext(ThemeContext);
}
