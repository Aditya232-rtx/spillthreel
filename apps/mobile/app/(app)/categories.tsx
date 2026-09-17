import { useState } from 'react';
import { Image, Pressable, ScrollView, Text, View } from 'react-native';
import { Shadowed } from '@/components/Shadowed';
import { CATEGORIES } from '@/data/categories';
import { border, color, font, fontSize, radius, space } from '@/theme/tokens';

/**
 * v1 implementation: a vertical card list with tap-to-expand tag chips.
 * The reference design specifies an infinitely-looping "dial" carousel
 * (cards on a virtual circle, curvature/foreshortening as they scroll away
 * from center) — a genuinely complex custom gesture+physics interaction
 * that deserves its own dedicated build pass rather than an approximation
 * bolted on here. This list preserves the exact card visual design and the
 * expand-on-tap interaction; the dial motion is a follow-up.
 */
export default function CategoriesScreen() {
  const [sourceFilterAll, setSourceFilterAll] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>('recipes');

  const totalReels = CATEGORIES.reduce((sum, c) => sum + c.count, 0);

  return (
    <View style={{ flex: 1, backgroundColor: color.cream, paddingHorizontal: 18, paddingTop: 54 }}>
      <Text
        style={{
          fontFamily: font.display,
          fontSize: fontSize.displayLg,
          letterSpacing: -0.6,
          color: color.ink,
          textTransform: 'uppercase',
        }}
      >
        CATEGORIES
      </Text>

      <View style={{ height: 64, marginTop: 6, position: 'relative' }}>
        <Pressable
          onPress={() => setSourceFilterAll((v) => !v)}
          style={{
            position: 'absolute',
            top: 6,
            left: 0,
            transform: [{ rotate: sourceFilterAll ? '-3deg' : '4deg' }],
            backgroundColor: color.coral,
            borderWidth: border.standard,
            borderColor: color.ink,
            borderRadius: radius.pill,
            paddingHorizontal: space.lg,
            paddingVertical: 9,
          }}
        >
          <Text style={{ fontFamily: font.display, fontSize: 12, letterSpacing: -0.2, color: color.white }}>
            {sourceFilterAll ? 'INSTAGRAM' : 'ALL SOURCES'}
          </Text>
        </Pressable>
        <Pressable
          onPress={() => setSourceFilterAll((v) => !v)}
          style={{
            position: 'absolute',
            top: 0,
            left: 14,
            transform: [{ rotate: sourceFilterAll ? '2deg' : '-4deg' }],
            backgroundColor: color.ink,
            borderWidth: border.standard,
            borderColor: color.ink,
            borderRadius: radius.pill,
            paddingHorizontal: space.lg,
            paddingVertical: 9,
          }}
        >
          <Text style={{ fontFamily: font.display, fontSize: 12, letterSpacing: -0.2, color: color.cream }}>
            {sourceFilterAll ? 'ALL SOURCES' : 'INSTAGRAM'}
          </Text>
        </Pressable>
      </View>

      <Text style={{ fontFamily: font.body, fontSize: fontSize.mono, letterSpacing: 0.6, color: 'rgba(21,23,15,0.55)', marginTop: 2, marginBottom: space.lg }}>
        {totalReels} REELS · {CATEGORIES.length} CATEGORIES
      </Text>

      <ScrollView contentContainerStyle={{ gap: space.md, paddingBottom: 140 }} showsVerticalScrollIndicator={false}>
        {CATEGORIES.map((cat) => {
          const expanded = expandedId === cat.id;
          return (
            <Shadowed key={cat.id} offset={5} radius={radius.xl}>
              <Pressable
                onPress={() => setExpandedId(expanded ? null : cat.id)}
                style={{
                  backgroundColor: cat.bg,
                  borderWidth: border.bold,
                  borderColor: color.ink,
                  borderRadius: radius.xl,
                  padding: space.lg - 2,
                }}
              >
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
                  <View style={{ flex: 1, minWidth: 0, gap: 7 }}>
                    <Text style={{ fontFamily: font.display, fontSize: 24, lineHeight: 25, letterSpacing: -0.5, color: cat.text, textTransform: 'uppercase' }}>
                      {cat.name}
                    </Text>
                    <Text style={{ fontFamily: font.body, fontSize: 10, letterSpacing: 0.4, color: cat.subText }}>
                      {cat.count} REELS · AUTO-SORTED
                    </Text>
                  </View>
                  <Image source={cat.icon} style={{ width: 70, height: 70 }} resizeMode="contain" />
                </View>

                {expanded ? (
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm, marginTop: space.md }}>
                    {cat.tags.map((tag) => (
                      <View
                        key={tag}
                        style={{
                          backgroundColor: 'rgba(21,23,15,0.85)',
                          borderRadius: radius.pill,
                          paddingHorizontal: 10,
                          paddingVertical: 5,
                        }}
                      >
                        <Text style={{ fontFamily: font.body, fontSize: 10, color: color.cream }}>{tag}</Text>
                      </View>
                    ))}
                  </View>
                ) : null}
              </Pressable>
            </Shadowed>
          );
        })}
      </ScrollView>
    </View>
  );
}
