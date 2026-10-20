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

/** A small brand accent; keep it secondary to the content and actions. */
export function Badge({
  label,
  rotateDeg = 2,
  background = color.gold,
  textColor = color.ink,
  shadowOffset = 1,
  style,
}: BadgeProps) {
  return (
    <View
      accessible
      accessibilityRole="text"
      accessibilityLabel={label}
      style={[
        {
          transform: [{ rotate: `${rotateDeg}deg` }],
          backgroundColor: background,
          borderWidth: border.hairline,
          borderColor: color.ink,
          borderRadius: radius.pill,
          paddingHorizontal: space.md,
          paddingVertical: space.xs + 2,
          shadowColor: color.ink,
          shadowOffset: { width: shadowOffset, height: shadowOffset },
          shadowOpacity: 0.35,
          shadowRadius: 0,
        },
        style,
      ]}
    >
      <Text
        style={{
          fontFamily: font.body,
          fontSize: 12,
          color: textColor,
        }}
      >
        {label}
      </Text>
    </View>
  );
}
