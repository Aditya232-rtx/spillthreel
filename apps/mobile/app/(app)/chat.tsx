import { useEffect, useRef, useState } from 'react';
import {
  Image,
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
import { SUGGESTION_CHIPS, type ChatMessage } from '@/data/chat';
import { alpha, color, font, radius, space } from '@/theme/tokens';

interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  messages: ChatMessage[];
}

const HISTORY_KEY = 'spillthereel.chat_history.v1';
const MAX_STORED_MESSAGES = 50;

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
  const botTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const listenTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const recognitionRef = useRef<any>(null);
  const transcriptRef = useRef('');
  const hintTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const active = conversations.find((c) => c.id === activeId) ?? conversations[0];
  const messages = active?.messages ?? [];
  const hasText = input.trim().length > 0;

  // Load persisted history once; ensure one active conversation.
  useEffect(() => {
    AsyncStorage.getItem(HISTORY_KEY)
      .then((raw) => {
        if (!raw) return;
        const parsed = JSON.parse(raw) as Conversation[];
        if (Array.isArray(parsed) && parsed.length > 0) {
          setConversations(parsed);
          setActiveId(parsed[0].id);
        }
      })
      .catch(() => {
        // corrupted history — start fresh
      });
    return () => {
      if (botTimerRef.current) clearTimeout(botTimerRef.current);
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

  function sendMessage(raw?: string): void {
    const trimmed = (raw ?? input).trim();
    if (!trimmed || thinkingFor || !active) return;
    const convId = active.id;
    const isFirst = active.messages.length === 0;
    appendMessage(
      convId,
      { id: newId('msg'), isUser: true, text: trimmed },
      isFirst ? conversationTitle(trimmed) : undefined,
    );
    setInput('');
    setThinkingFor(convId);
    requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
    if (botTimerRef.current) clearTimeout(botTimerRef.current);
    botTimerRef.current = setTimeout(() => {
      // Stub answer until the search/RAG backend lands — honest about it
      // so nobody mistakes canned content for real recall.
      appendMessage(convId, {
        id: newId('msg'),
        isUser: false,
        text: `On it — full recall answers plug in once the search backend lands. You asked: “${trimmed}”`,
      });
      setThinkingFor((current) => (current === convId ? null : current));
    }, 1000);
  }

  function startNewChat(): void {
    if (botTimerRef.current) clearTimeout(botTimerRef.current);
    setThinkingFor(null);
    const conv = freshConversation();
    setConversations((prev) => [conv, ...prev]);
    setActiveId(conv.id);
    setInput('');
    setDrawerOpen(false);
  }

  function openConversation(id: string): void {
    setActiveId(id);
    setInput('');
    setDrawerOpen(false);
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
            <Text style={{ fontFamily: font.script, fontSize: 26, color: alpha(color.cream, 0.9), textAlign: 'center' }}>
              Ask your second brain anything.
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
                <Text style={{ fontFamily: font.body, fontSize: 14.5, lineHeight: 21, color: color.cream }}>{msg.text}</Text>
                {msg.card ? (
                  <View style={{ backgroundColor: color.chatCard, borderWidth: 1.5, borderColor: alpha(color.cream, 0.15), borderRadius: 20, overflow: 'hidden', width: 210 }}>
                    <Image source={msg.card.image} style={{ height: 220, width: '100%' }} resizeMode="cover" />
                    <View style={{ padding: space.md, gap: space.sm }}>
                      <Text style={{ fontFamily: font.display, fontSize: 13, color: color.cream }}>{msg.card.creator}</Text>
                      <Text style={{ fontFamily: font.body, fontSize: 11.5, lineHeight: 16, color: alpha(color.cream, 0.55) }}>{msg.card.desc}</Text>
                      <View
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
                        <Text style={{ fontFamily: font.display, fontSize: 11.5, color: color.ink }}>↗ WATCH ON INSTAGRAM</Text>
                      </View>
                    </View>
                  </View>
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
              onPress={() => sendMessage(chip.replace(/^[^\s]+\s/, ''))}
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
              onSubmitEditing={() => sendMessage()}
              placeholder="Ask something to start"
              placeholderTextColor={alpha(color.cream, 0.55)}
              style={{ flex: 1, fontFamily: font.body, fontSize: 14, color: color.cream, paddingVertical: 0, textAlignVertical: 'center' }}
            />
          )}
          {hasText && !listening ? (
            <Animated.View key="send" entering={ZoomIn.duration(160)}>
              <Pressable
                onPress={() => sendMessage()}
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
