import { useEffect, useRef, useState } from 'react';
import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import type { NotificationResponse } from 'expo-notifications';
import * as Sentry from '@sentry/react-native';
import { PostHogProvider, usePostHog } from 'posthog-react-native';
import { useFonts } from 'expo-font';
import { Stack, useRouter, useSegments } from 'expo-router';
import { ShareIntentProvider, useShareIntentContext } from 'expo-share-intent';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useAuth } from '@/hooks/useAuth';
import { setAnalyticsClient } from '@/lib/analytics';
import { consumeOAuthNext, routeForOAuthNext } from '@/lib/oauth';
import { initSentry } from '@/lib/sentry';
import { clearPendingEmail, getPendingEmail } from '@/lib/verification';
import {
  decideImportSummaryRoute,
  notificationResponseKey,
} from '@/lib/notification-routing';

const handledNotificationResponseKeys = new Set<string>();

function rememberHandledNotificationResponse(key: string): void {
  handledNotificationResponseKeys.add(key);
  if (handledNotificationResponseKeys.size > 100) {
    const oldest = handledNotificationResponseKeys.values().next().value;
    if (oldest) handledNotificationResponseKeys.delete(oldest);
  }
}

// Error monitoring first: no-op without EXPO_PUBLIC_SENTRY_DSN.
initSentry();

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
  const { hasShareIntent } = useShareIntentContext();
  const segments = useSegments() as readonly string[];
  const router = useRouter();
  const [notificationResponse, setNotificationResponse] = useState<NotificationResponse | null>(null);
  const pendingResponseKey = useRef<string | null>(null);

  useEffect(() => {
    if (Platform.OS === 'web') return;
    const receive = (response: NotificationResponse) => {
      const key = notificationResponseKey(
        response.notification.request.identifier,
        response.actionIdentifier,
        response.notification.request.content.data,
      ) ?? 'unknown-response';
      if (handledNotificationResponseKeys.has(key) || pendingResponseKey.current === key) return;
      pendingResponseKey.current = key;
      setNotificationResponse(response);
    };
    const subscription = Notifications.addNotificationResponseReceivedListener(receive);
    void Notifications.getLastNotificationResponseAsync().then((response) => {
      if (response) receive(response);
    }).catch(() => {
      // Native response state may be unavailable in simulator or web builds.
    });
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    if (loading) {
      return;
    }
    const group = segments[0];
    const [, second] = segments;
    const inAuth = group === '(auth)';
    const inProtected = group === '(app)' || group === '(import)';
    // Recovery-link landing screen manages its own routing (it needs the
    // recovery session to stay put while the user sets a new password).
    const onResetPassword = group === 'auth' && second === 'reset-password';
    // Same for the post-OAuth name step — it navigates itself onward.
    const onYourName = inAuth && second === 'your-name';

    if (notificationResponse) {
      const response = notificationResponse;
      const decision = decideImportSummaryRoute(
        response.notification.request.content.data,
        session?.user.id,
        loading,
      );
      if (decision.status !== 'defer') {
        const key = notificationResponseKey(
          response.notification.request.identifier,
          response.actionIdentifier,
          response.notification.request.content.data,
        ) ?? 'unknown-response';
        pendingResponseKey.current = null;
        rememberHandledNotificationResponse(key);
        setNotificationResponse(null);
        void Notifications.clearLastNotificationResponseAsync().catch(() => {
          // The in-memory key still prevents duplicate routing for this session.
        });
        if (decision.status === 'route') {
          router.replace({
            pathname: '/(import)/import3',
            params: { notification_import_id: decision.importId },
          } as never);
          return;
        }
      }
    }

    if (session) {
      // Verified (or OAuth) session resolves any pending confirmation.
      void clearPendingEmail();
      if (inAuth && !onResetPassword && !onYourName) {
        // A share can launch the app signed out. Keep that captured payload in
        // front of the normal post-login landing route so login does not strand it.
        if (hasShareIntent) {
          router.replace('/share' as never);
          return;
        }
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
  }, [session, loading, segments, router, hasShareIntent, notificationResponse]);

  return null;
}

/** Route both cold and warm native shares into the focused save flow. */
function ShareIntentRouter() {
  const { hasShareIntent } = useShareIntentContext();
  const segments = useSegments() as readonly string[];
  const router = useRouter();

  useEffect(() => {
    if (!hasShareIntent || Platform.OS === 'web') return;
    const onShareRoute = segments[0] === 'share';
    const inAuth = segments[0] === '(auth)';
    if (!onShareRoute && !inAuth) {
      router.replace('/share' as never);
    }
  }, [hasShareIntent, segments, router]);

  return null;
}

function RootLayout() {
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
        <ShareIntentRouter />
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="share" options={{ headerShown: false }} />
        </Stack>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

/** Hands the live PostHog client to lib/analytics (no-op when disabled). */
function AnalyticsBootstrap() {
  const posthog = usePostHog();
  useEffect(() => {
    setAnalyticsClient(posthog);
    return () => setAnalyticsClient(null);
  }, [posthog]);
  return null;
}

const POSTHOG_KEY = process.env.EXPO_PUBLIC_POSTHOG_KEY;
const POSTHOG_HOST = process.env.EXPO_PUBLIC_POSTHOG_HOST || 'https://us.i.posthog.com';

function RootLayoutWithProviders() {
  const app = POSTHOG_KEY ? (
    <PostHogProvider
      apiKey={POSTHOG_KEY}
      autocapture={false}
      options={{ host: POSTHOG_HOST, enableSessionReplay: false }}
    >
      <AnalyticsBootstrap />
      <RootLayout />
    </PostHogProvider>
  ) : (
    <RootLayout />
  );

  // Privacy: nothing automatic — no session replay, no touch/screen
  // autocapture. All events are explicit track() calls (see lib/analytics).
  return (
    <ShareIntentProvider
      options={{ disabled: Platform.OS === 'web', resetOnBackground: false }}
    >
      {app}
    </ShareIntentProvider>
  );
}

export default Sentry.wrap(RootLayoutWithProviders);
