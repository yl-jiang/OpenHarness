import React, {useEffect, useRef, useState} from 'react';
import {Text} from 'ink';

const SHIMMER_SPEED = 70;
const GRADIENT_LEN = 8;

function hexToRgb(hex: string): [number, number, number] {
	const h = hex.replace('#', '');
	return [
		parseInt(h.substring(0, 2), 16),
		parseInt(h.substring(2, 4), 16),
		parseInt(h.substring(4, 6), 16),
	];
}

function toHex(r: number, g: number, b: number): string {
	return '#' + [r, g, b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
}

function lerpColor(a: string, b: string, t: number): string {
	const [r1, g1, b1] = hexToRgb(a);
	const [r2, g2, b2] = hexToRgb(b);
	return toHex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t);
}

function resolveNamedColor(name: string): string {
	const map: Record<string, string> = {
		yellow: '#e5c07b', cyan: '#56b6c2', blue: '#61afef',
		green: '#98c379', red: '#e06c75', magenta: '#c678dd',
		white: '#abb2bf', gray: '#5c6370',
	};
	return map[name.toLowerCase()] ?? name;
}

export function buildPulsePalette(baseColor: string, brightColor: string, steps: number): string[] {
	const base = baseColor.startsWith('#') ? baseColor : resolveNamedColor(baseColor);
	const bright = brightColor.startsWith('#') ? brightColor : resolveNamedColor(brightColor);
	const palette: string[] = [];
	const half = Math.max(1, Math.floor(steps / 2));
	for (let i = 0; i < steps; i++) {
		const t = i < half ? i / half : Math.max(0, 2 - i / half);
		palette.push(lerpColor(base, bright, t));
	}
	return palette;
}

function buildPalette(baseColor: string, brightColor: string): string[] {
	return buildPulsePalette(baseColor, brightColor, GRADIENT_LEN);
}

type ShimmerTextProps = {
	text: string;
	baseColor: string;
	brightColor: string;
	animate?: boolean;
};

export function ShimmerText({text, baseColor, brightColor, animate = true}: ShimmerTextProps): React.JSX.Element {
	const [offset, setOffset] = useState(0);
	const ref = useRef<NodeJS.Timeout | null>(null);

	useEffect(() => {
		if (animate && text.length > 0) {
			ref.current = setInterval(() => {
				setOffset(o => (o + 1) % GRADIENT_LEN);
			}, SHIMMER_SPEED);
		}
		return () => {
			if (ref.current) {
				clearInterval(ref.current);
				ref.current = null;
			}
		};
	}, [animate, text.length]);

	if (!animate || text.length === 0) {
		return <Text color={baseColor}>{text}</Text>;
	}

	const palette = buildPalette(baseColor, brightColor);
	const chars = [...text];

	return (
		<Text>
			{chars.map((char, i) => (
				<Text key={i} color={palette[(i + offset) % GRADIENT_LEN]}>{char}</Text>
			))}
		</Text>
	);
}
