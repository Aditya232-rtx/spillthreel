import { useEffect, useRef, useState } from 'react';
import {
  Image,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Animated, {
  FadeIn,
  FadeOut,
  SlideInLeft,
  SlideOutLeft,
  ZoomIn,
} from 'react-native-reanimated';
import { Svg, Line, Path, Polyline, Rect } from 'react-native-svg';
import { SUGGESTION_CHIPS, type ChatErrorKind, type ChatMessage } from '@/data/chat';
import { ApiError, search, type SearchCard, type SearchCitation } from '@/lib/api';
import { alpha, color, font, radius, space } from '@/theme/tokens';

interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  messages: ChatMessage[];
}

const HISTORY_KEY = 'spillthereel.chat_history.v2';
const LEGACY_HISTORY_KEY = 'spillthereel.chat_history.v1';
const MAX_STORED_MESSAGES = 50;
/** Backend contract: query is 1..500 chars — clamp client-side, never 400. */
const MAX_QUERY_LENGTH = 500;
/** Citation flash duration (ms) when tapping an inline [n] marker. */
const FLASH_MS = 1400;
/** Suggestion-chip double-tap guard (ms). */
const CHIP_DEBOUNCE_MS = 300;
/** Local fallback when a card has no thumbnail or remote load fails. */
const CARD_PLACEHOLDER = require('../../assets/images/chat-ui-libraries.png');

let nextId = 1;
function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${nextId++}`;
}

function freshConversation(): Conversation {
  return { id: newId('conv'), title: 'New chat', updatedAt: Date.now(), messages: [] };
}

function conversationTitle(firstQuery: string): string {
  const trimmed = firstQuery.trim().replace(/\s+/g, ' ');
  return trimmed.length > 34 ? `${trimmed.slice(0, 34)}…` : trimmed;
}

/**
 * v1 → v2 history migration. v1 messages carry no cards[]/citations[] and may
 * carry the legacy require()-image `card` — drop ONLY that legacy field and
 * keep text. v2 messages already carry backend cards[]/citations[] — rehydrate
 * them with shape validation. Unknown shapes degrade to an empty-text bubble
 * rather than crashing load. Stored v2 without cards stays text-only
 * (backward compatible).
 */
function isSearchCard(raw: unknown): raw is SearchCard {
  const c = (typeof raw === 'object' && raw !== null ? raw : {}) as Partial<SearchCard>;
  return (
    typeof c.item_id === 'string' &&
    typeof c.title === 'string' &&
    typeof c.platform === 'string' &&
    typeof c.deep_link === 'string' &&
    typeof c.score === 'number'
  );
}

function isSearchCitation(raw: unknown): raw is SearchCitation {
  const c = (typeof raw === 'object' && raw !== null ? raw : {}) as Partial<SearchCitation>;
  return typeof c.item_id === 'string' && typeof c.index === 'number';
}

function migrateMessage(raw: unknown): ChatMessage {
  const m = (typeof raw === 'object' && raw !== null ? raw : {}) as Partial<ChatMessage> & {
    cards?: unknown;
    citations?: unknown;
  };
  const msg: ChatMessage = {
    id: typeof m.id === 'string' ? m.id : newId('msg'),
    isUser: m.isUser === true,
    text: typeof m.text === 'string' ? m.text : '',
  };
  // v2 rehydration — validated, so a corrupt entry can't crash render.
  if (Array.isArray(m.cards)) {
    const cards = (m.cards as unknown[]).filter(isSearchCard);
    if (cards.length > 0) msg.cards = cards;
  }
  if (Array.isArray(m.citations)) {
    const citations = (m.citations as unknown[]).filter(isSearchCitation);
    if (citations.length > 0) msg.citations = citations;
  }
  if (m.error === 'auth' || m.error === 'retryable' || m.error === 'generic') {
    msg.error = m.error;
  }
  if (typeof m.retryQuery === 'string') {
    msg.retryQuery = m.retryQuery;
  }
  // NOTE: legacy `card` (require() image) is intentionally never copied —
  // the only field this migration strips.
  return msg;
}

function migrateConversations(raw: unknown): Conversation[] | null {
  if (!Array.isArray(raw)) return null;
  const convs = (raw as Partial<Conversation>[])
    .filter((c) => c && typeof c.id === 'string' && Array.isArray(c.messages))
    .map((c) => ({
      id: c.id as string,
      title: typeof c.title === 'string' ? c.title : 'New chat',
      updatedAt: typeof c.updatedAt === 'number' ? c.updatedAt : Date.now(),
      messages: (c.messages as unknown[]).map(migrateMessage),
    }));
  return convs.length > 0 ? convs : null;
}

function errorKind(err: unknown): ChatErrorKind {
  if (err instanceof ApiError) {
    if (err.status === 401) return 'auth';
    if (err.status === 503 || err.status === 504) return 'retryable';
  }
  // Default-timeout aborts surface as DOMException 'TimeoutError' (or a
  // generic Error with the same name on runtimes without DOMException).
  // Timeouts are transient — offer Retry, same as 503/504.
  const name = err instanceof DOMException ? err.name : (err as { name?: unknown })?.name;
  if (name === 'TimeoutError') return 'retryable';
  return 'generic';
}

function errorText(err: unknown): string {
  switch (errorKind(err)) {
    case 'auth':
      // api.ts already signed out locally; AuthGate routes to welcome.
      return 'Your session expired — please sign in again to keep searching.';
    case 'retryable':
      return 'Search is busy right now — try again in a moment.';
    default:
      return 'Something went wrong with search — try again.';
  }
}

/**
 * Open a backend deep_link. `app://item/{id}` routes in-app; anything else
 * goes to the OS. Parsed defensively — unrecognized input is ignored.
 */
function useDeepLinkOpener(): (deepLink: string | undefined) => void {
  const router = useRouter();
  return (deepLink: string | undefined) => {
    if (!deepLink) return;
    const target = deepLink.trim();
    const appMatch = /^app:\/\/item\/([^\s/?#]+)/.exec(target);
    if (appMatch) {
      const id = decodeURIComponent(appMatch[1]);
      // /(app)/item/[id] lands with the item-detail screen (parallel work);
      // cast because typedRoutes only knows routes that exist today.
      router.push(`/(app)/item/${id}` as never);
      return;
    }
    Linking.openURL(target).catch(() => {
      // Unsupported scheme or no handler — stay on chat, no crash.
    });
  };
}

/** Assistant text with tappable [n] citation markers (nested Text onPress). */
function AssistantText({
  text,
  onCite,
}: {
  text: string;
  onCite: (n: number) => void;
}) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <Text style={{ fontFamily: font.body, fontSize: 14.5, lineHeight: 21, color: color.cream }}>
      {parts.map((part, i) => {
        const marker = /^\[(\d+)\]$/.exec(part);
        if (!marker) {
          return <Text key={i}>{part}</Text>;
        }
        return (
          <Text
            key={i}
            onPress={() => onCite(Number(marker[1]))}
            style={{ textDecorationLine: 'underline', color: color.coral }}
          >
            {part}
          </Text>
        );
      })}
    </Text>
  );
}

/** One backend hit card — same look as the legacy card, remote thumbnail. */
function ResultCard({
  card,
  flashed,
  onOpen,
}: {
  card: SearchCard;
  flashed: boolean;
  onOpen: () => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  const platform = card.platform ? card.platform.toUpperCase() : 'APP';
  return (
    <View
      style={{
        backgroundColor: color.chatCard,
        borderWidth: flashed ? 2.5 : 1.5,
        borderColor: flashed ? color.coral : alpha(color.cream, 0.15),
        borderRadius: 20,
        overflow: 'hidden',
        width: 210,
      }}
    >
      {card.thumbnail_url && !imgFailed ? (
        <Image
          source={{ uri: card.thumbnail_url }}
          onError={() => setImgFailed(true)}
          style={{ height: 220, width: '100%' }}
          resizeMode="cover"
        />
      ) : (
        <Image source={CARD_PLACEHOLDER} style={{ height: 220, width: '100%' }} resizeMode="cover" />
      )}
      <View style={{ padding: space.md, gap: space.sm }}>
        <Text style={{ fontFamily: font.display, fontSize: 13, color: color.cream }} numberOfLines={2}>
          {card.title}
        </Text>
        {card.creator ? (
          <Text style={{ fontFamily: font.body, fontSize: 11.5, lineHeight: 16, color: alpha(color.cream, 0.55) }} numberOfLines={2}>
            {card.creator}
          </Text>
        ) : null}
        <Pressable
          onPress={onOpen}
          accessibilityRole="button"
          accessibilityLabel={`Open ${card.title}`}
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
            backgroundColor: color.cream,
            borderRadius: radius.pill,
            paddingVertical: 9,
            marginTop: 4,
          }}
        >
          <Text style={{ fontFamily: font.display, fontSize: 11.5, color: color.ink }}>↗ WATCH ON {platform}</Text>
        </Pressable>
      </View>
    </View>
  );
}

export default function ChatScreen() {
  const router = useRouter();
  const [conversations, setConversations] = useState<Conversation[]>([freshConversation()]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [thinkingFor, setThinkingFor] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [listenSecs, setListenSecs] = useState(0);
  const [voiceHint, setVoiceHint] = useState(false);

  const scrollRef = useRef<ScrollView>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const chipGuardRef = useRef(0);
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [flashCardId, setFlashCardId] = useState<string | null>(null);
  const openDeepLink = useDeepLinkOpener();
  const listenTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recognitionRef = useRef<any>(null);
  const transcriptRef = useRef('');
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const active = conversations.find((c) => c.id === activeId) ?? conversations[0];
  const messages = active?.messages ?? [];
  const hasText = input.trim().length > 0;

  // Load persisted history once; ensure one active conversation.
  // v2 key wins; absent v2, migrate v1 (drop card fields, keep text).
  useEffect(() => {
    AsyncStorage.getItem(HISTORY_KEY)
      .then((raw) => {
        if (raw) {
          const convs = migrateConversations(JSON.parse(raw));
          if (convs) {
            setConversations(convs);
            setActiveId(convs[0].id);
            return;
          }
        }
        return AsyncStorage.getItem(LEGACY_HISTORY_KEY).then((legacyRaw) => {
          if (!legacyRaw) return;
          const convs = migrateConversations(JSON.parse(legacyRaw));
          if (convs) {
            setConversations(convs);
            setActiveId(convs[0].id);
          }
        });
      })
      .catch(() => {
        // corrupted history — start fresh
      });
    return () => {
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
      if (listenTimerRef.current) clearInterval(listenTimerRef.current);
      if (hintTimerRef.current) clearTimeout(hintTimerRef.current);
      if (recognitionRef.current) {
        try {
          recognitionRef.current.stop();
        } catch {
          // no-op
        }
      }
    };
  }, []);

  // Persist history (bounded per conversation).
  useEffect(() => {
    const trimmed = conversations.map((c) => ({
      ...c,
      messages: c.messages.slice(-MAX_STORED_MESSAGES),
    }));
    AsyncStorage.setItem(HISTORY_KEY, JSON.stringify(trimmed)).catch(() => {
      // storage full or unavailable — chat still works in memory
    });
  }, [conversations]);

  function appendMessage(convId: string, msg: ChatMessage, title?: string): void {
    setConversations((prev) =>
      prev.map((c) =>
        c.id === convId
          ? {
              ...c,
              title: title ?? c.title,
              updatedAt: Date.now(),
              messages: [...c.messages, msg],
            }
          : c,
      ),
    );
  }

  async function sendMessage(raw?: string): Promise<void> {
    const trimmed = (raw ?? input).trim();
    if (!trimmed || thinkingFor || !active) return;
    const convId = active.id;
    const isFirst = active.messages.length === 0;
    const query = trimmed.slice(0, MAX_QUERY_LENGTH);
    appendMessage(
      convId,
      { id: newId('msg'), isUser: true, text: query },
      isFirst ? conversationTitle(query) : undefined,
    );
    setInput('');
    setThinkingFor(convId);
    requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
    // Cancel any previous in-flight search before starting a new one.
    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    try {
      const res = await search(query, undefined, { signal: controller.signal });
      if (controller.signal.aborted) return;
      // Backend text verbatim when provided; honest fallback when empty —
      // the suggestion chips below cover the no-match recovery path.
      const text =
        res.text && res.text.trim().length > 0
          ? res.text
          : 'No matches in your saves — try different words, or tap a suggestion below.';
      appendMessage(convId, {
        id: newId('msg'),
        isUser: false,
        text,
        cards: res.cards ?? [],
        citations: res.citations ?? [],
      });
    } catch (err) {
      // Client-side cancel from new chat / conversation switch — no bubble.
      if (controller.signal.aborted) return;
      appendMessage(convId, {
        id: newId('msg'),
        isUser: false,
        text: errorText(err),
        error: errorKind(err),
        retryQuery: query,
      });
    } finally {
      if (searchAbortRef.current === controller) searchAbortRef.current = null;
      setThinkingFor((current) => (current === convId ? null : current));
    }
  }

  /** Abort in-flight search; called on new chat / conversation switch. */
  function cancelInFlight(): void {
    searchAbortRef.current?.abort();
    searchAbortRef.current = null;
    setThinkingFor(null);
  }

  /** Tapping an inline [n] flashes the referenced card (no new deps). */
  function flashCitation(msg: ChatMessage, n: number): void {
    const itemId = msg.citations?.find((c) => c.index === n)?.item_id;
    if (!itemId || !msg.cards?.some((c) => c.item_id === itemId)) return;
    setFlashCardId(itemId);
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    flashTimerRef.current = setTimeout(() => setFlashCardId(null), FLASH_MS);
    requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
  }

  function startNewChat(): void {
    cancelInFlight();
    const conv = freshConversation();
    setConversations((prev) => [conv, ...prev]);
    setActiveId(conv.id);
    setInput('');
    setDrawerOpen(false);
  }

  function openConversation(id: string): void {
    cancelInFlight();
    setActiveId(id);
    setInput('');
    setDrawerOpen(false);
  }

  /** Suggestion chips with a 300ms double-tap guard. */
  function sendSuggestion(chip: string): void {
    const now = Date.now();
    if (now - chipGuardRef.current < CHIP_DEBOUNCE_MS) return;
    chipGuardRef.current = now;
    void sendMessage(chip.replace(/^[^\s]+\s/, ''));
  }

  function deleteConversation(id: string): void {
    setConversations((prev) => {
      const remaining = prev.filter((c) => c.id !== id);
      if (remaining.length === 0) {
        const conv = freshConversation();
        setActiveId(conv.id);
        return [conv];
      }
      if (id === activeId) {
        setActiveId(remaining[0].id);
      }
      return remaining;
    });
  }

  // ---- Hold-to-talk (mic) ----
  function startListening(): void {
    if (listening || thinkingFor) return;
    transcriptRef.current = '';
    setListenSecs(0);
    setListening(true);
    listenTimerRef.current = setInterval(() => setListenSecs((s) => s + 1), 1000);
    // Web Speech API works in desktop/mobile browsers. Native needs a
    // speech-recognition module (dev build only — not Expo Go), so on
    // native the hold gesture runs the UI but yields no transcript.
    // TODO: wire a native STT module (e.g. expo-speech-recognition) once
    // we move off Expo Go onto dev builds.
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      const SR =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SR) {
        try {
          const rec = new SR();
          rec.lang = 'en-US';
          rec.interimResults = true;
          rec.onresult = (e: any) => {
            let t = '';
            for (let i = 0; i < e.results.length; i++) {
              t += e.results[i][0].transcript;
            }
            transcriptRef.current = t;
          };
          rec.onerror = () => {};
          rec.start();
          recognitionRef.current = rec;
        } catch {
          recognitionRef.current = null;
        }
      }
    }
  }

  function stopListeningAndSend(): void {
    if (!listening) return;
    if (listenTimerRef.current) {
      clearInterval(listenTimerRef.current);
      listenTimerRef.current = null;
    }
    const rec = recognitionRef.current;
    recognitionRef.current = null;
    if (rec) {
      try {
        rec.stop();
      } catch {
        // no-op
      }
    }
    setListening(false);
    // Give the recognizer a beat to flush its final result.
    setTimeout(() => {
      const said = transcriptRef.current.trim();
      transcriptRef.current = '';
      if (said) {
        sendMessage(said);
      } else if (Platform.OS !== 'web') {
        setVoiceHint(true);
        if (hintTimerRef.current) clearTimeout(hintTimerRef.current);
        hintTimerRef.current = setTimeout(() => setVoiceHint(false), 3000);
      }
    }, 250);
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.chatBg }}>
      {/* Header: back | title | spacer (new chats start from the history drawer) */}
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 18, paddingTop: 54, paddingBottom: space.md }}>
        <Pressable
          onPress={() => router.replace('/(app)/home')}
          accessibilityLabel="Back to home"
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
          {/* Points shifted ~1.5 left of geometric center: a chevron's
              mass sits in its arms, so true-center coordinates read as
              right-shifted. Optical correction. */}
          <Svg width={19} height={19} viewBox="0 0 24 24">
            <Polyline points="16.5,1.5 3.5,12 16.5,22.5" fill="none" stroke={color.cream} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
          </Svg>
        </Pressable>
        {/* NOTE: no fontWeight here — MyLove ships a single weight and any
            weight value makes Android fall back to the system font. */}
        <Text style={{ flex: 1, fontFamily: font.script, fontSize: 30, color: color.cream, textAlign: 'center' }}>
          Spillthereel
        </Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView
        ref={scrollRef}
        onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}
        contentContainerStyle={{ paddingHorizontal: 18, paddingBottom: 122, gap: space.lg, flexGrow: messages.length === 0 ? 1 : undefined }}
        style={{ flex: 1 }}
      >
        {messages.length === 0 && !thinkingFor ? (
          <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', gap: space.sm, paddingBottom: 40 }}>
            {/* NOTE: Oswald ships without an italic file — fontStyle italic
                is synthesized. If the emulator ever falls back to system
                sans here, that's the renderer, not the style. */}
            <Text style={{ fontFamily: font.displayMedium, fontStyle: 'italic', fontSize: 22, lineHeight: 30, color: alpha(color.cream, 0.9), textAlign: 'center', paddingHorizontal: 24 }}>
              Nothing is ever truly lost.
            </Text>
            <Text style={{ fontFamily: font.body, fontSize: 12.5, color: alpha(color.cream, 0.55), textAlign: 'center' }}>
              Your saves become searchable answers here.
            </Text>
          </View>
        ) : null}

        {messages.map((msg) =>
          msg.isUser ? (
            <View
              key={msg.id}
              style={{
                maxWidth: '78%',
                alignSelf: 'flex-end',
                backgroundColor: alpha(color.cream, 0.1),
                borderRadius: 20,
                padding: space.md,
              }}
            >
              <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.cream }}>{msg.text}</Text>
            </View>
          ) : (
            <View key={msg.id} style={{ flexDirection: 'row', gap: space.sm, alignItems: 'flex-start', maxWidth: '92%' }}>
              <View
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: radius.pill,
                  borderWidth: 1.5,
                  borderColor: alpha(color.cream, 0.4),
                  alignItems: 'center',
                  justifyContent: 'center',
                  marginTop: 2,
                }}
              >
                <Text style={{ fontSize: 9, color: color.cream }}>▶</Text>
              </View>
              <View style={{ gap: space.md, flexShrink: 1 }}>
                <AssistantText text={msg.text} onCite={(n) => flashCitation(msg, n)} />
                {msg.cards && msg.cards.length > 0 ? (
                  <View style={{ gap: space.sm }}>
                    {msg.cards.map((card) => (
                      <ResultCard
                        key={card.item_id}
                        card={card}
                        flashed={flashCardId === card.item_id}
                        onOpen={() => openDeepLink(card.deep_link)}
                      />
                    ))}
                  </View>
                ) : null}
                {msg.error ? (
                  <Pressable
                    onPress={() => void sendMessage(msg.retryQuery ?? '')}
                    accessibilityRole="button"
                    accessibilityLabel="Retry search"
                    style={{
                      alignSelf: 'flex-start',
                      backgroundColor: alpha(color.cream, 0.12),
                      borderWidth: 1,
                      borderColor: alpha(color.cream, 0.25),
                      borderRadius: radius.pill,
                      paddingHorizontal: space.md + 2,
                      paddingVertical: space.sm,
                    }}
                  >
                    <Text style={{ fontFamily: font.display, fontSize: 11, color: color.cream }}>↻ RETRY</Text>
                  </Pressable>
                ) : null}
              </View>
            </View>
          ),
        )}

        {thinkingFor && thinkingFor === active?.id ? (
          <Animated.View entering={FadeIn.duration(180)} style={{ flexDirection: 'row', gap: 5, alignItems: 'center', paddingLeft: 36 }}>
            {[0, 1, 2].map((i) => (
              <Animated.View
                key={i}
                entering={FadeIn.delay(i * 140).duration(200)}
                style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: alpha(color.cream, 0.65) }}
              />
            ))}
          </Animated.View>
        ) : null}

        {/* Minimal single-line chip row — fixed height so chips never stretch. */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={{ flexGrow: 0, maxHeight: 40 }}
          contentContainerStyle={{ gap: space.sm, marginTop: space.xs, marginBottom: 2, alignItems: 'center', paddingRight: 18 }}
        >
          {SUGGESTION_CHIPS.map((chip) => (
            <Pressable
              key={chip}
              onPress={() => sendSuggestion(chip)}
              style={{
                backgroundColor: alpha(color.cream, 0.1),
                borderWidth: 1,
                borderColor: alpha(color.cream, 0.25),
                borderRadius: radius.pill,
                paddingHorizontal: space.md + 2,
                paddingVertical: space.sm,
              }}
            >
              <Text style={{ fontFamily: font.display, fontSize: 11, color: color.cream }}>{chip}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </ScrollView>

      {/* Composer: hamburger (history) | input pill with mic/send morph.
          Hamburger and pill share one 52px row height so nothing misaligns. */}
      <View style={{ position: 'absolute', left: 20, right: 20, bottom: 26, flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <Pressable
          onPress={() => setDrawerOpen(true)}
          accessibilityLabel="Open chat history"
          hitSlop={6}
          style={{
            width: 52,
            height: 52,
            borderRadius: radius.pill,
            backgroundColor: alpha(color.cream, 0.12),
            borderWidth: 1.5,
            borderColor: alpha(color.cream, 0.25),
            alignItems: 'center',
            justifyContent: 'center',
            gap: 4.5,
          }}
        >
          <View style={{ width: 17, height: 2, borderRadius: 1, backgroundColor: color.cream }} />
          <View style={{ width: 17, height: 2, borderRadius: 1, backgroundColor: color.cream }} />
          <View style={{ width: 17, height: 2, borderRadius: 1, backgroundColor: color.cream }} />
        </Pressable>
        <View style={{ flex: 1 }}>
        {voiceHint ? (
          <Animated.View entering={FadeIn.duration(180)} exiting={FadeOut.duration(180)}>
            <Text style={{ fontFamily: font.body, fontSize: 11.5, color: alpha(color.cream, 0.7), textAlign: 'center', marginBottom: 8 }}>
              Voice typing works on web — on this build, type instead.
            </Text>
          </Animated.View>
        ) : null}
        <View
          style={{
            flex: 1,
            flexDirection: 'row',
            alignItems: 'center',
            gap: space.sm,
            minHeight: 52,
            backgroundColor: listening ? alpha(color.coral, 0.18) : alpha(color.cream, 0.12),
            borderWidth: 1.5,
            borderColor: listening ? color.coral : alpha(color.cream, 0.25),
            borderRadius: radius.pill,
            paddingLeft: space.lg,
            paddingRight: 8,
            paddingVertical: 6,
          }}
        >
          {listening ? (
            <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
              <Animated.View entering={FadeIn.duration(200)}>
                <View style={{ width: 9, height: 9, borderRadius: 5, backgroundColor: color.coral }} />
              </Animated.View>
              <Text style={{ flex: 1, fontFamily: font.body, fontSize: 14, color: color.cream }}>
                Listening… {listenSecs}s — release to send
              </Text>
            </View>
          ) : (
            <TextInput
              value={input}
              onChangeText={setInput}
              onSubmitEditing={() => void sendMessage()}
              placeholder="Ask something to start"
              placeholderTextColor={alpha(color.cream, 0.55)}
              style={{ flex: 1, fontFamily: font.body, fontSize: 14, color: color.cream, paddingVertical: 0, textAlignVertical: 'center' }}
            />
          )}
          {hasText && !listening ? (
            <Animated.View key="send" entering={ZoomIn.duration(160)}>
              <Pressable
                onPress={() => void sendMessage()}
                accessibilityLabel="Send message"
                hitSlop={6}
                style={{ width: 32, height: 32, borderRadius: radius.pill, backgroundColor: color.coral, alignItems: 'center', justifyContent: 'center' }}
              >
                <Text style={{ fontSize: 15, color: color.white, lineHeight: 18 }}>→</Text>
              </Pressable>
            </Animated.View>
          ) : (
            <Animated.View key="mic" entering={ZoomIn.duration(160)}>
              <Pressable
                onPressIn={startListening}
                onPressOut={stopListeningAndSend}
                accessibilityLabel="Hold to speak, release to send"
                hitSlop={6}
                style={{ width: 36, height: 36, borderRadius: radius.pill, backgroundColor: color.coral, alignItems: 'center', justifyContent: 'center' }}
              >
                <Svg width={17} height={17} viewBox="0 0 24 24">
                  <Rect x={9} y={2} width={6} height={11} rx={3} fill={color.white} />
                  <Path d="M6 11a6 6 0 0 0 12 0" fill="none" stroke={color.white} strokeWidth={2} strokeLinecap="round" />
                  <Line x1={12} y1={17} x2={12} y2={21} stroke={color.white} strokeWidth={2} strokeLinecap="round" />
                  <Line x1={9} y1={21} x2={15} y2={21} stroke={color.white} strokeWidth={2} strokeLinecap="round" />
                </Svg>
              </Pressable>
            </Animated.View>
          )}
        </View>
        </View>
      </View>

      {/* History drawer */}
      {drawerOpen ? (
        <View style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, flexDirection: 'row', zIndex: 10 }}>
          <Animated.View entering={FadeIn.duration(200)} exiting={FadeOut.duration(200)} style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, backgroundColor: 'rgba(0,0,0,0.55)' }}>
            <Pressable style={{ flex: 1 }} onPress={() => setDrawerOpen(false)} />
          </Animated.View>
          <Animated.View
            entering={SlideInLeft.duration(240)}
            exiting={SlideOutLeft.duration(200)}
            style={{ width: '78%', maxWidth: 300, backgroundColor: color.chatCard, borderRightWidth: 1.5, borderRightColor: alpha(color.cream, 0.15), paddingTop: 60, paddingHorizontal: 18, paddingBottom: 30, gap: space.sm }}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: space.sm }}>
              <Text style={{ fontFamily: font.display, fontSize: 18, color: color.cream }}>CHATS</Text>
              <Pressable onPress={() => setDrawerOpen(false)} accessibilityLabel="Close history" hitSlop={8}>
                <Text style={{ fontSize: 18, color: alpha(color.cream, 0.7) }}>✕</Text>
              </Pressable>
            </View>
            <Pressable
              onPress={startNewChat}
              style={{ backgroundColor: color.coral, borderRadius: radius.pill, paddingVertical: 11, alignItems: 'center', marginBottom: space.sm }}
            >
              <Text style={{ fontFamily: font.display, fontSize: 12.5, color: color.white }}>+ NEW CHAT</Text>
            </Pressable>
            <ScrollView contentContainerStyle={{ gap: 4 }}>
              {conversations.map((conv) => {
                const isActive = conv.id === active?.id;
                return (
                  <View
                    key={conv.id}
                    style={{
                      flexDirection: 'row',
                      alignItems: 'center',
                      backgroundColor: isActive ? alpha(color.cream, 0.12) : 'transparent',
                      borderRadius: 12,
                      paddingVertical: 10,
                      paddingHorizontal: 10,
                    }}
                  >
                    <Pressable onPress={() => openConversation(conv.id)} style={{ flex: 1, gap: 2 }}>
                      <Text numberOfLines={1} style={{ fontFamily: font.body, fontSize: 13.5, color: color.cream }}>
                        {conv.title}
                      </Text>
                      <Text style={{ fontFamily: font.body, fontSize: 10.5, color: alpha(color.cream, 0.5) }}>
                        {new Date(conv.updatedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} · {conv.messages.length} msgs
                      </Text>
                    </Pressable>
                    <Pressable onPress={() => deleteConversation(conv.id)} accessibilityLabel="Delete chat" style={{ padding: 6 }}>
                      <Text style={{ fontSize: 13, color: alpha(color.cream, 0.5) }}>✕</Text>
                    </Pressable>
                  </View>
                );
              })}
            </ScrollView>
          </Animated.View>
        </View>
      ) : null}
    </View>
  );
}
