import { Text, View } from 'react-native';
import type { User } from '@supabase/supabase-js';
import { getAvatarChoice, getAvatarPreset } from '@/lib/avatar';
import { color, font } from '@/theme/tokens';

interface AvatarViewProps {
  user: User | null;
  size: number;
  fallbackLetter: string;
  backgroundColor?: string;
}

/**
 * Circular avatar: the user's chosen preset SVG when set, otherwise the
 * initial letter on a solid fill. SVG is clipped by the rounded container
 * (overflow hidden) so square artwork reads as a round avatar.
 */
export function AvatarView({ user, size, fallbackLetter, backgroundColor }: AvatarViewProps) {
  const preset = getAvatarPreset(getAvatarChoice(user));

  return (
    <View
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        backgroundColor: preset ? color.cream : (backgroundColor ?? color.ink),
        borderWidth: 2,
        borderColor: color.ink,
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
      }}
    >
      {preset ? (
        <preset.Component width={size} height={size} />
      ) : (
        <Text
          style={{
            fontFamily: font.display,
            fontSize: size * 0.38,
            color: color.cream,
          }}
        >
          {fallbackLetter}
        </Text>
      )}
    </View>
  );
}
