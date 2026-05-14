import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

export const SUMMARIZED_PASTE_MIN_LINES = 10;
export const TEMP_FILE_PROMPT_MIN_LINES = 101;

export type PasteReference =
	| {
		label: string;
		lineCount: number;
		storage: 'memory';
		content: string;
	}
	| {
		label: string;
		lineCount: number;
		storage: 'temp_file';
		tempFilePath: string;
	};

export type DetectedPasteInsertion = {
	pastedText: string;
	lineCount: number;
	insertStart: number;
	replaceEnd: number;
};

function countPasteLines(text: string): number {
	if (!text) {
		return 0;
	}
	return text.split('\n').length;
}

function readPasteReference(reference: PasteReference, readTempFile: (filePath: string) => string): string {
	if (reference.storage === 'memory') {
		return reference.content;
	}
	return readTempFile(reference.tempFilePath);
}

export function buildPastePlaceholder(pasteNumber: number, lineCount: number): string {
	return `[Paste #${pasteNumber} - ${lineCount} lines]`;
}

export function detectSummarizablePasteInsertion(
	previous: string,
	next: string,
): DetectedPasteInsertion | null {
	if (next === previous || next.length <= previous.length) {
		return null;
	}

	let prefixLength = 0;
	while (
		prefixLength < previous.length &&
		prefixLength < next.length &&
		previous[prefixLength] === next[prefixLength]
	) {
		prefixLength += 1;
	}

	let previousSuffixIndex = previous.length;
	let nextSuffixIndex = next.length;
	while (
		previousSuffixIndex > prefixLength &&
		nextSuffixIndex > prefixLength &&
		previous[previousSuffixIndex - 1] === next[nextSuffixIndex - 1]
	) {
		previousSuffixIndex -= 1;
		nextSuffixIndex -= 1;
	}

	const pastedText = next.slice(prefixLength, nextSuffixIndex);
	const lineCount = countPasteLines(pastedText);
	if (pastedText.length <= 1 || lineCount < SUMMARIZED_PASTE_MIN_LINES) {
		return null;
	}

	return {
		pastedText,
		lineCount,
		insertStart: prefixLength,
		replaceEnd: previousSuffixIndex,
	};
}

export function applyPastePlaceholder(
	previous: string,
	detected: DetectedPasteInsertion,
	placeholder: string,
): string {
	return (
		previous.slice(0, detected.insertStart) +
		placeholder +
		previous.slice(detected.replaceEnd)
	);
}

export function prunePasteReferences(
	text: string,
	references: Record<string, PasteReference>,
): Record<string, PasteReference> {
	const nextReferences: Record<string, PasteReference> = {};
	for (const [label, reference] of Object.entries(references)) {
		if (text.includes(label)) {
			nextReferences[label] = reference;
		}
	}
	return nextReferences;
}

export function listPasteStageNotices(
	text: string,
	references: Record<string, PasteReference>,
): string[] {
	const notices: string[] = [];
	for (const reference of Object.values(references)) {
		if (reference.storage === 'temp_file' && text.includes(reference.label)) {
			notices.push(`${reference.label} saved to ${reference.tempFilePath}`);
		}
	}
	return notices;
}

export function resolvePasteSubmission(
	text: string,
	references: Record<string, PasteReference>,
	readTempFile: (filePath: string) => string = (filePath) =>
		fs.readFileSync(filePath, 'utf8'),
): {line: string; transcriptLine?: string} {
	let resolved = text;
	let changed = false;
	for (const [label, reference] of Object.entries(references)) {
		if (!resolved.includes(label)) {
			continue;
		}
		resolved = resolved.split(label).join(readPasteReference(reference, readTempFile));
		changed = true;
	}
	return changed ? {line: resolved, transcriptLine: text} : {line: text};
}

export function stagePasteInTempFile({
	label,
	lineCount,
	content,
	baseDir,
}: {
	label: string;
	lineCount: number;
	content: string;
	baseDir?: string;
}): PasteReference {
	const targetDir = baseDir ?? fs.mkdtempSync(path.join(os.tmpdir(), 'openharness-paste-'));
	fs.mkdirSync(targetDir, {recursive: true});
	const tempFilePath = path.join(targetDir, `paste-${randomUUID()}.txt`);
	fs.writeFileSync(tempFilePath, content, 'utf8');
	return {
		label,
		lineCount,
		storage: 'temp_file',
		tempFilePath,
	};
}
