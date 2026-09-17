import { Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { border, color, font, radius, space } from '@/theme/tokens';

interface BadgeProps {
  label: string;
  rotateDeg?: number;
  background?: string;
  textColor?: string;
  shadowOffset?: number;
  style?: StyleProp<ViewStyle>;
}

/** Rotated sticker-style badge pill used across Welcome/Login/Signup/Home. */
export function Badge({
  label,
  rotateDeg = 8,
  background = color.gold,
  textColor = color.ink,
  shadowOffset = 3,
  style,
}: BadgeProps) {
  return (
    <View
      style={[
        {
          transform: [{ rotate: `${rotateDeg}deg` }],
          backgroundColor: background,
          borderWidth: border.bold,
          borderColor: color.ink,
          borderRadius: radius.pill,
          paddingHorizontal: space.md,
          paddingVertical: space.xs + 2,
          shadowColor: color.ink,
          shadowOffset: { width: shadowOffset, height: shadowOffset },
          shadowOpacity: 1,
          shadowRadius: 0,
        },
        style,
      ]}
    >
      <Text
        style={{
          fontFamily: font.display,
          fontSize: 10.5,
          letterSpacing: 0.5,
          color: textColor,
        }}
      >
        {label}
      </Text>
    </View>
  );
}
