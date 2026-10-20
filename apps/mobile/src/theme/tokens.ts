/**
 * Design tokens for SpillTheReel's warm native library.
 * Keep primitive colors here and use semantic roles in components so text and
 * controls stay readable when surfaces or states change.
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

/** Semantic roles for the light library and the dark Ask surface. */
export const semantic = {
  canvas: color.cream,
  surface: color.white,
  surfaceRaised: color.warmCream,
  textPrimary: color.ink,
  textSecondary: '#55574F',
  textDisabled: '#74766F',
  border: '#777970',
  action: color.coral,
  onAction: color.ink,
  selected: '#F6D7CF',
  success: '#315B3A',
  warning: '#8A5700',
  danger: '#A12D22',
  focus: '#5B51C8',
  askCanvas: color.chatBg,
  askSurface: color.chatCard,
  askTextPrimary: color.cream,
  askTextSecondary: '#C2C2B8',
  askBorder: '#54564D',
  askAction: color.coral,
  askOnAction: color.ink,
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
  // Leave the family unset so iOS and Android use their native readable sans
  // (San Francisco and Roboto respectively) and respect platform font scaling.
  body: undefined,
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
