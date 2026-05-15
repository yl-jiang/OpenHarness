export type VimInputMode = 'insert' | 'normal';

export type VimKey = {
	ctrl?: boolean;
	meta?: boolean;
	leftArrow?: boolean;
	rightArrow?: boolean;
	upArrow?: boolean;
	downArrow?: boolean;
	delete?: boolean;
	backspace?: boolean;
};

export type VimBindings<State> = {
	moveLeft: (state: State) => State;
	moveRight: (state: State) => State;
	moveHome: (state: State) => State;
	moveEnd: (state: State) => State;
	movePrevWord: (state: State) => State;
	moveNextWord: (state: State) => State;
	deleteChar: (state: State) => State;
	moveUp?: (state: State) => State;
	moveDown?: (state: State) => State;
	openLineBelow?: (state: State) => State;
	openLineAbove?: (state: State) => State;
};

export function toChars(value: string): string[] {
	return [...value];
}

function isWordChar(char: string): boolean {
	return /[\w]/u.test(char);
}

export function prevWordBoundary(chars: string[], offset: number): number {
	let index = offset - 1;
	while (index > 0 && !isWordChar(chars[index]!)) {
		index -= 1;
	}
	while (index > 0 && isWordChar(chars[index - 1]!)) {
		index -= 1;
	}
	return Math.max(0, index);
}

export function nextWordBoundary(chars: string[], offset: number): number {
	let index = offset;
	while (index < chars.length && isWordChar(chars[index]!)) {
		index += 1;
	}
	while (index < chars.length && !isWordChar(chars[index]!)) {
		index += 1;
	}
	return index;
}

export function applyVimNormalMode<State>(
	state: State,
	input: string,
	key: VimKey,
	bindings: VimBindings<State>,
): {handled: boolean; state: State; mode: VimInputMode} {
	const handled = (nextState: State, mode: VimInputMode = 'normal') => ({
		handled: true,
		state: nextState,
		mode,
	});

	if (key.ctrl || key.meta) {
		return {handled: false, state, mode: 'normal'};
	}
	if (key.leftArrow || input === 'h') {
		return handled(bindings.moveLeft(state));
	}
	if (key.rightArrow || input === 'l') {
		return handled(bindings.moveRight(state));
	}
	if (key.upArrow || input === 'k') {
		return bindings.moveUp ? handled(bindings.moveUp(state)) : handled(state);
	}
	if (key.downArrow || input === 'j') {
		return bindings.moveDown ? handled(bindings.moveDown(state)) : handled(state);
	}
	if (input === '0') {
		return handled(bindings.moveHome(state));
	}
	if (input === '$') {
		return handled(bindings.moveEnd(state));
	}
	if (input === 'b') {
		return handled(bindings.movePrevWord(state));
	}
	if (input === 'w') {
		return handled(bindings.moveNextWord(state));
	}
	if (input === 'x' || key.delete) {
		return handled(bindings.deleteChar(state));
	}
	if (input === 'i') {
		return handled(state, 'insert');
	}
	if (input === 'a') {
		return handled(bindings.moveRight(state), 'insert');
	}
	if (input === 'I') {
		return handled(bindings.moveHome(state), 'insert');
	}
	if (input === 'A') {
		return handled(bindings.moveEnd(state), 'insert');
	}
	if (input === 'o') {
		return bindings.openLineBelow ? handled(bindings.openLineBelow(state), 'insert') : handled(state);
	}
	if (input === 'O') {
		return bindings.openLineAbove ? handled(bindings.openLineAbove(state), 'insert') : handled(state);
	}
	if (input.length > 0 || key.backspace) {
		return handled(state);
	}
	return {handled: false, state, mode: 'normal'};
}
