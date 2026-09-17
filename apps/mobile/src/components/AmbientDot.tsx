import { View, type StyleProp, type ViewStyle } from 'react-native';

interface AmbientDotProps {
  size: number;
  backgroundColor: string;
  opacity: number;
  style?: StyleProp<ViewStyle>;
}

/** Purely decorative scattered accent dot used on Login/Signup backgrounds. */
export function AmbientDot({ size, backgroundColor, opacity, style }: AmbientDotProps) {
  return (
    <View
      style={[
        { position: 'absolute', width: size, height: size, borderRadius: size / 2, backgroundColor, opacity },
        style,
      ]}
    />
  );
}
