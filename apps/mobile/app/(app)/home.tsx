import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { Svg, Circle, Line } from 'react-native-svg';
import { Shadowed } from '@/components/Shadowed';
import { CHART_BARS, COLLECTION_RAILS, RECENT_THUMBS } from '@/data/homeData';
import { border, color, font, fontSize, radius, space } from '@/theme/tokens';

export default function HomeScreen() {
  const router = useRouter();

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: color.cream }}
      contentContainerStyle={{ paddingHorizontal: space.xl, paddingTop: 54, paddingBottom: 130, gap: space.lg }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displayMd,
            letterSpacing: -0.5,
            color: color.ink,
            textTransform: 'uppercase',
          }}
        >
          HEY,{'\n'}ADITYA.
        </Text>
        <Pressable onPress={() => router.push('/(app)/profile')}>
          <Shadowed offset={3} shadowColor={color.coral} radius={radius.pill}>
            <View
              style={{
                width: 44,
                height: 44,
                borderRadius: radius.pill,
                backgroundColor: color.ink,
                borderWidth: border.bold,
                borderColor: color.ink,
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Text style={{ fontFamily: font.display, fontSize: 17, color: color.cream }}>A</Text>
            </View>
          </Shadowed>
        </Pressable>
      </View>

      <Pressable
        onPress={() => router.push('/(app)/chat')}
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: space.sm,
          backgroundColor: color.ink,
          borderWidth: border.bold,
          borderColor: color.ink,
          borderRadius: radius.pill,
          paddingLeft: space.xl,
          paddingRight: space.sm,
          minHeight: 52,
        }}
      >
        <Text style={{ flex: 1, fontFamily: font.displaySemibold, fontSize: fontSize.bodyMd, color: 'rgba(243,239,226,0.65)' }}>
          lets spill the reel....
        </Text>
        <View style={{ width: 36, height: 36, borderRadius: radius.pill, backgroundColor: color.coral, alignItems: 'center', justifyContent: 'center' }}>
          <Svg width={15} height={15} viewBox="0 0 16 16">
            <Circle cx={7} cy={7} r={5} fill="none" stroke={color.white} strokeWidth={2.2} />
            <Line x1={11} y1={11} x2={15} y2={15} stroke={color.white} strokeWidth={2.2} strokeLinecap="round" />
          </Svg>
        </View>
      </Pressable>

      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Shadowed offset={5} radius={radius.lg} style={{ flex: 1 }}>
          <View
            style={{
              backgroundColor: color.coral,
              borderWidth: border.bold,
              borderColor: color.ink,
              borderRadius: radius.lg,
              padding: space.lg,
              height: 104,
              justifyContent: 'space-between',
            }}
          >
            <Text
              style={{
                alignSelf: 'flex-end',
                transform: [{ rotate: '-4deg' }],
                fontFamily: font.display,
                fontSize: 10,
                color: color.coral,
                backgroundColor: color.cream,
                borderWidth: 1.5,
                borderColor: color.ink,
                borderRadius: radius.pill,
                paddingHorizontal: 8,
                paddingVertical: 3,
              }}
            >
              +18
            </Text>
            <View>
              <Text style={{ fontFamily: font.display, fontSize: 38, lineHeight: 38, color: color.white }}>342</Text>
              <Text style={{ fontFamily: font.body, fontSize: fontSize.mono, letterSpacing: 0.6, color: 'rgba(255,255,255,0.9)', marginTop: 4 }}>
                REELS SAVED
              </Text>
            </View>
          </View>
        </Shadowed>

        <Shadowed offset={5} radius={radius.lg} style={{ flex: 1 }}>
          <View
            style={{
              backgroundColor: color.gold,
              borderWidth: border.bold,
              borderColor: color.ink,
              borderRadius: radius.lg,
              padding: space.lg,
              height: 104,
              justifyContent: 'space-between',
            }}
          >
            <Text
              style={{
                alignSelf: 'flex-end',
                fontFamily: font.body,
                fontSize: 10,
                color: 'rgba(21,23,15,0.6)',
                backgroundColor: 'rgba(21,23,15,0.12)',
                borderRadius: radius.pill,
                paddingHorizontal: 8,
                paddingVertical: 3,
              }}
            >
              wk
            </Text>
            <View>
              <Text style={{ fontFamily: font.display, fontSize: 38, lineHeight: 38, color: color.ink }}>6</Text>
              <Text style={{ fontFamily: font.body, fontSize: fontSize.mono, letterSpacing: 0.6, color: 'rgba(21,23,15,0.75)', marginTop: 4 }}>
                SAVED THIS WK
              </Text>
            </View>
          </View>
        </Shadowed>
      </View>

      <Shadowed offset={5} radius={radius.xl}>
        <View style={{ backgroundColor: color.violet, borderWidth: border.bold, borderColor: color.ink, borderRadius: radius.xl, padding: space.lg + 2 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: space.lg }}>
            <Text style={{ fontFamily: font.display, fontSize: 17, letterSpacing: -0.3, color: color.white }}>RECALL ACTIVITY</Text>
            <Text
              style={{
                fontFamily: font.display,
                fontSize: 11,
                color: color.ink,
                backgroundColor: color.cream,
                borderRadius: radius.pill,
                paddingHorizontal: space.md,
                paddingVertical: 5,
              }}
            >
              THIS WEEK ⌄
            </Text>
          </View>
          <View style={{ flexDirection: 'row', gap: 7, alignItems: 'flex-end', height: 60 }}>
            {CHART_BARS.map((bar, i) => (
              <View key={i} style={{ flex: 1, borderTopLeftRadius: 5, borderTopRightRadius: 5, backgroundColor: bar.color, height: `${bar.heightPercent}%` }} />
            ))}
          </View>
        </View>
      </Shadowed>

      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <Text style={{ fontFamily: font.display, fontSize: 18, letterSpacing: -0.3, color: color.ink }}>RECENTLY SAVED</Text>
        <Text style={{ fontFamily: font.display, fontSize: 12, color: color.ink, textDecorationLine: 'underline' }}>See all</Text>
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm }}>
        {RECENT_THUMBS.map((thumb) => (
          <View key={thumb.id} style={{ width: 72, height: 96, borderRadius: radius.md, backgroundColor: thumb.bg, borderWidth: border.standard, borderColor: color.ink }}>
            <View
              style={{
                position: 'absolute',
                bottom: 8,
                left: 8,
                width: 22,
                height: 22,
                borderRadius: radius.pill,
                backgroundColor: 'rgba(21,23,15,0.6)',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Text style={{ color: color.white, fontSize: 9 }}>▶</Text>
            </View>
          </View>
        ))}
      </ScrollView>

      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <Text style={{ fontFamily: font.display, fontSize: 18, letterSpacing: -0.3, color: color.ink }}>YOUR COLLECTIONS</Text>
        <Text style={{ fontFamily: font.body, fontSize: fontSize.mono, backgroundColor: color.ink, color: color.cream, borderRadius: radius.pill, paddingHorizontal: 9, paddingVertical: 3 }}>
          {COLLECTION_RAILS.length}
        </Text>
      </View>

      {COLLECTION_RAILS.map((rail) => (
        <Shadowed key={rail.id} offset={4} radius={radius.xxl}>
          <View
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              backgroundColor: rail.bg,
              borderWidth: border.bold,
              borderColor: color.ink,
              borderRadius: radius.xxl,
              paddingHorizontal: space.xl - 2,
              paddingVertical: space.lg,
            }}
          >
            {rail.badge ? (
              <Text
                style={{
                  position: 'absolute',
                  top: -10,
                  right: 16,
                  transform: [{ rotate: '5deg' }],
                  backgroundColor: color.violet,
                  borderWidth: border.standard,
                  borderColor: color.ink,
                  borderRadius: radius.pill,
                  paddingHorizontal: 10,
                  paddingVertical: 3,
                  fontFamily: font.display,
                  fontSize: 9,
                  color: color.white,
                }}
              >
                {rail.badge}
              </Text>
            ) : null}
            <View>
              <Text style={{ fontFamily: font.display, fontSize: 24, letterSpacing: -0.5, color: rail.text, textTransform: 'uppercase' }}>{rail.name}</Text>
              <Text style={{ fontFamily: font.body, fontSize: fontSize.mono, color: rail.subText, marginTop: 2 }}>{rail.count}</Text>
            </View>
            <View style={{ width: 36, height: 36, borderRadius: radius.pill, backgroundColor: color.ink, alignItems: 'center', justifyContent: 'center' }}>
              <Text style={{ fontSize: 15, color: color.cream }}>↗</Text>
            </View>
          </View>
        </Shadowed>
      ))}
    </ScrollView>
  );
}
