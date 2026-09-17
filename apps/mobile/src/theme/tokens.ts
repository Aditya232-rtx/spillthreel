/**
 * Design tokens for SpillTheReel — "kitsch-minimal" visual system.
 * Source of truth: /docs (root) design_handoff_spillthereel/README.md + SpillTheReel.dc.html
 * Do not hand-roll hex values or spacing anywhere else in the app — import from here.
 */

export const color = {
  ink: '#15170F',
  cream: '#F3EFE2',
  warmCream: '#EDEAE0',
  coral: '#E85C3F',
  gold: '#E8AC3D',
  violet: '#6A67E0',
  sage: '#C7D3C3',
  white: '#FFFFFF',
  chatBg: '#0F110C',
  chatCard: '#1B1D15',
} as const;

export const alpha = (hex: string, a: number): string => {
  const bigint = parseInt(hex.replace('#', ''), 16);
  const r = (bigint >> 16) & 255;
  const g = (bigint >> 8) & 255;
  const b = bigint & 255;
  return `rgba(${r}, ${g}, ${b}, ${a})`;
};

export const font = {
  display: 'Oswald_700Bold',
  displaySemibold: 'Oswald_600SemiBold',
  displayMedium: 'Oswald_500Medium',
  body: 'Oswald_500Medium',
  script: 'MyLove',
} as const;

export const fontSize = {
  displayXl: 46,
  displayLg: 40,
  displayMd: 30,
  displaySm: 22,
  displayXs: 18,
  bodyLg: 16,
  bodyMd: 14,
  bodySm: 12.5,
  bodyXs: 11,
  mono: 10.5,
  monoXs: 9.5,
} as const;

export const radius = {
  sm: 14,
  md: 18,
  lg: 22,
  xl: 26,
  xxl: 32,
  pill: 999,
} as const;

export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 24,
  xxxl: 32,
} as const;

export const border = {
  hairline: 1.5,
  standard: 2,
  bold: 2.5,
  heavy: 3,
} as const;

// Hard offset "sticker" shadows are rendered via the <Shadowed> component
// (src/components/Shadowed.tsx), not native shadow props — Android's
// elevation always blurs, so a stacked-backplate view is the only way to
// get a true blur-free offset edge on both platforms.

export const categoryColors = {
  recipes: color.gold,
  workouts: color.coral,
  travel: color.violet,
  reading: color.ink,
  fashion: color.sage,
} as const;
