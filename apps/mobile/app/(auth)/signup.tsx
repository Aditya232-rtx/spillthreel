import { useState } from 'react';
import { Image, Platform, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { AmbientDot } from '@/components/AmbientDot';
import { Badge } from '@/components/Badge';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { useAuth } from '@/hooks/useAuth';
import { rememberDisplayName } from '@/lib/display-name';
import { setOAuthNext } from '@/lib/oauth';
import { validatePassword, validatePasswordMatch } from '@/lib/password';
import { setPendingEmail } from '@/lib/verification';
import { supabase } from '@/lib/supabase';
import { color, font, fontSize, space } from '@/theme/tokens';

const LOGO_SOURCE = require('../../assets/images/logo-mark-transparent.png');

export default function SignupScreen() {
  const router = useRouter();
  const { signUpWithPassword, signInWithOAuth } = useAuth();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);

  async function handleCreateAccount(): Promise<void> {
    if (!email.trim()) {
      setEmailError('Enter an email address');
      return;
    }
    const strengthError = validatePassword(password);
    if (strengthError) {
      setErrorMessage(strengthError);
      return;
    }
    const matchError = validatePasswordMatch(password, confirmPassword);
    if (matchError) {
      setErrorMessage(matchError);
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    setEmailError(null);
    setInfoMessage(null);
    // Tell the AuthGate where this flow should land, so its redirect
    // agrees with the manual one below even if it fires first.
    await setOAuthNext('signup');
    const { error, session, user } = await signUpWithPassword(email.trim(), password, name.trim() || undefined);
    if (error) {
      setSubmitting(false);
      setErrorMessage(error.message);
      return;
    }
    // Session in hand — fresh account, straight into the app.
    if (session) {
      const typedName = name.trim();
      if (typedName) {
        await rememberDisplayName(email.trim(), typedName);
      }
      setSubmitting(false);
      router.push('/(import)/import1');
      return;
    }
    // No session and no error. Two possibilities: (a) confirmation mail
    // is on its way, or (b) this email already has an account — Supabase
    // deliberately returns an obfuscated user (empty identities) instead
    // of an "already exists" error, to block account enumeration.
    const identities = user?.identities ?? [];
    if (identities.length === 0) {
      // Can't trust the field alone (some versions omit it entirely), so
      // prove ownership: sign in with the credentials just entered. The
      // user typed this password seconds ago, so success unambiguously
      // means the account pre-existed — sign them in and continue.
      const { data: retry } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (retry.session) {
        // Existing account: apply the freshly typed name if it differs
        // from what's stored, otherwise the greeting keeps showing the
        // old name forever. Remember it too, so a later OAuth login
        // with a provider-side name can't silently overwrite it.
        const typedName = name.trim();
        const storedName = retry.session.user?.user_metadata?.full_name;
        if (typedName && typedName !== storedName) {
          await supabase.auth.updateUser({ data: { full_name: typedName } });
        }
        if (typedName) {
          await rememberDisplayName(email.trim(), typedName);
        }
        setSubmitting(false);
        router.push('/(import)/import1');
        return;
      }
      setSubmitting(false);
      setEmailError('This email already has an account — log in instead.');
      setInfoMessage('If this is a new address, check your inbox for the confirmation link.');
      return;
    }
    // Real user record, confirmation mail on its way. Don't strand them:
    // remember the pending address and the typed name, and let them into
    // the import flow — the guide screens are static, and the upload step
    // + home nudge them to confirm. Tapping the email link returns to the
    // app with a session (emailRedirectTo) and lands them back here via
    // next='signup'. The name is already in user_metadata from signUp, but
    // remembering it guards against a later provider-side overwrite.
    setSubmitting(false);
    await setPendingEmail(email.trim());
    const pendingName = name.trim();
    if (pendingName) {
      await rememberDisplayName(email.trim(), pendingName);
    }
    router.push('/(import)/import1');
  }

  async function handleOAuth(provider: 'google' | 'apple'): Promise<void> {
    setSubmitting(true);
    setErrorMessage(null);
    setInfoMessage(null);
    // OAuth signups detour through the your-name screen (prefilled when
    // the provider gave us a name) before the import flow.
    const { error } = await signInWithOAuth(provider, 'name');
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
      router.push('/(auth)/your-name');
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
            onChangeText={(text) => {
              setEmail(text);
              setEmailError(null);
            }}
            autoCapitalize="none"
            keyboardType="email-address"
            minHeight={46}
          />
          {emailError ? (
            <Text style={{ fontFamily: font.body, fontSize: 12, color: color.coral, textAlign: 'center' }}>
              {emailError}
            </Text>
          ) : null}
          <TextField
            placeholder="Password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            minHeight={46}
          />
          <TextField
            placeholder="Confirm password"
            value={confirmPassword}
            onChangeText={setConfirmPassword}
            secureTextEntry
            minHeight={46}
          />
          <Text style={{ fontFamily: font.body, fontSize: 11, color: 'rgba(21,23,15,0.55)', textAlign: 'center' }}>
            8+ characters, with at least one letter and one number.
          </Text>

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
