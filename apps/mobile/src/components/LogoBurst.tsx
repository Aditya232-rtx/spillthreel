import { useEffect, useRef, useState } from 'react';
import { Animated, Easing, Image, View } from 'react-native';
import { Circle, Svg } from 'react-native-svg';
import { color } from '@/theme/tokens';

// Reference geometry is authored against a 200-unit viewBox (ring radius 42,
// dot radius 17); everything below is scaled by `size / REFERENCE_BOX`.
const REFERENCE_BOX = 200;
const RING_RADIUS = 42;
const DOT_R = 17;
const RING_ANGLES = [270, 330, 30, 90, 150, 210]; // top, then clockwise, matches reference hexagon

const POP_EASING = Easing.bezier(0.34, 1.56, 0.64, 1); // matches reference cubic-bezier overshoot
const COLLAPSE_EASING = Easing.in(Easing.quad);
const SETTLE_EASING = Easing.inOut(Easing.ease);

const RING_HOLD_MS = 1350;
const LOGO_SWAP_MS = 1550;
const GROUP_SETTLE_DELAY_MS = 2000;
const CONTENT_REVEAL_MS = 2450;

interface LogoBurstProps {
  size?: number;
  logoSource: number;
  onSettled?: () => void;
}

/**
 * The Welcome-screen centerpiece: a center dot pops in, bursts into a
 * hexagonal ring of 6 dots, holds, collapses back to center, then swaps to
 * the real logo mark, which settles to a smaller size. Timing mirrors the
 * reference prototype (see design handoff).
 *
 * The dots-to-logo swap is a plain React conditional render (not an
 * Animated-driven crossfade): react-native-web's Image renders its visual
 * background-image on an internal zIndex:-1 layer that fights any
 * Animated-opacity wrapper placed around it, making a smooth crossfade
 * unreliable across platforms. A hard swap at the same timestamp reads
 * identically at this dot size and duration, and is guaranteed to render.
 */
export function LogoBurst({ size = 180, logoSource, onSettled }: LogoBurstProps) {
  const centerDotOpacity = useRef(new Animated.Value(0)).current;
  const ringProgress = useRef(new Animated.Value(0)).current; // 0 = collapsed, 1 = burst out
  const groupScale = useRef(new Animated.Value(1)).current;
  const [showLogo, setShowLogo] = useState(false);

  const hasStarted = useRef(false);

  useEffect(() => {
    // Guard against React 19 Strict Mode's dev-only double effect-invoke:
    // without this, two overlapping Animated.timing chains race on the same
    // shared Animated.Value refs. Must run exactly once.
    if (hasStarted.current) return;
    hasStarted.current = true;

    Animated.sequence([
      Animated.timing(centerDotOpacity, { toValue: 1, duration: 300, easing: POP_EASING, useNativeDriver: false }),
      Animated.timing(centerDotOpacity, { toValue: 0, duration: 80, easing: Easing.linear, useNativeDriver: false }),
    ]).start();

    Animated.sequence([
      Animated.delay(300),
      Animated.timing(ringProgress, { toValue: 1, duration: 400, easing: POP_EASING, useNativeDriver: false }),
      Animated.delay(RING_HOLD_MS - 300 - 400),
      Animated.timing(ringProgress, { toValue: 0, duration: 350, easing: COLLAPSE_EASING, useNativeDriver: false }),
    ]).start();

    Animated.sequence([
      Animated.delay(GROUP_SETTLE_DELAY_MS),
      Animated.timing(groupScale, { toValue: 0.55, duration: 400, easing: SETTLE_EASING, useNativeDriver: false }),
    ]).start();

    const swapTimer = setTimeout(() => setShowLogo(true), LOGO_SWAP_MS);
    const settledTimer = setTimeout(() => onSettled?.(), CONTENT_REVEAL_MS);
    return () => {
      clearTimeout(swapTimer);
      clearTimeout(settledTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scale = size / REFERENCE_BOX;
  const dotR = DOT_R * scale;

  return (
    <Animated.View style={{ width: size, height: size, transform: [{ scale: groupScale }] }}>
      {!showLogo ? (
        <>
          <Animated.View
            style={{
              position: 'absolute',
              left: size / 2 - dotR,
              top: size / 2 - dotR,
              width: dotR * 2,
              height: dotR * 2,
              opacity: centerDotOpacity,
            }}
          >
            <Svg width={dotR * 2} height={dotR * 2}>
              <Circle cx={dotR} cy={dotR} r={dotR} fill={color.ink} />
            </Svg>
          </Animated.View>

          <View style={{ position: 'absolute', inset: 0, width: size, height: size }}>
            {RING_ANGLES.map((angle, i) => {
              const rad = (angle * Math.PI) / 180;
              const dx = RING_RADIUS * scale * Math.cos(rad);
              const dy = RING_RADIUS * scale * Math.sin(rad);
              return (
                <Animated.View
                  key={i}
                  style={{
                    position: 'absolute',
                    left: size / 2 - dotR,
                    top: size / 2 - dotR,
                    width: dotR * 2,
                    height: dotR * 2,
                    opacity: ringProgress,
                    transform: [
                      { translateX: ringProgress.interpolate({ inputRange: [0, 1], outputRange: [0, dx] }) },
                      { translateY: ringProgress.interpolate({ inputRange: [0, 1], outputRange: [0, dy] }) },
                      { scale: ringProgress },
                    ],
                  }}
                >
                  <Svg width={dotR * 2} height={dotR * 2}>
                    <Circle cx={dotR} cy={dotR} r={dotR} fill={color.ink} />
                  </Svg>
                </Animated.View>
              );
            })}
          </View>
        </>
      ) : (
        <Image source={logoSource} style={{ width: size, height: size }} resizeMode="contain" fadeDuration={0} />
      )}
    </Animated.View>
  );
}
