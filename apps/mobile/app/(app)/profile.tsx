import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { Shadowed } from '@/components/Shadowed';
import { CONNECTED_SOURCES } from '@/data/profile';
import { border, color, font, radius, space } from '@/theme/tokens';

export default function ProfileScreen() {
  const router = useRouter();

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: color.cream }}
      contentContainerStyle={{ paddingHorizontal: space.xl, paddingTop: 54, paddingBottom: 130, gap: space.xl }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Pressable
          onPress={() => router.replace('/(app)/home')}
          style={{ width: 38, height: 38, borderRadius: radius.pill, backgroundColor: color.ink, alignItems: 'center', justifyContent: 'center' }}
        >
          <Text style={{ color: color.cream, fontSize: 18 }}>←</Text>
        </Pressable>
        <Text style={{ fontFamily: font.display, fontSize: 16, letterSpacing: 0.4, color: color.ink }}>PROFILE</Text>
        <View style={{ width: 38 }} />
      </View>

      <View style={{ alignItems: 'center', gap: 6 }}>
        <Shadowed offset={4} shadowColor={color.gold} radius={radius.pill}>
          <View style={{ width: 92, height: 92, borderRadius: radius.pill, backgroundColor: color.violet, borderWidth: border.heavy, borderColor: color.ink }} />
        </Shadowed>
        <Text style={{ fontFamily: font.display, fontSize: 22, color: color.ink, marginTop: space.sm }}>ADITYA JADHAV</Text>
        <Text style={{ fontFamily: font.body, fontSize: 10.5, color: 'rgba(21,23,15,0.6)' }}>@can.adityaa · joined jun 2026</Text>
      </View>

      <Shadowed offset={4} radius={radius.md}>
        <View style={{ flexDirection: 'row', borderWidth: border.bold, borderColor: color.ink, borderRadius: radius.md, paddingVertical: space.lg, backgroundColor: color.white }}>
          <View style={{ flex: 1, alignItems: 'center' }}>
            <Text style={{ fontFamily: font.display, fontSize: 22, color: color.ink }}>342</Text>
            <Text style={{ fontFamily: font.body, fontSize: 10.5, color: 'rgba(21,23,15,0.6)' }}>REELS</Text>
          </View>
          <View style={{ width: 2, backgroundColor: 'rgba(21,23,15,0.2)' }} />
          <View style={{ flex: 1, alignItems: 'center' }}>
            <Text style={{ fontFamily: font.display, fontSize: 22, color: color.ink }}>6</Text>
            <Text style={{ fontFamily: font.body, fontSize: 10.5, color: 'rgba(21,23,15,0.6)' }}>CATEGORIES</Text>
          </View>
          <View style={{ width: 2, backgroundColor: 'rgba(21,23,15,0.2)' }} />
          <View style={{ flex: 1, alignItems: 'center' }}>
            <Text style={{ fontFamily: font.display, fontSize: 22, color: color.coral }}>18</Text>
            <Text style={{ fontFamily: font.body, fontSize: 10.5, color: 'rgba(21,23,15,0.6)' }}>DAY STREAK</Text>
          </View>
        </View>
      </Shadowed>

      <Text style={{ fontFamily: font.body, fontSize: 10.5, letterSpacing: 0.6, color: 'rgba(21,23,15,0.55)' }}>CONNECTED SOURCES</Text>

      <View style={{ gap: space.sm }}>
        {CONNECTED_SOURCES.map((src) => (
          <View
            key={src.id}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'space-between',
              backgroundColor: src.bg,
              borderWidth: border.standard,
              borderColor: color.ink,
              borderRadius: radius.sm,
              paddingHorizontal: space.lg,
              paddingVertical: space.md + 2,
            }}
          >
            <Text style={{ fontFamily: font.display, fontSize: 15, color: color.ink }}>{src.name}</Text>
            <Text style={{ fontFamily: font.body, fontSize: 10.5, color: 'rgba(21,23,15,0.75)' }}>{src.status}</Text>
          </View>
        ))}
      </View>

      <Text
        onPress={() => router.push('/(import)/import2')}
        style={{ alignSelf: 'center', fontFamily: font.body, fontSize: 12.5, color: color.ink, textDecorationLine: 'underline' }}
      >
        Import more from Instagram
      </Text>
    </ScrollView>
  );
}
