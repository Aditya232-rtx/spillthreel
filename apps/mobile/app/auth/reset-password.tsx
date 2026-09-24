/**
 * Password-recovery deep-link landing screen.
 *
 * Mirrors app/auth/callback.tsx: the recovery email points here
 * (see buildPasswordResetRedirectUrl in src/lib/oauth.ts). On arrival we
 * exchange the one-time code for a recovery session (native PKCE flow;
 * on web Supabase already consumed the URL via detectSessionInUrl), then
 * present new-password + confirm fields and call updateUser.
 *
 * Explicitly handled: expired/used links (exchange fails → ask for a
 * fresh link), mismatched confirmation, and weak passwords (same rules
 * as signup via src/lib/password.ts).
 */
import { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useURL } from 'expo-linking';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { handleOAuthCallbackUrl } from '@/lib/oauth';
import { validatePassword, validatePasswordMatch } from '@/lib/password';
import { supabase } from '@/lib/supabase';
import { color, font, fontSize, space } from '@/theme/tokens';

type Phase = 'verifying' | 'ready' | 'done' | 'link_invalid';

export default function ResetPasswordScreen() {
  const router = useRouter();
  const url = useURL();
  const [phase, setPhase] = useState<Phase>('verifying');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function run(): Promise<void> {
      // Web: Supabase already exchanged the recovery link from the URL
      // (detectSessionInUrl) — a session means the link was valid.
      // Native: exchange the one-time code from the deep link ourselves.
      const { data: existing } = await supabase.auth.getSession();
      if (existing.session) {
        if (!cancelled) {
          setPhase('ready');
        }
        return;
      }

      if (!url) {
        return;
      }

      const { error } = await handleOAuthCallbackUrl(url);
      if (cancelled) {
        return;
      }
      if (error) {
        setPhase('link_invalid');
        setErrorMessage(error.message);
        return;
      }

      const { data } = await supabase.auth.getSession();
      if (!cancelled) {
        if (data.session) {
          setPhase('ready');
        } else {
          setPhase('link_invalid');
          setErrorMessage('This reset link is expired or was already used. Request a fresh one.');
        }
      }
    }

    run().catch(() => {
      if (!cancelled) {
        setPhase('link_invalid');
        setErrorMessage('This reset link is expired or was already used. Request a fresh one.');
      }
    });

    return () => {
      cancelled = true;
    };
  }, [url]);

  async function handleSetPassword(): Promise<void> {
    const strengthError = validatePassword(password);
    if (strengthError) {
      setErrorMessage(strengthError);
      return;
    }
    const matchError = validatePasswordMatch(password, confirm);
    if (matchError) {
      setErrorMessage(matchError);
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    const { error } = await supabase.auth.updateUser({ password });
    setSubmitting(false);
    if (error) {
      setErrorMessage(error.message);
      return;
    }
    setPhase('done');
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.cream, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32, gap: space.md }}>
      <Text style={{ fontFamily: font.display, fontSize: fontSize.displayMd, color: color.ink, textTransform: 'uppercase', textAlign: 'center' }}>
        {phase === 'done' ? 'PASSWORD SET.' : phase === 'link_invalid' ? 'LINK EXPIRED.' : 'NEW PASSWORD.'}
      </Text>

      {phase === 'verifying' ? (
        <Text style={{ fontFamily: font.body, fontSize: 13, color: 'rgba(21,23,15,0.6)', textAlign: 'center' }}>
          Checking your reset link…
        </Text>
      ) : null}

      {phase === 'link_invalid' ? (
        <>
          <Text style={{ fontFamily: font.body, fontSize: 13, color: color.coral, textAlign: 'center' }}>
            {errorMessage ?? 'This reset link is expired or was already used. Request a fresh one.'}
          </Text>
          <PillButton
            label="Request a new link"
            variant="ink"
            minHeight={50}
            fontSize={15}
            onPress={() => router.replace('/(auth)/forgot-password' as never)}
          />
        </>
      ) : null}

      {phase === 'ready' ? (
        <View style={{ width: '100%', gap: space.sm }}>
          <TextField placeholder="New password" value={password} onChangeText={setPassword} secureTextEntry />
          <TextField placeholder="Confirm new password" value={confirm} onChangeText={setConfirm} secureTextEntry />
          <Text style={{ fontFamily: font.body, fontSize: 11, color: 'rgba(21,23,15,0.55)', textAlign: 'center' }}>
            8+ characters, with at least one letter and one number.
          </Text>
          {errorMessage ? (
            <Text style={{ fontFamily: font.body, fontSize: 12, color: color.coral, textAlign: 'center' }}>
              {errorMessage}
            </Text>
          ) : null}
          <PillButton
            label={submitting ? 'Saving…' : 'Set new password'}
            variant="coral"
            minHeight={50}
            fontSize={15}
            shadowColor={color.ink}
            disabled={submitting}
            onPress={handleSetPassword}
          />
        </View>
      ) : null}

      {phase === 'done' ? (
        <>
          <Text style={{ fontFamily: font.body, fontSize: 13, color: 'rgba(21,23,15,0.65)', textAlign: 'center' }}>
            Your password is updated. You are signed in.
          </Text>
          <PillButton
            label="Continue to home"
            variant="ink"
            minHeight={50}
            fontSize={15}
            onPress={() => router.replace('/(app)/home' as never)}
          />
        </>
      ) : null}
    </View>
  );
}
