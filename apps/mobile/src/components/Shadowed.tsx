import { View, type ViewStyle, type StyleProp } from 'react-native';
import { color } from '@/theme/tokens';

interface ShadowedProps {
  children: React.ReactNode;
  offset?: number;
  shadowColor?: string;
  radius?: number;
  style?: StyleProp<ViewStyle>;
}

/**
 * Renders a hard-edged offset "sticker" shadow by stacking a solid backplate
 * behind the content, offset by `offset` px — not a native shadow.
 * Native shadowOffset/elevation cannot produce a blur-free hard edge on
 * Android, so this is the only cross-platform way to match the design spec.
 */
export function Shadowed({
  children,
  offset = 4,
  shadowColor = color.ink,
  radius = 22,
  style,
}: ShadowedProps) {
  return (
    <View style={style}>
      <View
        style={{
          position: 'absolute',
          top: offset,
          left: offset,
          right: -offset,
          bottom: -offset,
          backgroundColor: shadowColor,
          borderRadius: radius,
        }}
      />
      <View style={{ borderRadius: radius }}>{children}</View>
    </View>
  );
}
