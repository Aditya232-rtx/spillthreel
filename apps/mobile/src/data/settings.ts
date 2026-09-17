import { color } from '@/theme/tokens';

export type SettingsRowKind = 'chevron' | 'chip' | 'toggle';

export interface SettingsRow {
  id: string;
  label: string;
  sub?: string;
  labelColor?: string;
  kind: SettingsRowKind;
  chipLabel?: string;
  chipBg?: string;
  chipText?: string;
}

export interface SettingsGroup {
  id: string;
  eyebrow: string;
  rows: SettingsRow[];
}

export const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    id: 'library',
    eyebrow: 'LIBRARY',
    rows: [
      { id: 'import', label: 'Import from Instagram', sub: 'Add older saves anytime', kind: 'chevron' },
      { id: 'quality', label: 'Quality', kind: 'chip', chipLabel: 'FAST', chipBg: color.gold, chipText: color.ink },
      { id: 'auto-delete', label: 'Auto-delete stale items', kind: 'toggle' },
    ],
  },
  {
    id: 'notifications',
    eyebrow: 'NOTIFICATIONS',
    rows: [
      { id: 'save-ready', label: 'Save ready to search', kind: 'toggle' },
      { id: 'weekly-digest', label: 'Weekly digest', kind: 'toggle' },
      { id: 'failed-saves', label: 'Failed saves', kind: 'toggle' },
    ],
  },
  {
    id: 'account',
    eyebrow: 'ACCOUNT',
    rows: [
      { id: 'data-export', label: 'Data export', sub: 'Download everything as JSON', kind: 'chevron' },
      { id: 'sign-out', label: 'Sign out', kind: 'chevron' },
    ],
  },
  {
    id: 'danger',
    eyebrow: 'DANGER ZONE',
    rows: [{ id: 'delete-account', label: 'Delete account', labelColor: color.coral, kind: 'chevron' }],
  },
];

export const DEFAULT_TOGGLE_STATE: Record<string, boolean> = {
  'auto-delete': false,
  'save-ready': true,
  'weekly-digest': false,
  'failed-saves': true,
};
