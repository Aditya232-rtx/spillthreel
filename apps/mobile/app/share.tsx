import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useShareIntentContext } from 'expo-share-intent';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { PillButton } from '@/components/PillButton';
import { useAuth } from '@/hooks/useAuth';
import { clearQueuedShares, enqueueSharedUrl, flushSharedQueue } from '@/lib/share-queue';
import { border, color, font, radius, space } from '@/theme/tokens';

type SharedLink = { status: 'ready'; url: string } | {
  status: 'unsupported'; message: string;
};

type QueueState =
  | { ownerId: string; status: 'syncing'; message: string }
  | { ownerId: string; status: 'queued'; message: string }
  | { ownerId: string; status: 'accepted'; message: string }
  | { ownerId: string; status: 'cleared'; message: string }
  | null;

const URL_START = /https?:\/\//gi;
const URL_TOKEN = /https?:\/\/[^\s<>"'`]+/i;
const TRAILING_URL_PUNCTUATION = /[.,!?;:)\]}]+$/;

function validateHttpUrl(candidate: string): string | null {
  try {
    const parsed = new URL(candidate);
    if (
      !['http:', 'https:'].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

function resolveSharedLink(intent: {
  type: string | null;
  webUrl: string | null;
  text?: string | null;
  files: unknown[] | null;
}): SharedLink | null {
  if (intent.files?.length || intent.type === 'file' || intent.type === 'media') {
    return {
      status: 'unsupported',
      message: 'This share contains a file. SpillTheReel can save web links only.',
    };
  }

  if (intent.webUrl) {
    const url = validateHttpUrl(intent.webUrl.trim());
    return url
      ? { status: 'ready', url }
      : { status: 'unsupported', message: 'This web address is not a supported HTTP or HTTPS link.' };
  }

  if (!intent.text || !['text', 'weburl'].includes(intent.type ?? '')) {
    return null;
  }

  const starts = intent.text.match(URL_START) ?? [];
  const match = intent.text.match(URL_TOKEN)?.[0];
  if (starts.length !== 1 || !match) {
    return {
      status: 'unsupported',
      message: starts.length > 1
        ? 'Share one web link at a time.'
        : 'This share does not contain a web link we can save.',
    };
  }

  const candidate = match.replace(TRAILING_URL_PUNCTUATION, '');
  const url = validateHttpUrl(candidate);
  return url
    ? { status: 'ready', url }
    : { status: 'unsupported', message: 'This web address is not a supported HTTP or HTTPS link.' };
}

export default function ShareScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, loading: authLoading } = useAuth();
  const {
    hasShareIntent,
    shareIntent,
    resetShareIntent,
    error: shareError,
  } = useShareIntentContext();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [queueState, setQueueState] = useState<QueueState>(null);
  const currentUserId = useRef<string | null>(user?.id ?? null);
  currentUserId.current = user?.id ?? null;

  const sharedLink = hasShareIntent ? resolveSharedLink(shareIntent) : null;
  const currentQueueState = queueState?.ownerId === user?.id ? queueState : null;

  const refreshQueue = useCallback(async (ownerId: string): Promise<void> => {
    setQueueState({ ownerId, status: 'syncing', message: 'Checking for saved links to send…' });
    try {
      const result = await flushSharedQueue(ownerId);
      if (currentUserId.current !== ownerId) return;
      if (result.remaining > 0) {
        setQueueState({
          ownerId,
          status: 'queued',
          message: result.errorCode
            ? 'Saved on this device. We’ll retry when the connection is available.'
            : 'Waiting to reach the server. Your link is safe on this device.',
        });
      } else {
        setQueueState({
          ownerId,
          status: 'accepted',
          message: result.accepted > 0
            ? 'The server accepted your link. It may take a little while to become searchable.'
            : 'Your saved links are up to date.',
        });
      }
    } catch {
      if (currentUserId.current === ownerId) {
        setQueueState({
          ownerId,
          status: 'queued',
          message: 'Saved on this device. We’ll retry when the connection is available.',
        });
      }
    }
  }, []);

  useEffect(() => {
    setQueueState(null);
    if (user?.id) void refreshQueue(user.id);
  }, [user?.id, refreshQueue]);

  useFocusEffect(
    useCallback(() => {
      if (!user?.id) return undefined;
      void refreshQueue(user.id);
      const subscription = AppState.addEventListener('change', (state) => {
        if (state === 'active' && currentUserId.current === user.id) {
          void refreshQueue(user.id);
        }
      });
      return () => subscription.remove();
    }, [user?.id, refreshQueue]),
  );

  async function saveSharedLink(): Promise<void> {
    if (!user?.id || !sharedLink || sharedLink.status !== 'ready' || saving) return;
    const ownerId = user.id;
    setSaving(true);
    setSaveError(null);
    setQueueState({ ownerId, status: 'syncing', message: 'Saving this link on your device…' });
    try {
      // This explicit action binds the native payload to the account shown on
      // screen. The queue write must finish before native state is cleared.
      await enqueueSharedUrl(ownerId, sharedLink.url);
      resetShareIntent();
      if (currentUserId.current !== ownerId) return;
      setQueueState({
        ownerId,
        status: 'queued',
        message: 'Saved on this device. Sending it to SpillTheReel…',
      });
      await refreshQueue(ownerId);
    } catch (error) {
      if (currentUserId.current === ownerId) {
        const deletionPending = error instanceof Error
          && error.message === 'share_queue_account_deleting';
        setSaveError(deletionPending
          ? 'This account has a pending deletion request. Finish signing out before saving more links.'
          : 'We couldn’t save this link on your device. Keep this share open and try again.');
        setQueueState({
          ownerId,
          status: 'queued',
          message: deletionPending
            ? 'This account has a pending deletion request. Sign out before continuing.'
            : 'We couldn’t save this link yet. Keep the share open and try again.',
        });
      }
    } finally {
      setSaving(false);
    }
  }

  function dismissShare(): void {
    resetShareIntent();
    router.replace(user ? '/(app)/home' : '/(auth)/welcome');
  }

  const title = Platform.OS === 'web' ? 'Share links from Android' : 'Save a shared link';

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          justifyContent: 'center',
          paddingHorizontal: space.xxl,
          paddingTop: Math.max(insets.top, space.xxl),
          paddingBottom: Math.max(insets.bottom, space.xxl),
          gap: space.xl,
        }}
      >
        <View style={{ gap: space.sm }}>
          <Text
            accessibilityRole="header"
            style={{
              fontFamily: font.display,
              fontSize: 32,
              lineHeight: 38,
              color: color.ink,
              textTransform: 'uppercase',
            }}
          >
            {title}
          </Text>
          <Text style={{ fontFamily: font.body, fontSize: 16, lineHeight: 24, color: color.ink }}>
            {Platform.OS === 'web'
              ? 'Native share capture is available in the Android app. You can open SpillTheReel there and share a web link from another app.'
              : 'Bring a web link here, then choose the account where it belongs.'}
          </Text>
        </View>

        {authLoading ? (
          <Text accessibilityLiveRegion="polite" style={{ fontFamily: font.body, fontSize: 16, lineHeight: 24, color: color.ink }}>
            Checking your account…
          </Text>
        ) : !user ? (
          <View style={{ gap: space.md }}>
            <View style={surfaceStyle}>
              <Text style={bodyStyle}>
                Your shared link is waiting here. Sign in first; it will stay on this device until you choose to save it.
              </Text>
            </View>
            <PillButton
              label="Sign in to continue"
              variant="coral"
              minHeight={52}
              onPress={() => router.push('/(auth)/login')}
            />
            <PillButton
              label="Create an account"
              variant="outline"
              minHeight={52}
              onPress={() => router.push('/(auth)/signup')}
            />
          </View>
        ) : shareError ? (
          <View style={{ gap: space.md }}>
            <View style={surfaceStyle}>
              <Text accessibilityRole="alert" style={bodyStyle}>
                SpillTheReel couldn’t read this share. Open the source app and share the link again.
              </Text>
            </View>
            <PillButton label="Dismiss share" variant="outline" minHeight={52} onPress={dismissShare} />
          </View>
        ) : sharedLink?.status === 'unsupported' ? (
          <View style={{ gap: space.md }}>
            <View style={surfaceStyle}>
              <Text style={bodyStyle}>{sharedLink.message}</Text>
              {shareIntent.files?.length ? (
                <Text style={[bodyStyle, { marginTop: space.sm }]}>
                  Media files are not uploaded from the share sheet.
                </Text>
              ) : null}
            </View>
            <PillButton label="Dismiss share" variant="outline" minHeight={52} onPress={dismissShare} />
          </View>
        ) : sharedLink?.status === 'ready' ? (
          <View style={{ gap: space.md }}>
            <View style={surfaceStyle}>
              <Text style={labelStyle}>SHARED LINK</Text>
              <Text selectable style={[bodyStyle, { marginTop: space.xs }]} numberOfLines={4}>
                {sharedLink.url}
              </Text>
              {saveError ? (
                <Text accessibilityRole="alert" style={[bodyStyle, { marginTop: space.sm, color: '#A12D22' }]}>
                  {saveError}
                </Text>
              ) : null}
            </View>
            <View style={surfaceStyle}>
              <Text style={labelStyle}>SAVE TO THIS ACCOUNT</Text>
              <Text selectable style={[bodyStyle, { marginTop: space.xs }]} numberOfLines={2}>
                {user.email ?? 'Signed-in account'}
              </Text>
              <Text style={[bodyStyle, { marginTop: space.sm }]}>
                Saving starts full media processing. This is a regular save and does not use your bulk-import allowance.
              </Text>
            </View>
            <PillButton
              label={saving ? 'Saving on this device…' : 'Save to my library'}
              variant="coral"
              minHeight={54}
              loading={saving}
              disabled={saving}
              onPress={saveSharedLink}
            />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Dismiss shared link"
              accessibilityHint="Clears this share without saving it"
              onPress={dismissShare}
              style={{ minHeight: 48, alignItems: 'center', justifyContent: 'center' }}
            >
              <Text style={{ fontFamily: font.body, fontSize: 16, lineHeight: 24, color: color.ink, textDecorationLine: 'underline' }}>
                Not now
              </Text>
            </Pressable>
          </View>
        ) : currentQueueState ? (
          <View style={{ gap: space.md }}>
            <View style={surfaceStyle} accessibilityLiveRegion="polite">
              <Text style={labelStyle}>
                {currentQueueState.status === 'syncing'
                  ? 'SENDING SAVED LINKS'
                  : currentQueueState.status === 'queued'
                    ? 'SAVED ON THIS DEVICE'
                    : currentQueueState.status === 'cleared'
                      ? 'CLEARED FROM DEVICE'
                      : 'SENT TO SPILLTHEREEL'}
              </Text>
              <Text style={[bodyStyle, { marginTop: space.xs }]}>{currentQueueState.message}</Text>
            </View>
            {currentQueueState.status === 'queued' ? (
              <>
                <PillButton
                  label="Try sending again"
                  variant="coral"
                  minHeight={52}
                  onPress={() => user?.id && refreshQueue(user.id)}
                />
                <PillButton
                  label="Clear pending links from this device"
                  variant="outline"
                  minHeight={52}
                  onPress={async () => {
                    const ownerId = user?.id;
                    if (!ownerId) return;
                    await clearQueuedShares(ownerId);
                    if (currentUserId.current === ownerId) {
                      setQueueState({
                        ownerId,
                        status: 'cleared',
                        message: 'Pending links were removed from this device. Nothing was sent by this action.',
                      });
                    }
                  }}
                />
              </>
            ) : null}
            <PillButton
              label="Done"
              variant="outline"
              minHeight={52}
              onPress={() => router.replace('/(app)/home')}
            />
          </View>
        ) : (
          <View style={{ gap: space.md }}>
            <View style={surfaceStyle}>
              <Text style={bodyStyle}>
                {hasShareIntent
                  ? 'This share does not include a supported web link.'
                  : 'Open a supported app and use its Share action to send a web link here.'}
              </Text>
            </View>
            {hasShareIntent ? (
              <PillButton label="Dismiss share" variant="outline" minHeight={52} onPress={dismissShare} />
            ) : null}
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const surfaceStyle = {
  padding: space.lg,
  borderWidth: border.hairline,
  borderColor: color.ink,
  borderRadius: radius.md,
  backgroundColor: color.white,
} as const;

const bodyStyle = {
  fontFamily: font.body,
  fontSize: 16,
  lineHeight: 24,
  color: color.ink,
} as const;

const labelStyle = {
  fontFamily: font.body,
  fontSize: 14,
  lineHeight: 20,
  fontWeight: '700' as const,
  color: color.ink,
};
