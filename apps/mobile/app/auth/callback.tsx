/**
 * OAuth deep-link landing screen.
 *
 * Native flow builds its redirect as spillthereel://auth/callback
 * (see buildOAuthRedirectUrl in src/lib/oauth.ts). When the OS delivers
 * the Supabase redirect via deep link — standalone builds, cold starts,
 * system-browser fallbacks — this screen exchanges it for a session and
 * routes onward. The in-app WebBrowser path (openAuthSessionAsync) never
 * lands here; it handles the callback inline.
 */
import { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useURL } from 'expo-linking';
import { supabase } from '@/lib/supabase';
import { consumeOAuthNext, handleOAuthCallbackUrl, routeForOAuthNext } from '@/lib/oauth';
import { color, font, fontSize } from '@/theme/tokens';

export default function AuthCallbackScreen() {
  const router = useRouter();
  const url = useURL();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function run(): Promise<void> {
      // No URL yet (or web, where detectSessionInUrl already handled it):
      // if a session exists we are done, otherwise wait for the deep link.
      const { data: existing } = await supabase.auth.getSession();
      const next = await consumeOAuthNext();
      const destination = routeForOAuthNext(next);

      if (existing.session) {
        router.replace(destination as never);
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
        setErrorMessage(error.message);
        return;
      }

      const { data } = await supabase.auth.getSession();
      if (data.session) {
        router.replace(destination as never);
      } else {
        setErrorMessage('Sign-in completed but no session was created. Please try again.');
      }
    }

    run().catch((err) => {
      if (!cancelled) {
        setErrorMessage(err?.message ?? 'Sign-in failed. Please try again.');
      }
    });

    return () => {
      cancelled = true;
    };
  }, [router, url]);

  return (
    <View style={{ flex: 1, backgroundColor: color.cream, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 }}>
      <Text style={{ fontFamily: font.display, fontSize: fontSize.displayMd, color: color.ink, textTransform: 'uppercase', textAlign: 'center' }}>
        {errorMessage ? 'SIGN-IN FAILED.' : 'FINISHING SIGN-IN…'}
      </Text>
      {errorMessage ? (
        <>
          <Text style={{ fontFamily: font.body, fontSize: 13, color: color.coral, textAlign: 'center', marginTop: 12 }}>
            {errorMessage}
          </Text>
          <Text
            onPress={() => router.replace('/(auth)/login' as never)}
            style={{ fontFamily: font.body, fontSize: 13, color: color.ink, textDecorationLine: 'underline', marginTop: 16 }}
          >
            Back to login
          </Text>
        </>
      ) : null}
    </View>
  );
}
