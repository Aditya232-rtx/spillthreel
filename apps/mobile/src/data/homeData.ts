import { color } from '@/theme/tokens';

export interface ChartBar {
  color: string;
  heightPercent: number;
}

export const CHART_BARS: ChartBar[] = [
  { color: 'rgba(255,255,255,0.45)', heightPercent: 40 },
  { color: 'rgba(255,255,255,0.6)', heightPercent: 65 },
  { color: 'rgba(255,255,255,0.45)', heightPercent: 30 },
  { color: '#FFFFFF', heightPercent: 90 },
  { color: 'rgba(255,255,255,0.6)', heightPercent: 50 },
  { color: 'rgba(255,255,255,0.7)', heightPercent: 75 },
  { color: 'rgba(255,255,255,0.4)', heightPercent: 20 },
];

export interface RecentThumb {
  id: string;
  bg: string;
}

export const RECENT_THUMBS: RecentThumb[] = [
  { id: '1', bg: color.gold },
  { id: '2', bg: color.coral },
  { id: '3', bg: color.violet },
  { id: '4', bg: '#AEBFA9' },
];

export interface CollectionRail {
  id: string;
  name: string;
  count: string;
  bg: string;
  text: string;
  subText: string;
  badge?: string;
}

export const COLLECTION_RAILS: CollectionRail[] = [
  { id: 'recipes', name: 'RECIPES', count: '48 reels', bg: color.gold, text: color.ink, subText: 'rgba(21,23,15,0.75)' },
  {
    id: 'travel',
    name: 'TRAVEL PLACES',
    count: '22 reels',
    bg: color.violet,
    text: color.white,
    subText: 'rgba(255,255,255,0.85)',
    badge: 'NEW',
  },
];
