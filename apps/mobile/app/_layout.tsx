import { useEffect } from 'react';
import { useFonts } from 'expo-font';
import { Stack, useRouter, useSegments } from 'expo-router';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useAuth } from '@/hooks/useAuth';
import { consumeOAuthNext, routeForOAuthNext } from '@/lib/oauth';
import { clearPendingEmail, getPendingEmail } from '@/lib/verification';

SplashScreen.preventAutoHideAsync().catch(() => {
  // no-op: acceptable if called before native module is ready
});

/**
 * Central auth gate. Without this, a user returning from the Google
 * OAuth round-trip (web full-page redirect, or native deep link) holds
 * a valid session but sits on whatever screen the redirect landed on —
 * typically back at welcome/login with no onward navigation.
 *
 * Rules:
 *   * signed in + on an (auth) screen  → home (login) or import (signup)
 *   * signed out + on an (app)/(import) screen → welcome
 *   * auth/reset-password is exempt from the first rule: arriving from a
 *     recovery link creates a recovery session, and redirecting to home
 *     would strand the user before they set a new password.
 * The login-vs-signup destination comes from the flag each auth handler
 * stores before starting its flow (see setOAuthNext in lib/oauth.ts).
 */
function AuthGate() {
  const { session, loading } = useAuth();
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (loading) {
      return;
    }
    const group = segments[0];
    const inAuth = group === '(auth)';
    const inProtected = group === '(app)' || group === '(import)';
    // Recovery-link landing screen manages its own routing (it needs the
    // recovery session to stay put while the user sets a new password).
    const onResetPassword = group === 'auth' && segments[1] === 'reset-password';
    // Same for the post-OAuth name step — it navigates itself onward.
    const onYourName = inAuth && segments[1] === 'your-name';

    if (session) {
      // Verified (or OAuth) session resolves any pending confirmation.
      void clearPendingEmail();
      if (inAuth && !onResetPassword && !onYourName) {
        consumeOAuthNext().then((next) => {
          router.replace(routeForOAuthNext(next) as never);
        });
      }
      return;
    }
    if (inProtected) {
      // Fresh email signups have no session until the link is tapped, but
      // may still browse the (static) import guide AND home while the mail
      // lands — the verify nudge follows them there. Never bounce a
      // pending user back to welcome; only fully-signed-out users go there.
      void getPendingEmail().then((pending) => {
        if (!pending) {
          router.replace('/(auth)/welcome' as never);
        }
      });
    }
  }, [session, loading, segments, router]);

  return null;
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Oswald_700Bold: require('../assets/fonts/Oswald-Bold.ttf'),
    Oswald_600SemiBold: require('../assets/fonts/Oswald-SemiBold.ttf'),
    Oswald_500Medium: require('../assets/fonts/Oswald-Medium.ttf'),
    Oswald_400Regular: require('../assets/fonts/Oswald-Regular.ttf'),
    MyLove: require('../assets/fonts/MyLove.otf'),
  });

  useEffect(() => {
    if (fontsLoaded || fontError) {
      SplashScreen.hideAsync().catch(() => {
        // no-op
      });
    }
  }, [fontsLoaded, fontError]);

  if (!fontsLoaded && !fontError) {
    return null;
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <StatusBar style="dark" />
        <AuthGate />
        <Stack screenOptions={{ headerShown: false }} />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
