import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, {
  type SharedValue,
  useAnimatedStyle,
  useSharedValue,
  withDecay,
} from 'react-native-reanimated';
import { border, color, font } from '@/theme/tokens';
import type { CategoryDef } from '@/data/categories';

// Physics constants ported from the reference dial (SpillTheReel.dc.html):
// 30 fixed slots cycling through the category list, laid out on a virtual
// circle of `RADIUS` px so only a shallow ~64deg arc (2 * CULL) is ever
// visible — reads as a rotating rolodex rather than a flat scroll list.
const SLOT_COUNT = 30;
const PITCH = 150; // "virtual px" of drag distance between adjacent slots
const STEP_RAD = (12 * Math.PI) / 180; // angular spacing per slot
const RADIUS = 900;
const CULL_DEG = 32;
const BASE_OFFSET = SLOT_COUNT * PITCH * 100; // arbitrary large start so early drags never approach 0

const CARD_HEIGHT = 104;
const DIAL_HEIGHT = 460;
const FADE_HEIGHT = 70;

interface DialCardGeometry {
  hiddenOpacity: number;
  translateY: number;
  rotateDeg: number;
  scale: number;
  zIndex: number;
}

function computeGeometry(offset: number, slotIndex: number): DialCardGeometry {
  'worklet';
  const total = SLOT_COUNT * PITCH;
  let a = (slotIndex * PITCH - offset) % total;
  a = ((a % total) + total) % total;
  if (a > total / 2) a -= total;
  const theta = (a / PITCH) * STEP_RAD;
  const deg = (theta * 180) / Math.PI;
  const absDeg = Math.abs(deg);
  const translateY = RADIUS * Math.sin(theta);
  const scale = 1 - absDeg / 110;
  const opacity = absDeg > CULL_DEG ? 0 : Math.max(0, 1 - absDeg / 34);
  const zIndex = 100 - Math.round(absDeg);
  return { hiddenOpacity: opacity, translateY, rotateDeg: deg, scale, zIndex };
}

interface DialCardProps {
  slotIndex: number;
  category: CategoryDef;
  offset: SharedValue<number>;
  onPress: (id: string) => void;
}

function DialCard({ slotIndex, category, offset, onPress }: DialCardProps) {
  const animatedStyle = useAnimatedStyle(() => {
    const g = computeGeometry(offset.value, slotIndex);
    return {
      opacity: g.hiddenOpacity,
      zIndex: g.zIndex,
      transform: [{ translateY: g.translateY }, { rotate: `${g.rotateDeg}deg` }, { scale: g.scale }],
    };
  });

  return (
    <Animated.View style={[styles.card, { backgroundColor: category.bg }, animatedStyle]}>
      <Pressable style={styles.cardPressable} onPress={() => onPress(category.id)}>
        <View style={{ flex: 1, minWidth: 0, gap: 7 }}>
          <Text style={[styles.cardName, { color: category.text }]}>{category.name}</Text>
          <Text style={[styles.cardCount, { color: category.subText }]}>{category.count} REELS · AUTO-SORTED</Text>
        </View>
        <Image source={category.icon} style={styles.cardIcon} resizeMode="contain" />
      </Pressable>
    </Animated.View>
  );
}

interface CategoryDialProps {
  categories: CategoryDef[];
  onSelectCategory: (id: string) => void;
}

/**
 * The signature "categories dial": an infinitely-looping vertical carousel
 * of category cards laid out on a virtual circle. Drag to spin; momentum
 * carries it via Reanimated's withDecay. Ported from the reference's DOM
 * scroll-offset + imperative transform math (SpillTheReel.dc.html) onto
 * a single gesture-driven shared value — the per-card worklet already
 * wraps via modulo for any offset value, so unlike the DOM version there's
 * no need to periodically re-center a bounded scroll container.
 */
export function CategoryDial({ categories, onSelectCategory }: CategoryDialProps) {
  const offset = useSharedValue(BASE_OFFSET);
  const dragStartOffset = useSharedValue(0);

  const pan = Gesture.Pan()
    .activeOffsetY([-8, 8])
    .onStart(() => {
      dragStartOffset.value = offset.value;
    })
    .onUpdate((e) => {
      offset.value = dragStartOffset.value - e.translationY;
    })
    .onEnd((e) => {
      offset.value = withDecay({ velocity: -e.velocityY, deceleration: 0.997 });
    });

  const slots = Array.from({ length: SLOT_COUNT }, (_, i) => ({
    slotIndex: i,
    category: categories[i % categories.length],
  }));

  return (
    <View style={styles.container}>
      <GestureDetector gesture={pan}>
        <View style={styles.viewport}>
          {slots.map(({ slotIndex, category }) => (
            <DialCard
              key={slotIndex}
              slotIndex={slotIndex}
              category={category}
              offset={offset}
              onPress={onSelectCategory}
            />
          ))}
        </View>
      </GestureDetector>

      <LinearGradient
        colors={[color.sage, `${color.sage}00`]}
        style={[styles.fade, { top: 0, height: FADE_HEIGHT }]}
        pointerEvents="none"
      />
      <LinearGradient
        colors={[`${color.sage}00`, color.sage]}
        style={[styles.fade, { bottom: 0, height: FADE_HEIGHT }]}
        pointerEvents="none"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    height: DIAL_HEIGHT,
    position: 'relative',
  },
  viewport: {
    flex: 1,
    overflow: 'hidden',
  },
  fade: {
    position: 'absolute',
    left: 0,
    right: 0,
  },
  card: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: '50%',
    marginTop: -CARD_HEIGHT / 2,
    height: CARD_HEIGHT,
    borderRadius: 24,
    borderWidth: border.bold,
    borderColor: color.ink,
  },
  cardPressable: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    paddingHorizontal: 16,
  },
  cardName: {
    fontFamily: font.display,
    fontSize: 24,
    lineHeight: 25,
    letterSpacing: -0.5,
    textTransform: 'uppercase',
  },
  cardCount: {
    fontFamily: font.body,
    fontSize: 10,
    letterSpacing: 0.4,
  },
  cardIcon: {
    width: 70,
    height: 70,
    flexShrink: 0,
  },
});
