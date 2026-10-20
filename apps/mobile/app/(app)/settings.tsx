import { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { PillButton } from '@/components/PillButton';
import { SettingsRowView } from '@/components/SettingsRowView';
import { SETTINGS_GROUPS } from '@/data/settings';
import { useAuth } from '@/hooks/useAuth';
import { AvatarView } from '@/components/AvatarView';
import { getFirstName } from '@/lib/display-name';
import { disableNativePush, enableNativePush, nativePushEnabled, restoreNativePush } from '@/lib/native-push';
import { runWithPushRevoked } from '@/lib/auth-session';
import { ApiError, api, deleteMe } from '@/lib/api';
import {
  prepareShareQueueForAccountDeletion,
  restoreShareQueueAfterDeletionFailure,
} from '@/lib/share-queue';
import { border, color, font, radius, space } from '@/theme/tokens';

interface MeProfile {
  id: string;
  display_name: string | null;
  avatar_url: string | null;
}

interface ImportAllowance {
  tier: string;
  policy_version: string;
  total_limit: number;
  total_used: number;
  total_remaining: number;
  instagram_limit: number;
  instagram_used: number;
  instagram_remaining: number;
  other_limit: number;
  other_used: number;
  other_remaining: number;
}

type AllowanceState =
  | { status: 'loading' }
  | { status: 'available'; value: ImportAllowance }
  | { status: 'unavailable' };

export default function SettingsScreen() {
  const router = useRouter();
  const { signOut, user } = useAuth();
  const [profile, setProfile] = useState<MeProfile | null>(null);
  const [allowance, setAllowance] = useState<AllowanceState>({ status: 'loading' });
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [deletionScheduled, setDeletionScheduled] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const [pushState, setPushState] = useState<'loading' | 'enabled' | 'disabled' | 'busy' | 'error'>('loading');
  const [pushMessage, setPushMessage] = useState<string | null>(null);

  useEffect(() => {
    setProfile(null);
    setAllowance({ status: 'loading' });
    setPushMessage(null);
    if (!user) {
      setAllowance({ status: 'unavailable' });
      setPushState('disabled');
      return;
    }

    const controller = new AbortController();
    let current = true;
    void nativePushEnabled(user.id)
      .then((enabled) => {
        if (current) setPushState(enabled ? 'enabled' : 'disabled');
      })
      .catch(() => {
        if (current) {
          setPushState('error');
          setPushMessage('Notification settings could not be checked. You can still use your account.');
        }
      });
    void api.get<MeProfile>('/v1/me', { signal: controller.signal })
      .then((value) => {
        if (current) setProfile(value);
      })
      .catch(() => {
        // Keep the authenticated Supabase identity visible if profile lookup is unavailable.
      });
    void api.get<ImportAllowance>('/v1/me/import-allowance', { signal: controller.signal })
      .then((value) => {
        // The current API contract reports the approved free allowance only.
        // Do not infer an entitlement or reuse free bucket semantics for another tier.
        if (current) {
          setAllowance(value.tier === 'free'
            ? { status: 'available', value }
            : { status: 'unavailable' });
        }
      })
      .catch(() => {
        if (current) setAllowance({ status: 'unavailable' });
      });

    return () => {
      current = false;
      controller.abort();
    };
  }, [user?.id]);

  async function handleSignOut(): Promise<void> {
    if (signingOut) return;
    setSigningOut(true);
    setSignOutError(null);
    try {
      await signOut();
      router.replace('/(auth)/welcome');
    } catch {
      setSignOutError('Could not safely turn off device notifications or clear this session. Try again.');
    } finally {
      setSigningOut(false);
    }
  }

  async function handlePushPreference(): Promise<void> {
    if (!user?.id || pushState === 'busy' || deleting || signingOut) return;
    const wasEnabled = pushState === 'enabled';
    setPushState('busy');
    setPushMessage(null);
    const result = wasEnabled
      ? await disableNativePush(user.id)
      : await enableNativePush(user.id);
    if (result.status === 'registered') {
      setPushState('enabled');
      setPushMessage('Notifications are enabled on this device.');
    } else if (result.status === 'unregistered') {
      setPushState('disabled');
      setPushMessage('Notifications are off on this device.');
    } else if (result.status === 'permission_denied') {
      setPushState('disabled');
      setPushMessage('Permission is off. You can change it in your device settings; the app will keep working.');
    } else {
      setPushState(wasEnabled ? 'enabled' : 'disabled');
      setPushMessage('We could not update notifications. Your account and library are still available; try again.');
    }
  }

  function handleRowPress(rowId: string) {
    if (rowId === 'delete-account') {
      setDeleteError(null);
      setShowDeleteConfirm(true);
      return;
    }
    if (rowId === 'import') {
      router.push('/(import)/import2');
      return;
    }
    if (rowId === 'sign-out') {
      void handleSignOut();
    }
  }

  async function handleDeleteAccount(): Promise<void> {
    if (deleting) return;
    setDeleting(true);
    setDeleteError(null);
    let requestAccepted = deletionScheduled;
    let deletionRequestStarted = deletionScheduled;
    let deletionRejected = false;
    try {
      if (user?.id) await prepareShareQueueForAccountDeletion(user.id);
      if (!requestAccepted) {
        try {
          const sendDeletion = async () => {
            deletionRequestStarted = true;
            return deleteMe();
          };
          if (user?.id) {
            await runWithPushRevoked(sendDeletion, restoreNativePush, {
              restoreOnSuccess: () => false,
              restoreOnError: (error) => error instanceof ApiError && [400, 422, 429].includes(error.status),
            });
          } else {
            await sendDeletion();
          }
        } catch (error) {
          // Retrying after an ambiguous outcome is safe: the route is
          // idempotent, and middleware's explicit account-deleted response
          // proves an earlier request committed.
          const alreadyDeleted = error instanceof ApiError
            && error.status === 403
            && error.code === 'account_deleted';
          if (alreadyDeleted) {
            requestAccepted = true;
            setDeletionScheduled(true);
          } else if (
            user?.id
            && error instanceof ApiError
            && [400, 422, 429].includes(error.status)
          ) {
            // Only clear the durable fence for responses that reject the
            // request before the deletion transaction can commit.
            await restoreShareQueueAfterDeletionFailure(user.id);
            deletionRejected = true;
            throw error;
          } else {
            // A timeout, disconnect, 401/5xx, or other ambiguous response can
            // hide a committed DELETE. Keep the local queue fenced for retry.
            throw error;
          }
        }
        // DELETE /v1/me returns 202 after soft-flagging the account and
        // scheduling its background purge. Preserve this if sign-out fails.
        requestAccepted = true;
        setDeletionScheduled(true);
      }
      await signOut();
      setShowDeleteConfirm(false);
      router.replace('/(auth)/welcome');
    } catch (err) {
      if (!deletionRequestStarted && user?.id) {
        await restoreShareQueueAfterDeletionFailure(user.id).catch(() => {});
      }
      setDeleteError(
        requestAccepted
          ? 'Your deletion request was accepted, but sign-out did not finish. Try signing out again.'
          : deletionRequestStarted && !deletionRejected
            ? 'We could not confirm the deletion request. Pending links were cleared from this device; retry deletion to reconcile the result.'
          : err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
            : 'Could not submit your deletion request. Try again.',
      );
    } finally {
      setDeleting(false);
    }
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.sage }}>
      <ScrollView
        contentContainerStyle={{ paddingHorizontal: 18, paddingTop: 54, paddingBottom: 130, gap: space.lg }}
        accessibilityElementsHidden={showDeleteConfirm}
        importantForAccessibility={showDeleteConfirm ? 'no-hide-descendants' : 'auto'}
      >
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text style={{ fontFamily: font.display, fontSize: 30, color: color.ink }}>SETTINGS</Text>
          <Pressable
            onPress={() => router.push('/(app)/profile')}
            accessibilityRole="button"
            accessibilityLabel="Open profile"
          >
            <AvatarView
              user={user}
              size={32}
              fallbackLetter={(getFirstName(user)[0] ?? 'S').toUpperCase()}
              backgroundColor={color.violet}
            />
          </Pressable>
        </View>

        <View
          accessible
          accessibilityLabel={`Signed in${user?.email ? ` as ${user.email}` : ''}`}
          style={{
            padding: space.lg,
            borderWidth: border.hairline,
            borderColor: color.ink,
            borderRadius: radius.md,
            backgroundColor: color.cream,
            gap: space.xs,
          }}
        >
          <Text style={{ fontFamily: font.body, fontSize: 14, color: color.ink, fontWeight: '600' }}>
            {profile?.display_name || getFirstName(user) || 'Your account'}
          </Text>
          <Text selectable style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
            {user?.email ?? 'Signed-in account'}
          </Text>
        </View>

        <View
          accessibilityRole="summary"
          style={{
            padding: space.lg,
            borderWidth: border.hairline,
            borderColor: color.ink,
            borderRadius: radius.md,
            gap: space.sm,
          }}
        >
          <Text accessibilityRole="header" style={{ fontFamily: font.body, fontSize: 16, color: color.ink, fontWeight: '700' }}>
            One-time import allowance
          </Text>
          {allowance.status === 'loading' ? (
            <Text accessibilityLiveRegion="polite" style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
              Checking your allowance…
            </Text>
          ) : allowance.status === 'unavailable' ? (
            <Text accessibilityLiveRegion="polite" style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
              Your allowance details aren’t available right now.
            </Text>
          ) : (
            <>
              <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
                Instagram: {allowance.value.instagram_remaining} of {allowance.value.instagram_limit} remaining
              </Text>
              <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
                Other apps: {allowance.value.other_remaining} of {allowance.value.other_limit} remaining
              </Text>
              <Text style={{ fontFamily: font.body, fontSize: 13, lineHeight: 19, color: 'rgba(21,23,15,0.72)' }}>
                This allowance is shared across bulk imports on your account. Uploading another archive does not reset it.
              </Text>
            </>
          )}
        </View>

        {SETTINGS_GROUPS.map((group) => (
          <View key={group.id} style={{ gap: space.sm }}>
            <Text style={{ fontFamily: font.body, fontSize: 10.5, letterSpacing: 0.6, color: 'rgba(21,23,15,0.55)' }}>
              {group.eyebrow}
            </Text>
            <View style={{ gap: space.sm }}>
              {group.rows.map((row) => (
                <SettingsRowView key={row.id} row={row} onPress={() => handleRowPress(row.id)} />
              ))}
            </View>
          </View>
        ))}

        <View style={{ gap: space.xs }}>
          <Text accessibilityRole="header" style={{ fontFamily: font.body, fontSize: 16, color: color.ink, fontWeight: '700' }}>
            Notifications
          </Text>
          <Text style={{ fontFamily: font.body, fontSize: 13, lineHeight: 19, color: 'rgba(21,23,15,0.72)' }}>
            Choose whether this device receives useful updates. You can keep using SpillTheReel if you decline.
          </Text>
          {pushMessage ? (
            <Text
              accessibilityRole={pushState === 'error' ? 'alert' : undefined}
              accessibilityLiveRegion={pushState === 'error' ? 'assertive' : 'polite'}
              style={{ fontFamily: font.body, fontSize: 13, lineHeight: 19, color: pushState === 'error' ? '#B3261E' : color.ink }}
            >
              {pushMessage}
            </Text>
          ) : null}
          <Pressable
            onPress={() => void handlePushPreference()}
            disabled={!user?.id || pushState === 'loading' || pushState === 'busy' || deleting || signingOut}
            accessibilityRole="button"
            accessibilityLabel={pushState === 'enabled' ? 'Turn off notifications' : 'Enable notifications'}
            accessibilityHint="Controls notification delivery for this device. Declining does not limit app access."
            accessibilityState={{ disabled: !user?.id || pushState === 'loading' || pushState === 'busy' || deleting || signingOut, busy: pushState === 'busy' }}
            style={{
              minHeight: 48,
              justifyContent: 'center',
              alignItems: 'center',
              paddingHorizontal: space.lg,
              borderWidth: border.hairline,
              borderColor: color.ink,
              borderRadius: radius.md,
              backgroundColor: pushState === 'enabled' ? color.cream : color.ink,
              opacity: pushState === 'loading' || pushState === 'busy' || deleting || signingOut || !user?.id ? 0.6 : 1,
            }}
          >
            <Text style={{ fontFamily: font.body, fontSize: 14, fontWeight: '700', color: pushState === 'enabled' ? color.ink : color.cream }}>
              {pushState === 'loading' ? 'Checking…' : pushState === 'busy' ? 'Updating…' : pushState === 'enabled' ? 'Turn off notifications' : 'Enable notifications'}
            </Text>
          </Pressable>
        </View>

        <Text style={{ textAlign: 'center', fontFamily: font.body, fontSize: 9.5, color: 'rgba(21,23,15,0.4)', marginTop: space.sm }}>
          SPILLTHEREEL V1.0.0
        </Text>
      </ScrollView>

      {showDeleteConfirm ? (
        <View
          style={{
            position: 'absolute',
            inset: 0,
            backgroundColor: 'rgba(21,23,15,0.55)',
            alignItems: 'center',
            justifyContent: 'center',
            padding: space.xxl,
          }}
          onAccessibilityEscape={() => {
            if (!deleting) setShowDeleteConfirm(false);
          }}
        >
          <View
            accessibilityViewIsModal
            style={{
              backgroundColor: color.cream,
              borderWidth: border.heavy,
              borderColor: color.ink,
              borderRadius: radius.lg,
              padding: space.xxl,
              width: '100%',
              gap: space.md,
              shadowColor: color.ink,
              shadowOffset: { width: 6, height: 6 },
              shadowOpacity: 1,
              shadowRadius: 0,
            }}
          >
            <Text accessibilityRole="header" style={{ fontFamily: font.display, fontSize: 20, color: color.ink, textTransform: 'uppercase' }}>
              {deletionScheduled ? 'Deletion requested' : 'Delete account?'}
            </Text>
            <Text style={{ fontFamily: font.body, fontSize: 13, color: 'rgba(21,23,15,0.75)' }}>
              {deletionScheduled
                ? 'Your request was accepted. Removal of your account and saved data is scheduled.'
                : 'We’ll clear unsent shared links from this device, then mark your account for deletion and schedule removal of your saved data. This request cannot be undone.'}
            </Text>
            {deleteError ? (
              <Text
                accessibilityRole="alert"
                accessibilityLiveRegion="assertive"
                style={{ fontFamily: font.body, fontSize: 12.5, color: '#B3261E' }}
              >
                {deleteError}
              </Text>
            ) : null}
            <PillButton
              label={deleting ? (deletionScheduled ? 'Signing out…' : 'Submitting…') : deletionScheduled ? 'Sign out' : 'Delete my account'}
              variant="coral"
              minHeight={48}
              fontSize={14}
              disabled={deleting}
              loading={deleting}
              accessibilityHint={deletionScheduled
                ? 'Finish signing out. Your deletion request has already been accepted.'
                : 'Submit an account deletion request. Account removal continues in the background.'}
              onPress={() => void handleDeleteAccount()}
            />
            <PillButton
              label={deletionScheduled ? 'Close' : 'Cancel'}
              variant="outline"
              minHeight={48}
              fontSize={14}
              disabled={deleting}
              accessibilityHint={deletionScheduled
                ? 'Close this message. Your deletion request remains scheduled.'
                : 'Close this confirmation without requesting account deletion.'}
              onPress={() => setShowDeleteConfirm(false)}
            />
          </View>
        </View>
      ) : null}

      {signOutError ? (
        <Text
          accessibilityRole="alert"
          accessibilityLiveRegion="assertive"
          style={{
            position: 'absolute',
            left: space.lg,
            right: space.lg,
            bottom: 102,
            padding: space.md,
            borderRadius: radius.md,
            backgroundColor: color.ink,
            color: color.cream,
            fontFamily: font.body,
            fontSize: 14,
          }}
        >
          {signOutError}
        </Text>
      ) : null}
    </View>
  );
}
