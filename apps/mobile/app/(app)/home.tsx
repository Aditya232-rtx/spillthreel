import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Image, Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { Svg, Circle, Line } from 'react-native-svg';
import { Shadowed } from '@/components/Shadowed';
import { AvatarView } from '@/components/AvatarView';
import { useAuth } from '@/hooks/useAuth';
import { getFirstName } from '@/lib/display-name';
import { api } from '@/lib/api';
import {
  dismissVerifyNotice,
  getPendingEmail,
  isVerifyDismissed,
  resendConfirmation,
} from '@/lib/verification';
import { border, color, font, fontSize, radius, space } from '@/theme/tokens';

interface RecentItem {
  id: string;
  source_url: string;
  platform: string;
  state: string;
  saved_at: string;
  title: string | null;
  summary: string | null;
  thumbnail_url: string | null;
  owner_username: string | null;
}

interface ItemsPage {
  items: RecentItem[];
  next_cursor: string | null;
}

type RecentStatus = 'loading' | 'ready' | 'error';

function readableState(state: string): string {
  return state.replace(/_/g, ' ');
}

export default function HomeScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const firstName = getFirstName(user);
  const initial = (firstName[0] ?? 'S').toUpperCase();

  // Verify nudge: signed-in-but-unconfirmed users, or pending signups
  // browsing without a session yet.
  const [pendingEmail, setPendingEmailState] = useState<string | null>(null);
  const needsVerify = (!!user && !user.email_confirmed_at) || (!user && !!pendingEmail);
  const verifyEmail = user?.email ?? pendingEmail;
  const [dismissed, setDismissed] = useState(false);
  const [popupOpen, setPopupOpen] = useState(false);
  const popupShownRef = useRef(false);
  const [resendNote, setResendNote] = useState<string | null>(null);
  const [resending, setResending] = useState(false);

  const [recentItems, setRecentItems] = useState<RecentItem[]>([]);
  const [thumbnailFailures, setThumbnailFailures] = useState<Set<string>>(() => new Set());
  const [recentStatus, setRecentStatus] = useState<RecentStatus>('loading');
  const [recentError, setRecentError] = useState('Could not load your recent saves.');
  const userId = user?.id;

  const loadRecentItems = useCallback(async (signal?: AbortSignal) => {
    if (signal?.aborted) return;
    if (!userId) {
      setRecentItems([]);
      setRecentStatus('ready');
      return;
    }
    setRecentStatus('loading');
    try {
      const page = await api.get<ItemsPage>('/v1/items?limit=8', signal ? { signal } : undefined);
      if (signal?.aborted) return;
      setRecentItems(page.items);
      setThumbnailFailures(new Set());
      setRecentStatus('ready');
    } catch {
      if (signal?.aborted) return;
      setRecentItems([]);
      // Keep provider/database details in diagnostics; the screen gets a safe,
      // recoverable message that does not expose implementation errors.
      setRecentError('Check your connection and try again.');
      setRecentStatus('error');
    }
  }, [userId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadRecentItems(controller.signal);
    return () => controller.abort();
  }, [loadRecentItems]);

  useEffect(() => {
    setDismissed(false);
    setResendNote(null);
    popupShownRef.current = false;
    if (!user) {
      getPendingEmail().then(setPendingEmailState);
    } else {
      setPendingEmailState(null);
      if (!user.email_confirmed_at) {
        isVerifyDismissed(user.id).then(setDismissed);
      }
    }
  }, [user?.id, user?.email_confirmed_at]);

  // Pop the notice at the profile icon on landing (once per mount).
  useEffect(() => {
    if (needsVerify && !dismissed && !popupShownRef.current) {
      popupShownRef.current = true;
      setPopupOpen(true);
    }
    if (!needsVerify) {
      setPopupOpen(false);
    }
  }, [needsVerify, dismissed]);

  async function handleDismiss(): Promise<void> {
    setDismissed(true);
    setPopupOpen(false);
    if (user) {
      await dismissVerifyNotice(user.id);
    }
  }

  async function handleResend(): Promise<void> {
    if (!verifyEmail || resending) return;
    setResending(true);
    const error = await resendConfirmation(verifyEmail);
    setResending(false);
    setResendNote(error ?? 'Fresh link sent — check your inbox.');
  }

  const openLibrary = (): void => {
    router.push('/(app)/library' as never);
  };

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      {popupOpen ? (
        <Pressable
          onPress={() => setPopupOpen(false)}
          accessibilityRole="button"
          accessibilityLabel="Dismiss email confirmation notice"
          style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, zIndex: 20 }}
        />
      ) : null}
      <ScrollView
        style={{ flex: 1, backgroundColor: color.cream }}
        contentContainerStyle={{ paddingHorizontal: space.xl, paddingTop: 54, paddingBottom: 130, gap: space.lg }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text
            style={{
              fontFamily: font.display,
              fontSize: fontSize.displayMd,
              letterSpacing: -0.5,
              color: color.ink,
              textTransform: 'uppercase',
            }}
          >
            HEY,{'\n'}{firstName}.
          </Text>
          <Pressable
            onPress={() => router.push('/(app)/profile')}
            accessibilityRole="button"
            accessibilityLabel="Open profile"
            hitSlop={8}
          >
            <Shadowed offset={3} shadowColor={color.coral} radius={radius.pill}>
              <View>
                <AvatarView user={user} size={44} fallbackLetter={initial} backgroundColor={color.ink} />
                {needsVerify && !dismissed ? (
                  <View
                    style={{
                      position: 'absolute',
                      top: -3,
                      right: -3,
                      width: 14,
                      height: 14,
                      borderRadius: 7,
                      backgroundColor: color.coral,
                      borderWidth: 2,
                      borderColor: color.cream,
                    }}
                  />
                ) : null}
              </View>
            </Shadowed>
          </Pressable>
        </View>

        {popupOpen && needsVerify && !dismissed ? (
          <View
            style={{
              position: 'absolute',
              top: 108,
              right: space.xl,
              width: 250,
              backgroundColor: '#F6E3D3',
              borderWidth: 2,
              borderColor: color.ink,
              borderRadius: radius.md,
              padding: space.md,
              gap: 6,
              zIndex: 30,
            }}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.ink, textTransform: 'uppercase' }}>
                Confirm your email
              </Text>
              <Pressable onPress={handleDismiss} accessibilityRole="button" accessibilityLabel="Dismiss">
                <Text style={{ fontSize: 13, color: 'rgba(21,23,15,0.6)' }}>✕</Text>
              </Pressable>
            </View>
            <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: 'rgba(21,23,15,0.8)' }}>
              Tap the link we sent to {verifyEmail} to unlock your library.
            </Text>
            <Pressable
              onPress={handleResend}
              disabled={resending}
              accessibilityRole="button"
              accessibilityLabel={resending ? 'Sending confirmation link' : 'Resend confirmation link'}
              accessibilityState={{ disabled: resending, busy: resending }}
              style={{ minHeight: 48, justifyContent: 'center' }}
            >
              <Text style={{ fontFamily: font.body, fontSize: 14, color: color.coral, textDecorationLine: 'underline' }}>
                {resending ? 'Sending…' : 'Resend confirmation link'}
              </Text>
            </Pressable>
            {resendNote ? (
              <Text style={{ fontFamily: font.body, fontSize: 13, color: 'rgba(21,23,15,0.8)' }}>
                {resendNote}
              </Text>
            ) : null}
          </View>
        ) : null}

        <Pressable
          onPress={() => router.push('/(app)/chat')}
          accessibilityRole="button"
          accessibilityLabel="Add an item or ask SpillTheReel"
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: space.sm,
            backgroundColor: color.ink,
            borderWidth: border.bold,
            borderColor: color.ink,
            borderRadius: radius.pill,
            paddingLeft: space.xl,
            paddingRight: space.sm,
            minHeight: 56,
          }}
        >
          <Text style={{ flex: 1, fontFamily: font.displaySemibold, fontSize: fontSize.bodyMd, color: color.cream }}>
            Add an item · Ask
          </Text>
          <View style={{ width: 40, height: 40, borderRadius: radius.pill, backgroundColor: color.coral, alignItems: 'center', justifyContent: 'center' }}>
            <Svg width={19} height={19} viewBox="0 0 16 16">
              <Circle cx={7} cy={7} r={5} fill="none" stroke={color.white} strokeWidth={2.2} />
              <Line x1={11} y1={11} x2={15} y2={15} stroke={color.white} strokeWidth={2.2} strokeLinecap="round" />
            </Svg>
          </View>
        </Pressable>

        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <Text style={{ fontFamily: font.display, fontSize: 18, letterSpacing: -0.3, color: color.ink }}>RECENTLY SAVED</Text>
          <Pressable onPress={openLibrary} accessibilityRole="button" accessibilityLabel="See all saved items" hitSlop={8} style={{ minHeight: 48, justifyContent: 'center' }}>
            <Text style={{ fontFamily: font.display, fontSize: 13, color: color.ink, textDecorationLine: 'underline' }}>See all</Text>
          </Pressable>
        </View>

        {recentStatus === 'loading' ? (
          <View accessibilityRole="progressbar" accessibilityLabel="Loading recent saves" style={{ minHeight: 104, alignItems: 'center', justifyContent: 'center', gap: space.sm }}>
            <ActivityIndicator color={color.coral} />
            <Text style={{ fontFamily: font.body, fontSize: 14, color: '#55574F' }}>Loading your recent saves…</Text>
          </View>
        ) : recentStatus === 'error' ? (
          <View style={{ backgroundColor: '#F6E3D3', borderWidth: border.standard, borderColor: color.ink, borderRadius: radius.lg, padding: space.lg, gap: space.sm }}>
            <Text style={{ fontFamily: font.display, fontSize: 16, color: color.ink }}>Your saves didn’t load.</Text>
            <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: '#45473F' }}>{recentError}</Text>
            <Pressable
              onPress={() => void loadRecentItems()}
              accessibilityRole="button"
              accessibilityLabel="Retry loading recent saves"
              style={{ alignSelf: 'flex-start', minHeight: 48, justifyContent: 'center', paddingHorizontal: space.lg, borderRadius: radius.pill, backgroundColor: color.ink }}
            >
              <Text style={{ fontFamily: font.display, fontSize: 13, color: color.cream }}>TRY AGAIN</Text>
            </Pressable>
          </View>
        ) : recentItems.length === 0 ? (
          <View style={{ backgroundColor: '#EDEAE0', borderWidth: border.standard, borderColor: color.ink, borderRadius: radius.lg, padding: space.lg, gap: space.sm }}>
            <Text style={{ fontFamily: font.display, fontSize: 17, color: color.ink }}>Your library starts with one good find.</Text>
            <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: '#45473F' }}>
              Save something you want to remember, then ask SpillTheReel about it anytime.
            </Text>
            <Pressable
              onPress={() => router.push('/(app)/chat')}
              accessibilityRole="button"
              accessibilityLabel="Add your first item"
              style={{ alignSelf: 'flex-start', minHeight: 48, justifyContent: 'center', paddingHorizontal: space.lg, borderRadius: radius.pill, backgroundColor: color.coral }}
            >
              <Text style={{ fontFamily: font.display, fontSize: 13, color: color.ink }}>＋ ADD AN ITEM</Text>
            </Pressable>
          </View>
        ) : (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm, paddingBottom: space.xs }}>
            {recentItems.map((item) => (
              <Pressable
                key={item.id}
                onPress={() => router.push({ pathname: '/(app)/item/[id]', params: { id: item.id } })}
                accessibilityRole="button"
                accessibilityLabel={(item.title || 'Untitled save') + ', ' + item.platform + ', ' + readableState(item.state)}
                style={{ width: 148, borderRadius: radius.lg }}
              >
                <Shadowed offset={3} radius={radius.lg} shadowColor={color.ink}>
                  <View style={{ overflow: 'hidden', backgroundColor: '#EDEAE0', borderWidth: border.standard, borderColor: color.ink, borderRadius: radius.lg }}>
                    {item.thumbnail_url && !thumbnailFailures.has(item.id) ? (
                      <Image
                        source={{ uri: item.thumbnail_url }}
                        style={{ width: '100%', height: 116 }}
                        resizeMode="cover"
                        accessibilityLabel="Saved item thumbnail"
                        onError={() => setThumbnailFailures((current) => new Set(current).add(item.id))}
                      />
                    ) : (
                      <View
                        accessibilityRole="image"
                        accessibilityLabel="Preview unavailable"
                        style={{ height: 116, alignItems: 'center', justifyContent: 'center', backgroundColor: color.sage }}
                      >
                        <Text style={{ fontFamily: font.display, fontSize: 12, color: color.ink }}>PREVIEW UNAVAILABLE</Text>
                      </View>
                    )}
                    <View style={{ padding: space.sm, gap: 3 }}>
                      <Text numberOfLines={2} style={{ minHeight: 38, fontFamily: font.body, fontSize: 14, lineHeight: 19, color: color.ink }}>
                        {item.title || 'Untitled save'}
                      </Text>
                      <Text numberOfLines={1} style={{ fontFamily: font.body, fontSize: 12, color: '#55574F' }}>
                        {item.platform.toUpperCase()} · {readableState(item.state)}
                      </Text>
                    </View>
                  </View>
                </Shadowed>
              </Pressable>
            ))}
          </ScrollView>
        )}
      </ScrollView>
    </View>
  );
}
