import { useState } from 'react';
import { Image, Platform, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { AmbientDot } from '@/components/AmbientDot';
import { Badge } from '@/components/Badge';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { useAuth } from '@/hooks/useAuth';
import { setOAuthNext } from '@/lib/oauth';
import { supabase } from '@/lib/supabase';
import { color, font, fontSize, space } from '@/theme/tokens';

const LOGO_SOURCE = require('../../assets/images/logo-mark-transparent.png');
const MIN_PASSWORD_LENGTH = 8;

export default function SignupScreen() {
  const router = useRouter();
  const { signUpWithPassword, signInWithOAuth } = useAuth();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);

  async function handleCreateAccount(): Promise<void> {
    if (!email.trim() || password.length < MIN_PASSWORD_LENGTH) {
      setErrorMessage(`Password needs at least ${MIN_PASSWORD_LENGTH} characters`);
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    setInfoMessage(null);
    // Tell the AuthGate where this flow should land, so its redirect
    // agrees with the manual one below even if it fires first.
    await setOAuthNext('signup');
    const { error, session } = await signUpWithPassword(email.trim(), password, name.trim() || undefined);
    setSubmitting(false);
    if (error) {
      setErrorMessage(error.message);
      return;
    }
    // Supabase may require email confirmation depending on project
    // settings. Only advance to the import flow when we actually hold a
    // session — otherwise the user would land in the app signed-out.
    if (session) {
      router.push('/(import)/import1');
    } else {
      setInfoMessage('Account created — check your email for a confirmation link, then log in.');
    }
  }

  async function handleOAuth(provider: 'google' | 'apple'): Promise<void> {
    setSubmitting(true);
    setErrorMessage(null);
    setInfoMessage(null);
    const { error } = await signInWithOAuth(provider, 'signup');
    setSubmitting(false);

    if (error) {
      if (error.name !== 'OAuthCancelled') {
        setErrorMessage(error.message);
      }
      return;
    }

    if (Platform.OS === 'web') {
      // Full-page redirect to Google is in flight; the AuthGate/index
      // route takes over when the app reloads on return. See login.tsx.
      return;
    }

    const { data } = await supabase.auth.getSession();
    if (data?.session) {
      router.push('/(import)/import1');
    } else {
      setErrorMessage(
        'Sign-in did not complete — no session was created. Check your connection and try again.',
      );
    }
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
          paddingTop: 40,
          paddingBottom: 24,
          gap: space.sm,
        }}
      >
        <Badge label="20 SEC ✦ FREE" rotateDeg={4} background={color.gold} style={{ marginBottom: 2 }} />
        <Image source={LOGO_SOURCE} style={{ width: 60, height: 60 }} resizeMode="contain" />

        <Text
          style={{
            fontFamily: font.display,
            fontSize: fontSize.displaySm + 6,
            lineHeight: 30,
            color: color.ink,
            textTransform: 'uppercase',
            textAlign: 'center',
          }}
        >
          Find stuff easily
        </Text>
        <Text style={{ fontFamily: font.displayMedium, fontSize: 13, color: 'rgba(21,23,15,0.65)', textAlign: 'center' }}>
          Takes 20 seconds. No credit card.
        </Text>

        <View style={{ width: '100%', gap: space.sm - 1, marginTop: space.xs }}>
          <PillButton
            label="Continue with Google"
            variant="outline"
            minHeight={46}
            fontSize={13.5}
            disabled={submitting}
            onPress={() => handleOAuth('google')}
          />
          <PillButton
            label="Continue with Apple"
            variant="outline"
            minHeight={46}
            fontSize={13.5}
            disabled={submitting}
            onPress={() => handleOAuth('apple')}
          />

          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, marginVertical: space.xs }}>
            <View style={{ flex: 1, height: 1.5, backgroundColor: color.ink, opacity: 0.2 }} />
            <Text style={{ fontFamily: font.body, fontSize: fontSize.mono, color: 'rgba(21,23,15,0.5)' }}>OR</Text>
            <View style={{ flex: 1, height: 1.5, backgroundColor: color.ink, opacity: 0.2 }} />
          </View>

          <TextField placeholder="Your name" value={name} onChangeText={setName} minHeight={46} />
          <TextField
            placeholder="you@email.com"
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            keyboardType="email-address"
            minHeight={46}
          />
          <TextField
            placeholder="8+ characters"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            minHeight={46}
          />

          {errorMessage ? (
            <Text style={{ fontFamily: font.body, fontSize: 12, color: color.coral, textAlign: 'center' }}>
              {errorMessage}
            </Text>
          ) : null}
          {infoMessage ? (
            <Text style={{ fontFamily: font.body, fontSize: 12, color: color.ink, textAlign: 'center' }}>
              {infoMessage}
            </Text>
          ) : null}

          <View style={{ marginTop: space.xs }}>
            <PillButton
              label={submitting ? 'Creating…' : 'Create account'}
              variant="coral"
              minHeight={50}
              fontSize={15}
              shadowColor={color.ink}
              disabled={submitting}
              onPress={handleCreateAccount}
            />
          </View>
        </View>

        <Text style={{ fontFamily: font.body, fontSize: 11, color: 'rgba(21,23,15,0.6)', textAlign: 'center', marginTop: 4 }}>
          ALREADY HAVE AN ACCOUNT?{' '}
          <Text
            onPress={() => router.push('/(auth)/login')}
            style={{ textDecorationLine: 'underline', fontWeight: '600', color: color.ink }}
          >
            LOG IN.
          </Text>
        </Text>
      </ScrollView>
    </View>
  );
}
