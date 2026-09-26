import { useEffect, useState } from 'react';
import { Redirect } from 'expo-router';
import { useAuth } from '@/hooks/useAuth';
import { consumeOAuthNext, routeForOAuthNext } from '@/lib/oauth';
import { getPendingEmail } from '@/lib/verification';

/**
 * Entry route. Session-aware: a signed-in user (e.g. just back from the
 * web Google round-trip, which lands on `/`) goes straight into the app
 * instead of flashing the welcome screen. The login-vs-signup
 * destination reuses the flag stored before the auth flow started.
 */
export default function Index() {
  const { session, loading } = useAuth();
  const [destination, setDestination] = useState<string | null>(null);

  useEffect(() => {
    if (loading) {
      return;
    }
    if (!session) {
      // Pending-confirmation users resume the import flow, never welcome.
      getPendingEmail().then((pending) => {
        setDestination(pending ? '/(import)/import1' : '/(auth)/welcome');
      });
      return;
    }
    consumeOAuthNext().then((next) => {
      setDestination(routeForOAuthNext(next));
    });
  }, [session, loading]);

  if (!destination) {
    return null;
  }
  return <Redirect href={destination as never} />;
}
