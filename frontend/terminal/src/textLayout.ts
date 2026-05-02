import stringWidth from 'string-width';

export function truncateWithEllipsis(value: string, maxWidth: number): string {
	if (maxWidth <= 0) {
		return '';
	}
	if (stringWidth(value) <= maxWidth) {
		return value;
	}
	const ellipsis = '...';
	const targetWidth = Math.max(0, maxWidth - stringWidth(ellipsis));
	let output = '';
	for (const char of value) {
		if (stringWidth(output) + stringWidth(char) > targetWidth) {
			break;
		}
		output += char;
	}
	return output.trimEnd() + ellipsis;
}
