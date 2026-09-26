import { useState } from 'react';
import { Image, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { AmbientDot } from '@/components/AmbientDot';
import { Badge } from '@/components/Badge';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { useAuth } from '@/hooks/useAuth';
import { getFullName, rememberDisplayName } from '@/lib/display-name';
import { supabase } from '@/lib/supabase';
import { color, font, fontSize, space } from '@/theme/tokens';

const LOGO_SOURCE = require('../../assets/images/logo-mark-transparent.png');

/**
 * Post-OAuth name step (signup path only). OAuth providers don't always
 * hand us a usable display name, so we ask once — prefilled when the
 * provider gave us one — and store it in user_metadata before import.
 */
export default function YourNameScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [name, setName] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const value = name ?? getFullName(user) ?? '';

  async function handleContinue(skip = false): Promise<void> {
    const trimmed = value.trim();
    if (!skip && !trimmed) {
      setErrorMessage('Tell us your name, or skip for now');
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    if (!skip && trimmed) {
      const { error } = await supabase.auth.updateUser({ data: { full_name: trimmed } });
      if (error) {
        setSubmitting(false);
        setErrorMessage(error.message);
        return;
      }
      if (user?.email) {
        await rememberDisplayName(user.email, trimmed);
      }
      setSubmitting(false);
    } else {
      setSubmitting(false);
    }
    router.replace('/(import)/import1');
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      <AmbientDot size={14} backgroundColor={color.violet} opacity={0.35} style={{ top: 80, right: 28 }} />
      <AmbientDot size={10} backgroundColor={color.gold} opacity={0.35} style={{ top: 170, left: 30 }} />
      <AmbientDot size={12} backgroundColor={color.coral} opacity={0.3} style={{ bottom: 100, right: 34 }} />

      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          alignItems: 'center',
          justifyContent: 'center',
          paddingHorizontal: space.xxl,
          paddingVertical: 44,
          gap: space.md,
        }}
      >
        <Badge label="ALMOST THERE ✦" rotateDeg={-4} background={color.coral} textColor={color.white} style={{ marginBottom: space.xs }} />
        <Image source={LOGO_SOURCE} style={{ width: 66, height: 66 }} resizeMode="contain" />

        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displayMd + 2,
            lineHeight: 34,
            color: color.ink,
            textTransform: 'uppercase',
            textAlign: 'center',
          }}
        >
          WHAT SHOULD{'\n'}WE CALL YOU?
        </Text>
        <Text style={{ fontFamily: font.displayMedium, fontSize: fontSize.bodyMd, color: 'rgba(21,23,15,0.65)', textAlign: 'center' }}>
          Your home screen greets you by name.
        </Text>

        <View style={{ width: '100%', gap: space.sm, marginTop: space.sm }}>
          <TextField
            placeholder="Your name"
            value={value}
            onChangeText={(text) => {
              setName(text);
              setErrorMessage(null);
            }}
            autoCapitalize="words"
          />

          {errorMessage ? (
            <Text style={{ fontFamily: font.body, fontSize: 12, color: color.coral, textAlign: 'center' }}>
              {errorMessage}
            </Text>
          ) : null}

          <View style={{ marginTop: space.xs }}>
            <PillButton
              label={submitting ? 'Saving…' : 'Continue'}
              variant="coral"
              minHeight={50}
              fontSize={15}
              shadowColor={color.ink}
              disabled={submitting}
              onPress={() => handleContinue(false)}
            />
          </View>
        </View>

        <Text
          onPress={() => handleContinue(true)}
          style={{ fontFamily: font.body, fontSize: 12, color: 'rgba(21,23,15,0.6)', textAlign: 'center', textDecorationLine: 'underline', marginTop: space.sm }}
        >
          Skip for now
        </Text>
      </ScrollView>
    </View>
  );
}
