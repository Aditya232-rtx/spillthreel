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

// Only actions that are connected to a working route or API are shown here.
// Persistence for preferences and a data-export endpoint are not available yet.
export const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    id: 'library',
    eyebrow: 'LIBRARY',
    rows: [
      { id: 'import', label: 'Import from Instagram', sub: 'Add older saves anytime', kind: 'chevron' },
    ],
  },
  {
    id: 'account',
    eyebrow: 'ACCOUNT',
    rows: [{ id: 'sign-out', label: 'Sign out', kind: 'chevron' }],
  },
  {
    id: 'danger',
    eyebrow: 'DANGER ZONE',
    rows: [{ id: 'delete-account', label: 'Delete account', labelColor: color.coral, kind: 'chevron' }],
  },
];
