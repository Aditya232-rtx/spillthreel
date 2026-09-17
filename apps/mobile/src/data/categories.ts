import { categoryColors, color } from '@/theme/tokens';

export interface CategoryDef {
  id: string;
  name: string;
  count: number;
  bg: string;
  text: string;
  subText: string;
  icon: number;
  tags: string[];
}

export const CATEGORIES: CategoryDef[] = [
  {
    id: 'recipes',
    name: 'RECIPES',
    count: 48,
    bg: categoryColors.recipes,
    text: color.ink,
    subText: 'rgba(21,23,15,0.7)',
    icon: require('../../assets/images/icon-recipes.png'),
    tags: ['UNDER 15 MIN', 'VEGETARIAN', 'HIGH PROTEIN'],
  },
  {
    id: 'workouts',
    name: 'WORKOUTS',
    count: 31,
    bg: categoryColors.workouts,
    text: color.white,
    subText: 'rgba(255,255,255,0.85)',
    icon: require('../../assets/images/icon-workouts.png'),
    tags: ['NO EQUIPMENT', 'UNDER 20 MIN'],
  },
  {
    id: 'travel',
    name: 'TRAVEL',
    count: 19,
    bg: categoryColors.travel,
    text: color.white,
    subText: 'rgba(255,255,255,0.85)',
    icon: require('../../assets/images/icon-travel.png'),
    tags: ['BUDGET', 'SOLO'],
  },
  {
    id: 'reading',
    name: 'READING',
    count: 12,
    bg: categoryColors.reading,
    text: color.cream,
    subText: 'rgba(243,239,226,0.75)',
    icon: require('../../assets/images/icon-reading.png'),
    tags: ['NON-FICTION'],
  },
  {
    id: 'fashion',
    name: 'FASHION',
    count: 9,
    bg: categoryColors.fashion,
    text: color.ink,
    subText: 'rgba(21,23,15,0.7)',
    icon: require('../../assets/images/icon-fashion.png'),
    tags: ['STREETWEAR', 'MINIMAL'],
  },
];
