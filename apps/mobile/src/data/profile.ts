import { color } from '@/theme/tokens';

export interface ConnectedSource {
  id: string;
  name: string;
  status: string;
  bg: string;
}

export const CONNECTED_SOURCES: ConnectedSource[] = [
  { id: 'instagram', name: 'INSTAGRAM', status: 'CONNECTED · 210 REELS', bg: color.gold },
  { id: 'tiktok', name: 'TIKTOK', status: 'CONNECTED · 98 REELS', bg: color.gold },
  { id: 'youtube', name: 'YOUTUBE', status: 'NOT CONNECTED', bg: 'rgba(243,239,226,0.7)' },
  { id: 'x', name: 'X / TWITTER', status: 'NOT CONNECTED', bg: 'rgba(243,239,226,0.7)' },
];
