import { Pressable, Text, type StyleProp, type ViewStyle } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useSharedValue,
  withTiming,
} from 'react-native-reanimated';
import { Shadowed } from './Shadowed';
import { border, color, font, fontSize, radius, space } from '@/theme/tokens';

export type PillButtonVariant = 'ink' | 'coral' | 'outline';

interface PillButtonProps {
  label: string;
  onPress: () => void;
  variant?: PillButtonVariant;
  shadowColor?: string;
  minHeight?: number;
  fontSize?: number;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
}

const VARIANT_STYLES: Record<
  PillButtonVariant,
  { background: string; textColor: string; borderColor: string; borderWidth: number }
> = {
  ink: { background: color.ink, textColor: color.cream, borderColor: color.ink, borderWidth: border.bold },
  coral: { background: color.coral, textColor: color.white, borderColor: color.ink, borderWidth: border.standard },
  outline: { background: color.cream, textColor: color.ink, borderColor: color.ink, borderWidth: border.hairline },
};

const PRESS_TRANSLATE = 3;
const PRESS_DURATION = 90;

/**
 * The signature CTA: solid pill, thick border, hard offset shadow that
 * shrinks toward the button on press — "pressing into its own shadow".
 */
export function PillButton({
  label,
  onPress,
  variant = 'ink',
  shadowColor,
  minHeight = 54,
  fontSize: labelSize = fontSize.bodyLg,
  disabled = false,
  style,
}: PillButtonProps) {
  const { background, textColor, borderColor, borderWidth } = VARIANT_STYLES[variant];
  const resolvedShadowColor = shadowColor ?? (variant === 'coral' ? color.ink : color.gold);
  const pressed = useSharedValue(0);

  const contentStyle = useAnimatedStyle(() => ({
    transform: [
      { translateX: withTiming(pressed.value * PRESS_TRANSLATE, { duration: PRESS_DURATION }) },
      { translateY: withTiming(pressed.value * PRESS_TRANSLATE, { duration: PRESS_DURATION }) },
    ],
  }));

  return (
    <Pressable
      disabled={disabled}
      onPressIn={() => {
        pressed.value = 1;
      }}
      onPressOut={() => {
        pressed.value = 0;
      }}
      onPress={onPress}
      style={[{ opacity: disabled ? 0.5 : 1 }, style]}
    >
      <Shadowed offset={4} shadowColor={resolvedShadowColor} radius={radius.pill}>
        <Animated.View
          style={[
            {
              minHeight,
              borderRadius: radius.pill,
              borderWidth,
              borderColor,
              backgroundColor: background,
              alignItems: 'center',
              justifyContent: 'center',
              paddingHorizontal: space.xl,
            },
            contentStyle,
          ]}
        >
          <Text
            style={{
              fontFamily: font.display,
              fontSize: labelSize,
              letterSpacing: -0.3,
              textTransform: 'uppercase',
              color: textColor,
            }}
          >
            {label}
          </Text>
        </Animated.View>
      </Shadowed>
    </Pressable>
  );
}
