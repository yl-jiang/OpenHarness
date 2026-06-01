export function getQuestionDisplayText(question: string, choices: readonly string[] | null): string {
	if (!choices || choices.length === 0) {
		return question;
	}

	const lines = question.split(/\r?\n/);
	let end = lines.length;
	while (end > 0 && lines[end - 1]!.trim() === '') {
		end--;
	}
	if (end < choices.length) {
		return question;
	}

	const start = end - choices.length;
	let markedChoiceLines = 0;
	for (let i = 0; i < choices.length; i++) {
		const parsed = parseChoiceLine(lines[start + i]!);
		if (parsed.marked) {
			markedChoiceLines++;
		}
		if (normalizeChoice(parsed.text) !== normalizeChoice(choices[i]!)) {
			return question;
		}
	}
	if (markedChoiceLines === 0) {
		return question;
	}

	const displayLines = lines.slice(0, start);
	while (displayLines.length > 0 && displayLines[displayLines.length - 1]!.trim() === '') {
		displayLines.pop();
	}
	const displayQuestion = displayLines.join('\n').trimEnd();
	if (!displayQuestion) {
		return question;
	}
	return displayQuestion;
}

function parseChoiceLine(line: string): {text: string; marked: boolean} {
	const match = line.trim().match(/^(?:[-*+]|\u2022|\d+[.)])\s+(.+)$/u);
	if (!match) {
		return {text: line, marked: false};
	}
	return {text: match[1]!, marked: true};
}

function normalizeChoice(value: string): string {
	return value.trim().replace(/\s+/g, ' ');
}
