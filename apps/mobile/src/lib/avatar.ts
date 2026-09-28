/**
 * Avatar presets — bundled Sketchvalley SVGs in assets/avatars/.
 *
 * The user's choice is stored as a plain id string in
 * user_metadata.avatar (synced) plus a per-email AsyncStorage override
 * (same last-choice-wins guard as display names — see display-name.ts).
 * Resolution order: explicit choice → provider avatar_url → initial.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { User } from '@supabase/supabase-js';
import type { FC } from 'react';
import type { SvgProps } from 'react-native-svg';

import Avatar01 from '../../assets/avatars/avatar-01.svg';
import Avatar02 from '../../assets/avatars/avatar-02.svg';
import Avatar03 from '../../assets/avatars/avatar-03.svg';
import Avatar05 from '../../assets/avatars/avatar-05.svg';
import Avatar06 from '../../assets/avatars/avatar-06.svg';
import Avatar07 from '../../assets/avatars/avatar-07.svg';
import Avatar08 from '../../assets/avatars/avatar-08.svg';
import Avatar09 from '../../assets/avatars/avatar-09.svg';
import Avatar10 from '../../assets/avatars/avatar-10.svg';
import Avatar11 from '../../assets/avatars/avatar-11.svg';
import Avatar12 from '../../assets/avatars/avatar-12.svg';
import Avatar13 from '../../assets/avatars/avatar-13.svg';
import Avatar14 from '../../assets/avatars/avatar-14.svg';
import Avatar15 from '../../assets/avatars/avatar-15.svg';
import Avatar16 from '../../assets/avatars/avatar-16.svg';
import Avatar17 from '../../assets/avatars/avatar-17.svg';
import Avatar18 from '../../assets/avatars/avatar-18.svg';
import Avatar19 from '../../assets/avatars/avatar-19.svg';
import Avatar20 from '../../assets/avatars/avatar-20.svg';
import Avatar21 from '../../assets/avatars/avatar-21.svg';
import Avatar22 from '../../assets/avatars/avatar-22.svg';
import Avatar23 from '../../assets/avatars/avatar-23.svg';
import Avatar24 from '../../assets/avatars/avatar-24.svg';
import Avatar25 from '../../assets/avatars/avatar-25.svg';
import Avatar26 from '../../assets/avatars/avatar-26.svg';
import Avatar27 from '../../assets/avatars/avatar-27.svg';
import Avatar28 from '../../assets/avatars/avatar-28.svg';
import Avatar29 from '../../assets/avatars/avatar-29.svg';
import Avatar30 from '../../assets/avatars/avatar-30.svg';
import Avatar31 from '../../assets/avatars/avatar-31.svg';
import Avatar32 from '../../assets/avatars/avatar-32.svg';
import Avatar33 from '../../assets/avatars/avatar-33.svg';
import Avatar34 from '../../assets/avatars/avatar-34.svg';
import Avatar35 from '../../assets/avatars/avatar-35.svg';
import Avatar36 from '../../assets/avatars/avatar-36.svg';
import Avatar37 from '../../assets/avatars/avatar-37.svg';
import Avatar38 from '../../assets/avatars/avatar-38.svg';
import Avatar39 from '../../assets/avatars/avatar-39.svg';
import Avatar40 from '../../assets/avatars/avatar-40.svg';
import Avatar41 from '../../assets/avatars/avatar-41.svg';
import Avatar42 from '../../assets/avatars/avatar-42.svg';
import Avatar43 from '../../assets/avatars/avatar-43.svg';
import Avatar44 from '../../assets/avatars/avatar-44.svg';
import Avatar45 from '../../assets/avatars/avatar-45.svg';

export interface AvatarPreset {
  id: string;
  Component: FC<SvgProps>;
}

const PRESETS: AvatarPreset[] = [
  { id: 'avatar-01', Component: Avatar01 },
  { id: 'avatar-02', Component: Avatar02 },
  { id: 'avatar-03', Component: Avatar03 },
  { id: 'avatar-05', Component: Avatar05 },
  { id: 'avatar-06', Component: Avatar06 },
  { id: 'avatar-07', Component: Avatar07 },
  { id: 'avatar-08', Component: Avatar08 },
  { id: 'avatar-09', Component: Avatar09 },
  { id: 'avatar-10', Component: Avatar10 },
  { id: 'avatar-11', Component: Avatar11 },
  { id: 'avatar-12', Component: Avatar12 },
  { id: 'avatar-13', Component: Avatar13 },
  { id: 'avatar-14', Component: Avatar14 },
  { id: 'avatar-15', Component: Avatar15 },
  { id: 'avatar-16', Component: Avatar16 },
  { id: 'avatar-17', Component: Avatar17 },
  { id: 'avatar-18', Component: Avatar18 },
  { id: 'avatar-19', Component: Avatar19 },
  { id: 'avatar-20', Component: Avatar20 },
  { id: 'avatar-21', Component: Avatar21 },
  { id: 'avatar-22', Component: Avatar22 },
  { id: 'avatar-23', Component: Avatar23 },
  { id: 'avatar-24', Component: Avatar24 },
  { id: 'avatar-25', Component: Avatar25 },
  { id: 'avatar-26', Component: Avatar26 },
  { id: 'avatar-27', Component: Avatar27 },
  { id: 'avatar-28', Component: Avatar28 },
  { id: 'avatar-29', Component: Avatar29 },
  { id: 'avatar-30', Component: Avatar30 },
  { id: 'avatar-31', Component: Avatar31 },
  { id: 'avatar-32', Component: Avatar32 },
  { id: 'avatar-33', Component: Avatar33 },
  { id: 'avatar-34', Component: Avatar34 },
  { id: 'avatar-35', Component: Avatar35 },
  { id: 'avatar-36', Component: Avatar36 },
  { id: 'avatar-37', Component: Avatar37 },
  { id: 'avatar-38', Component: Avatar38 },
  { id: 'avatar-39', Component: Avatar39 },
  { id: 'avatar-40', Component: Avatar40 },
  { id: 'avatar-41', Component: Avatar41 },
  { id: 'avatar-42', Component: Avatar42 },
  { id: 'avatar-43', Component: Avatar43 },
  { id: 'avatar-44', Component: Avatar44 },
  { id: 'avatar-45', Component: Avatar45 },
];

export const AVATAR_PRESETS: AvatarPreset[] = PRESETS;

export function getAvatarPreset(id: string | null | undefined): AvatarPreset | null {
  if (!id) return null;
  return PRESETS.find((p) => p.id === id) ?? null;
}

/** The user's chosen preset id, if any. */
export function getAvatarChoice(user: User | null): string | null {
  const raw = user?.user_metadata?.avatar;
  return typeof raw === 'string' && getAvatarPreset(raw) ? raw : null;
}

const OVERRIDE_PREFIX = 'spillthereel.avatar.';

export async function rememberAvatarChoice(email: string, id: string): Promise<void> {
  try {
    await AsyncStorage.setItem(OVERRIDE_PREFIX + email.toLowerCase(), id);
  } catch {
    // non-fatal
  }
}

export async function getRememberedAvatarChoice(email: string): Promise<string | null> {
  try {
    const id = await AsyncStorage.getItem(OVERRIDE_PREFIX + email.toLowerCase());
    return id && getAvatarPreset(id) ? id : null;
  } catch {
    return null;
  }
}
