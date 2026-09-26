import { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { Svg, Polyline } from 'react-native-svg';
import { Shadowed } from '@/components/Shadowed';
import { CONNECTED_SOURCES } from '@/data/profile';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { useAuth } from '@/hooks/useAuth';
import { getFirstName, getFullName, rememberDisplayName } from '@/lib/display-name';
import { supabase } from '@/lib/supabase';
import { alpha, border, color, font, radius, space } from '@/theme/tokens';

function joinedLabel(createdAt: string | undefined): string {
  if (!createdAt) return '';
  const d = new Date(createdAt);
  if (Number.isNaN(d.getTime())) return '';
  return ` · joined ${d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' }).toLowerCase()}`;
}

export default function ProfileScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const fullName = getFullName(user) ?? getFirstName(user);
  const initial = (fullName[0] ?? 'S').toUpperCase();
  const handle = user?.email ? `@${user.email.split('@')[0]}` : '@you';

  const [editingName, setEditingName] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [saving, setSaving] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);

  function beginEditName(): void {
    setDraftName(fullName === 'friend' ? '' : fullName);
    setNameError(null);
    setEditingName(true);
  }

  async function saveName(): Promise<void> {
    const trimmed = draftName.trim();
    if (!trimmed) {
      setNameError('Name cannot be empty');
      return;
    }
    setSaving(true);
    setNameError(null);
    const { error } = await supabase.auth.updateUser({ data: { full_name: trimmed } });
    if (error) {
      setSaving(false);
      setNameError(error.message);
      return;
    }
    if (user?.email) {
      await rememberDisplayName(user.email, trimmed);
    }
    setSaving(false);
    // Session (and every screen reading it) refreshes via onAuthStateChange.
    setEditingName(false);
  }

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: color.cream }}
      contentContainerStyle={{ paddingHorizontal: space.xl, paddingTop: 54, paddingBottom: 130, gap: space.xl }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        {/* Frosted-glass circle: same recipe as the floating dock
            (translucent white + light border + soft shadow). True backdrop
            blur would need expo-blur; translucency is the app's glass
            language everywhere else. Ink glyph for contrast on light glass. */}
        <Pressable
          onPress={() => router.replace('/(app)/home')}
          accessibilityLabel="Back to home"
          hitSlop={8}
          style={{
            width: 40,
            height: 40,
            borderRadius: radius.pill,
            backgroundColor: alpha(color.white, 0.5),
            borderWidth: 1.5,
            borderColor: alpha(color.white, 0.7),
            alignItems: 'center',
            justifyContent: 'center',
            shadowColor: color.ink,
            shadowOffset: { width: 0, height: 4 },
            shadowOpacity: 0.12,
            shadowRadius: 12,
            elevation: 4,
          }}
        >
          <Svg width={19} height={19} viewBox="0 0 24 24">
            <Polyline points="16.5,1.5 3.5,12 16.5,22.5" fill="none" stroke={color.ink} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
          </Svg>
        </Pressable>
        <Text style={{ fontFamily: font.display, fontSize: 16, letterSpacing: 0.4, color: color.ink }}>PROFILE</Text>
        <View style={{ width: 38 }} />
      </View>

      <View style={{ alignItems: 'center', gap: 6 }}>
        <Shadowed offset={4} shadowColor={color.gold} radius={radius.pill}>
          <View style={{ width: 92, height: 92, borderRadius: radius.pill, backgroundColor: color.violet, borderWidth: border.heavy, borderColor: color.ink, alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ fontFamily: font.display, fontSize: 34, color: color.cream }}>{initial}</Text>
          </View>
        </Shadowed>
        {editingName ? (
          <View style={{ width: '100%', gap: space.sm, marginTop: space.sm }}>
            <TextField
              placeholder="Your name"
              value={draftName}
              onChangeText={(text) => {
                setDraftName(text);
                setNameError(null);
              }}
              autoCapitalize="words"
              autoFocus
            />
            {nameError ? (
              <Text style={{ fontFamily: font.body, fontSize: 12, color: color.coral, textAlign: 'center' }}>
                {nameError}
              </Text>
            ) : null}
            <View style={{ flexDirection: 'row', gap: space.sm }}>
              <View style={{ flex: 1 }}>
                <PillButton
                  label="Cancel"
                  variant="outline"
                  minHeight={44}
                  fontSize={13}
                  disabled={saving}
                  onPress={() => setEditingName(false)}
                />
              </View>
              <View style={{ flex: 1 }}>
                <PillButton
                  label={saving ? 'Saving…' : 'Save'}
                  variant="coral"
                  minHeight={44}
                  fontSize={13}
                  disabled={saving}
                  onPress={saveName}
                />
              </View>
            </View>
          </View>
        ) : (
          <>
            <Pressable onPress={beginEditName} accessibilityLabel="Edit display name">
              <Text style={{ fontFamily: font.display, fontSize: 22, color: color.ink, marginTop: space.sm, textTransform: 'uppercase', textAlign: 'center' }}>{fullName}</Text>
            </Pressable>
            <Text
              onPress={beginEditName}
              style={{ fontFamily: font.body, fontSize: 10.5, color: color.coral, textDecorationLine: 'underline' }}
            >
              edit name
            </Text>
          </>
        )}
        <Text style={{ fontFamily: font.body, fontSize: 10.5, color: 'rgba(21,23,15,0.6)' }}>{handle}{joinedLabel(user?.created_at)}</Text>
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
