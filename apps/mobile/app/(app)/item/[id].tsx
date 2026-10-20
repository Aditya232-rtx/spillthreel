import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Linking,
  Pressable,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Svg, Polyline } from 'react-native-svg';
import { Badge } from '@/components/Badge';
import { ApiError, getItem, retryItem, type ItemDetail } from '@/lib/api';
import { useAuth } from '@/hooks/useAuth';
import { alpha, color, font, radius, space } from '@/theme/tokens';

/** Local fallback when the item has no thumbnail or remote load fails. */
const ITEM_PLACEHOLDER = require('../../../assets/images/chat-ui-libraries.png');

type Status = 'loading' | 'ready' | 'not-found' | 'error' | 'auth-required';

/** State → sticker badge colors (Badge is the shared sticker pill). */
function badgeColors(state: string): { background: string; textColor: string } {
  switch (state) {
    case 'fully_indexed':
      return { background: color.sage, textColor: color.ink };
    case 'text_indexed':
      return { background: color.gold, textColor: color.ink };
    case 'failed':
      return { background: color.coral, textColor: color.white };
    case 'source_gone':
      return { background: color.cream, textColor: color.ink };
    default:
      // queued / downloading / analyzing / indexing — in-flight.
      return { background: color.violet, textColor: color.white };
  }
}

function badgeLabel(state: string): string {
  return state.replace(/_/g, ' ').toUpperCase();
}

export default function ItemDetailScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  const { id } = useLocalSearchParams<{ id: string }>();
  const activeUserIdRef = useRef(user?.id ?? null);
  activeUserIdRef.current = user?.id ?? null;
  const [status, setStatus] = useState<Status>('loading');
  const [loadedOwnerId, setLoadedOwnerId] = useState<string | null>(null);
  const [item, setItem] = useState<ItemDetail | null>(null);
  const [imgFailed, setImgFailed] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [retryNote, setRetryNote] = useState<string | null>(null);

  const goBack = useCallback(() => {
    if (router.canGoBack()) {
      router.back();
    } else {
      // Cold start (no history) — fall back to chat, the entry point.
      router.replace('/(app)/chat');
    }
  }, [router]);

  const load = useCallback(async (signal?: AbortSignal) => {
    const ownerId = user?.id;
    if (!ownerId) {
      setLoadedOwnerId(null);
      setItem(null);
      setStatus('auth-required');
      return;
    }
    setItem(null);
    setLoadedOwnerId(ownerId);
    if (!id) {
      setStatus('error');
      return;
    }
    if (signal?.aborted) return;
    setRetryBusy(false);
    setStatus('loading');
    setRetryNote(null);
    try {
      const detail = await getItem(id, signal ? { signal } : undefined);
      if (signal?.aborted || activeUserIdRef.current !== ownerId) return;
      setItem(detail);
      setImgFailed(false);
      setStatus('ready');
    } catch (err) {
      if (signal?.aborted || activeUserIdRef.current !== ownerId) return;
      setItem(null);
      setStatus(err instanceof ApiError && err.status === 404 ? 'not-found' : 'error');
    }
  }, [id, user?.id]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => {
      controller.abort();
    };
  }, [load]);

  async function handleRetry(): Promise<void> {
    const ownerId = user?.id;
    if (!id || !ownerId || retryBusy) return;
    setRetryBusy(true);
    setRetryNote(null);
    try {
      await retryItem(id);
      if (activeUserIdRef.current !== ownerId) return;
      // Retry clears failure_reason server-side — re-fetch for fresh state.
      await load();
    } catch (err) {
      if (activeUserIdRef.current !== ownerId) return;
      if (err instanceof ApiError && err.status === 422) {
        setRetryNote('Already picked up — pulling the latest state.');
        await load();
      } else {
        setRetryNote("Retry didn't go through — try again.");
      }
    } finally {
      if (activeUserIdRef.current === ownerId) setRetryBusy(false);
    }
  }

  function openOriginal(): void {
    if (!item?.source_url) return;
    Linking.openURL(item.source_url).catch(() => {
      setRetryNote('Could not open the original link on this device.');
    });
  }

  const creator = item?.owner_username || item?.owner_name || 'Unknown creator';
  const platform = (item?.platform ?? '').toUpperCase() || 'APP';
  const failed = item?.state === 'failed';
  const visibleStatus: Status = !user?.id
    ? 'auth-required'
    : loadedOwnerId === user.id
      ? status
      : 'loading';

  return (
    <View style={{ flex: 1, backgroundColor: color.chatBg }}>
      {/* Header: back | title | spacer — same chrome as chat. */}
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          paddingHorizontal: 18,
          paddingTop: insets.top + space.sm,
          paddingBottom: space.md,
        }}
      >
        <Pressable
          onPress={goBack}
          accessibilityRole="button"
          accessibilityLabel="Back"
          hitSlop={8}
          style={{
            width: 48,
            height: 48,
            borderRadius: radius.pill,
            backgroundColor: alpha(color.cream, 0.1),
            borderWidth: 1.5,
            borderColor: alpha(color.cream, 0.25),
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Svg width={19} height={19} viewBox="0 0 24 24">
            <Polyline
              points="16.5,1.5 3.5,12 16.5,22.5"
              fill="none"
              stroke={color.cream}
              strokeWidth={3}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </Svg>
        </Pressable>
        <Text
          style={{
            flex: 1,
            fontFamily: font.display,
            fontSize: 15,
            letterSpacing: 1,
            color: color.cream,
            textAlign: 'center',
          }}
        >
          SAVED REEL
        </Text>
        <View style={{ width: 48 }} />
      </View>

      {visibleStatus === 'loading' ? (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md }}>
          <ActivityIndicator size="large" color={color.coral} />
          <Text style={{ fontFamily: font.body, fontSize: 15, lineHeight: 22, color: alpha(color.cream, 0.75) }}>
            Pulling up your save…
          </Text>
        </View>
      ) : visibleStatus === 'auth-required' ? (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md, paddingHorizontal: 32 }}>
          <Text style={{ fontFamily: font.displayMedium, fontStyle: 'italic', fontSize: 22, lineHeight: 30, color: color.cream, textAlign: 'center' }}>
            Sign in to open this save.
          </Text>
          <Pressable
            onPress={() => router.replace('/(auth)/login')}
            accessibilityRole="button"
            accessibilityLabel="Sign in to view this save"
            style={{ backgroundColor: color.coral, borderRadius: radius.pill, minHeight: 48, justifyContent: 'center', paddingHorizontal: space.xl }}
          >
            <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.ink }}>SIGN IN</Text>
          </Pressable>
        </View>
      ) : visibleStatus === 'not-found' ? (
        <View
          style={{
            flex: 1,
            alignItems: 'center',
            justifyContent: 'center',
            gap: space.md,
            paddingHorizontal: 32,
          }}
        >
          <Text
            style={{
              fontFamily: font.displayMedium,
              fontStyle: 'italic',
              fontSize: 22,
              lineHeight: 30,
              color: alpha(color.cream, 0.9),
              textAlign: 'center',
            }}
          >
            This save isn&apos;t available.
          </Text>
          <Text
            style={{
              fontFamily: font.body,
              fontSize: 15,
              lineHeight: 22,
              color: alpha(color.cream, 0.75),
              textAlign: 'center',
            }}
          >
            It may have been deleted or is no longer available to this account.
          </Text>
          <Pressable
            onPress={goBack}
            accessibilityRole="button"
            accessibilityLabel="Go back"
            style={{
              backgroundColor: color.coral,
              borderRadius: radius.pill,
              minHeight: 48,
              justifyContent: 'center',
              paddingHorizontal: space.xl,
              paddingVertical: space.md,
              marginTop: space.sm,
            }}
          >
            <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.ink }}>
              ← GO BACK
            </Text>
          </Pressable>
        </View>
      ) : visibleStatus === 'error' || !item ? (
        <View
          style={{
            flex: 1,
            alignItems: 'center',
            justifyContent: 'center',
            gap: space.md,
            paddingHorizontal: 32,
          }}
        >
          <Text
            style={{
              fontFamily: font.displayMedium,
              fontStyle: 'italic',
              fontSize: 22,
              lineHeight: 30,
              color: alpha(color.cream, 0.9),
              textAlign: 'center',
            }}
          >
            Couldn&apos;t load this save.
          </Text>
          <Text
            style={{
              fontFamily: font.body,
              fontSize: 15,
              lineHeight: 22,
              color: alpha(color.cream, 0.75),
              textAlign: 'center',
            }}
          >
            Check your connection and try again.
          </Text>
          <Pressable
            onPress={() => void load()}
            accessibilityRole="button"
            accessibilityLabel="Retry loading item"
            style={{
              backgroundColor: color.coral,
              borderRadius: radius.pill,
              minHeight: 48,
              justifyContent: 'center',
              paddingHorizontal: space.xl,
              paddingVertical: space.md,
              marginTop: space.sm,
            }}
          >
            <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.ink }}>
              ↻ RETRY
            </Text>
          </Pressable>
        </View>
      ) : (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={{
            paddingHorizontal: 18,
            paddingBottom: Math.max(insets.bottom + 36, 48),
            gap: space.md,
          }}
        >
          {/* Thumbnail — same placeholder asset chat cards use. */}
          {item.thumbnail_url && !imgFailed ? (
            <Image
              source={{ uri: item.thumbnail_url }}
              onError={() => setImgFailed(true)}
              style={{ height: 240, width: '100%', borderRadius: 20 }}
              resizeMode="cover"
              accessibilityLabel="Video thumbnail"
            />
          ) : (
            <Image
              source={ITEM_PLACEHOLDER}
              style={{ height: 240, width: '100%', borderRadius: 20 }}
              resizeMode="cover"
            />
          )}

          {/* Title + state badge */}
          <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: space.sm }}>
            <Text
              style={{
                flex: 1,
                fontFamily: font.display,
                fontSize: 20,
                lineHeight: 26,
                color: color.cream,
              }}
            >
              {item.title || 'Untitled save'}
            </Text>
            <Badge
              label={badgeLabel(item.state)}
              rotateDeg={0}
              shadowOffset={0}
              background={badgeColors(item.state).background}
              textColor={badgeColors(item.state).textColor}
            />
          </View>

          {/* Creator + platform row */}
          <Text
            style={{
              fontFamily: font.body,
              fontSize: 14,
              lineHeight: 20,
              color: alpha(color.cream, 0.75),
            }}
            numberOfLines={1}
          >
            {creator} · {platform}
          </Text>

          {/* Summary */}
          {item.summary ? (
            <Text
              style={{
                fontFamily: font.body,
                fontSize: 16,
                lineHeight: 24,
                color: color.cream,
              }}
            >
              {item.summary}
            </Text>
          ) : null}

          {/* Key chips — backend ItemDetail carries hashtags[], no
              categories array, so hashtags are the topic chips. */}
          {item.hashtags && item.hashtags.length > 0 ? (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
              {item.hashtags.map((tag) => (
                <View
                  key={tag}
                  style={{
                    backgroundColor: alpha(color.cream, 0.1),
                    borderWidth: 1,
                    borderColor: alpha(color.cream, 0.25),
                    borderRadius: radius.pill,
                    paddingHorizontal: space.md,
                    paddingVertical: space.sm,
                  }}
                >
                  <Text style={{ fontFamily: font.display, fontSize: 11, color: color.cream }}>
                    #{tag.replace(/^#+/, '')}
                  </Text>
                </View>
              ))}
            </View>
          ) : null}

          {/* Failed state — honest reason when the backend has one. */}
          {failed ? (
            <View
              style={{
                backgroundColor: alpha(color.coral, 0.12),
                borderWidth: 1.5,
                borderColor: alpha(color.coral, 0.5),
                borderRadius: radius.md,
                padding: space.md,
                gap: 4,
              }}
            >
              <Text style={{ fontFamily: font.display, fontSize: 12, color: color.coral }}>
                PROCESSING FAILED
              </Text>
              <Text
                style={{
                  fontFamily: font.body,
                  fontSize: 15,
                  lineHeight: 22,
                  color: alpha(color.cream, 0.9),
                }}
              >
                {item.failure_reason || 'Something went wrong while saving this reel.'}
              </Text>
            </View>
          ) : null}

          {retryNote ? (
            <Text
              style={{
                fontFamily: font.body,
                fontSize: 14,
                lineHeight: 20,
                color: alpha(color.cream, 0.85),
                textAlign: 'center',
              }}
            >
              {retryNote}
            </Text>
          ) : null}

          {/* Action row */}
          <View style={{ flexDirection: 'row', gap: space.sm, marginTop: space.xs }}>
            <Pressable
              onPress={openOriginal}
              accessibilityRole="button"
              accessibilityLabel={`Watch original on ${platform}`}
              accessibilityHint="Opens the original post in its app or browser."
              style={{
                flex: 1,
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                backgroundColor: color.cream,
                borderRadius: radius.pill,
                minHeight: 48,
                paddingVertical: 13,
              }}
            >
              <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.ink }}>
                ↗ WATCH ORIGINAL
              </Text>
            </Pressable>
            {failed ? (
              <Pressable
                onPress={() => void handleRetry()}
                disabled={retryBusy}
                accessibilityRole="button"
                accessibilityLabel="Retry processing"
                accessibilityState={{ disabled: retryBusy, busy: retryBusy }}
                style={{
                  flex: 1,
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: retryBusy ? alpha(color.coral, 0.5) : color.coral,
                  borderRadius: radius.pill,
                  minHeight: 48,
                  paddingVertical: 13,
                }}
              >
                <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.ink }}>
                  {retryBusy ? 'RETRYING…' : '↻ RETRY'}
                </Text>
              </Pressable>
            ) : null}
          </View>
        </ScrollView>
      )}
    </View>
  );
}
