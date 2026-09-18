import { categoryColors, color } from '@/theme/tokens';

export type CategorySource = 'auto' | 'user';

export interface CategoryDef {
  id: string;
  name: string;
  count: number;
  bg: string;
  text: string;
  subText: string;
  /** PNG asset (require()) OR emoji string used as fallback icon */
  icon: number | null;
  emoji?: string;
  tags: string[];
  source: CategorySource;
}

// Seed data — counts and tag chips match the reference prototype
// (SpillTheReel.dc.html) so the header "112 REELS · N CATEGORIES" reads
// as designed. Marked source: 'auto' — these are the AI-inferred
// categories the ingestion pipeline generates during onboarding.
export const SEED_CATEGORIES: CategoryDef[] = [
  {
    id: 'recipes',
    name: 'RECIPES',
    count: 48,
    bg: categoryColors.recipes,
    text: color.ink,
    subText: 'rgba(21,23,15,0.65)',
    icon: require('../../assets/images/icon-recipes.png'),
    tags: ['UNDER 15 MIN', 'VEGETARIAN', 'HIGH PROTEIN'],
    source: 'auto',
  },
  {
    id: 'workouts',
    name: 'WORKOUTS',
    count: 22,
    bg: categoryColors.workouts,
    text: color.white,
    subText: 'rgba(255,255,255,0.8)',
    icon: require('../../assets/images/icon-workouts.png'),
    tags: ['NO EQUIPMENT', '15 MIN', 'STRENGTH'],
    source: 'auto',
  },
  {
    id: 'travel',
    name: 'TRAVEL',
    count: 16,
    bg: categoryColors.travel,
    text: color.white,
    subText: 'rgba(255,255,255,0.8)',
    icon: require('../../assets/images/icon-travel.png'),
    tags: ['BUDGET', 'SOLO', 'ASIA'],
    source: 'auto',
  },
  {
    id: 'reading',
    name: 'READING',
    count: 14,
    bg: categoryColors.reading,
    text: color.cream,
    subText: 'rgba(243,239,226,0.65)',
    icon: require('../../assets/images/icon-reading.png'),
    tags: ['FICTION', 'QUICK READ', 'AUDIO'],
    source: 'auto',
  },
  {
    id: 'fashion',
    name: 'FASHION',
    count: 12,
    bg: categoryColors.fashion,
    text: color.ink,
    subText: 'rgba(21,23,15,0.65)',
    icon: require('../../assets/images/icon-fashion.png'),
    tags: ['STREETWEAR', 'FALL', 'THRIFTED'],
    source: 'auto',
  },
];

// Palette rotation for user-created categories — cycles through the app's
// accent colors so newly-added categories visually belong with the seed
// set without letting the user pick anything off-brand.
export const USER_CATEGORY_PALETTE: Array<{ bg: string; text: string; subText: string }> = [
  { bg: color.gold, text: color.ink, subText: 'rgba(21,23,15,0.65)' },
  { bg: color.coral, text: color.white, subText: 'rgba(255,255,255,0.8)' },
  { bg: color.violet, text: color.white, subText: 'rgba(255,255,255,0.8)' },
  { bg: color.ink, text: color.cream, subText: 'rgba(243,239,226,0.65)' },
  { bg: color.sage, text: color.ink, subText: 'rgba(21,23,15,0.65)' },
];

// Emoji suggestion set surfaced in the "add category" modal — kept as a
// short curated list so users don't get lost picking from the full unicode
// range. Order roughly matches category-type frequency in early user data.
export const USER_CATEGORY_EMOJI_CHOICES = [
  '⭐', '🎨', '🎵', '📚', '🎬', '💡', '🌱', '🏠', '🐶', '☕',
  '💰', '⚽', '✈️', '🎮', '📸', '🍔',
];
