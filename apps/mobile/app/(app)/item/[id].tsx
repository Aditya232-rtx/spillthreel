import { useCallback, useEffect, useState } from 'react';
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
import { Svg, Polyline } from 'react-native-svg';
import { Badge } from '@/components/Badge';
import { ApiError, getItem, retryItem, type ItemDetail } from '@/lib/api';
import { alpha, color, font, radius, space } from '@/theme/tokens';

/** Local fallback when the item has no thumbnail or remote load fails. */
const ITEM_PLACEHOLDER = require('../../../assets/images/chat-ui-libraries.png');

type Status = 'loading' | 'ready' | 'not-found' | 'error';

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
  const { id } = useLocalSearchParams<{ id: string }>();
  const [status, setStatus] = useState<Status>('loading');
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

  const load = useCallback(async () => {
    if (!id) {
      setStatus('error');
      return;
    }
    setStatus('loading');
    setRetryNote(null);
    try {
      const detail = await getItem(id);
      setItem(detail);
      setImgFailed(false);
      setStatus('ready');
    } catch (err) {
      setItem(null);
      setStatus(err instanceof ApiError && err.status === 404 ? 'not-found' : 'error');
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!id) {
        if (!cancelled) setStatus('error');
        return;
      }
      if (!cancelled) setStatus('loading');
      setRetryNote(null);
      try {
        const detail = await getItem(id);
        if (cancelled) return;
        setItem(detail);
        setImgFailed(false);
        setStatus('ready');
      } catch (err) {
        if (cancelled) return;
        setItem(null);
        setStatus(err instanceof ApiError && err.status === 404 ? 'not-found' : 'error');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function handleRetry(): Promise<void> {
    if (!id || retryBusy) return;
    setRetryBusy(true);
    setRetryNote(null);
    try {
      await retryItem(id);
      // Retry clears failure_reason server-side — re-fetch for fresh state.
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setRetryNote('Already picked up — pulling the latest state.');
        await load();
      } else {
        setRetryNote("Retry didn't go through — try again.");
      }
    } finally {
      setRetryBusy(false);
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

  return (
    <View style={{ flex: 1, backgroundColor: color.chatBg }}>
      {/* Header: back | title | spacer — same chrome as chat. */}
      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          paddingHorizontal: 18,
          paddingTop: 54,
          paddingBottom: space.md,
        }}
      >
        <Pressable
          onPress={goBack}
          accessibilityRole="button"
          accessibilityLabel="Back"
          hitSlop={8}
          style={{
            width: 40,
            height: 40,
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
        <View style={{ width: 40 }} />
      </View>

      {status === 'loading' ? (
        <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.md }}>
          <ActivityIndicator size="large" color={color.coral} />
          <Text style={{ fontFamily: font.body, fontSize: 13, color: alpha(color.cream, 0.6) }}>
            Pulling up your save…
          </Text>
        </View>
      ) : status === 'not-found' ? (
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
            This save is gone.
          </Text>
          <Text
            style={{
              fontFamily: font.body,
              fontSize: 13,
              lineHeight: 19,
              color: alpha(color.cream, 0.6),
              textAlign: 'center',
            }}
          >
            It was deleted or belongs to another account — nothing to show here.
          </Text>
          <Pressable
            onPress={goBack}
            accessibilityRole="button"
            accessibilityLabel="Go back"
            style={{
              backgroundColor: color.coral,
              borderRadius: radius.pill,
              paddingHorizontal: space.xl,
              paddingVertical: space.md,
              marginTop: space.sm,
            }}
          >
            <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.white }}>
              ← GO BACK
            </Text>
          </Pressable>
        </View>
      ) : status === 'error' || !item ? (
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
              fontSize: 13,
              lineHeight: 19,
              color: alpha(color.cream, 0.6),
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
              paddingHorizontal: space.xl,
              paddingVertical: space.md,
              marginTop: space.sm,
            }}
          >
            <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.white }}>
              ↻ RETRY
            </Text>
          </Pressable>
        </View>
      ) : (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={{
            paddingHorizontal: 18,
            paddingBottom: 130,
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
              fontSize: 12.5,
              color: alpha(color.cream, 0.6),
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
                fontSize: 14.5,
                lineHeight: 21,
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
                  fontSize: 13,
                  lineHeight: 19,
                  color: alpha(color.cream, 0.8),
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
                fontSize: 12.5,
                color: alpha(color.cream, 0.7),
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
              style={{
                flex: 1,
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                backgroundColor: color.cream,
                borderRadius: radius.pill,
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
                style={{
                  flex: 1,
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: retryBusy ? alpha(color.coral, 0.5) : color.coral,
                  borderRadius: radius.pill,
                  paddingVertical: 13,
                }}
              >
                <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.white }}>
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
