import { useState } from 'react';
import { Image, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { useRouter } from 'expo-router';
import { INITIAL_MESSAGES, SUGGESTION_CHIPS, type ChatMessage } from '@/data/chat';
import { alpha, color, font, radius, space } from '@/theme/tokens';

let nextId = 100;

export default function ChatScreen() {
  const router = useRouter();
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [input, setInput] = useState('');

  function sendMessage() {
    const trimmed = input.trim();
    if (!trimmed) return;
    const userMessage: ChatMessage = { id: String(nextId++), isUser: true, text: trimmed };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.chatBg }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: 18, paddingTop: 54, paddingBottom: space.md }}>
        <Pressable
          onPress={() => router.replace('/(app)/home')}
          style={{
            width: 38,
            height: 38,
            borderRadius: radius.pill,
            backgroundColor: alpha(color.cream, 0.1),
            borderWidth: 1.5,
            borderColor: alpha(color.cream, 0.25),
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text style={{ color: color.cream, fontSize: 17 }}>←</Text>
        </Pressable>
        <Text style={{ flex: 1, fontFamily: font.script, fontWeight: '700', fontSize: 28, color: color.cream, textAlign: 'center' }}>
          Spillthereel
        </Text>
        <View style={{ width: 38 }} />
      </View>

      <ScrollView contentContainerStyle={{ paddingHorizontal: 18, paddingBottom: 110, gap: space.lg }} style={{ flex: 1 }}>
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

        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.sm, marginTop: space.xs }}>
          {SUGGESTION_CHIPS.map((chip) => (
            <Pressable
              key={chip}
              onPress={() => setInput(chip.replace(/^[^\s]+\s/, ''))}
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

      <View style={{ position: 'absolute', left: 20, right: 20, bottom: 26, flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <View
          style={{
            width: 46,
            height: 46,
            borderRadius: radius.pill,
            backgroundColor: alpha(color.cream, 0.12),
            borderWidth: 1.5,
            borderColor: alpha(color.cream, 0.25),
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text style={{ fontSize: 20, color: color.cream }}>+</Text>
        </View>
        <View
          style={{
            flex: 1,
            flexDirection: 'row',
            alignItems: 'center',
            gap: space.sm,
            backgroundColor: alpha(color.cream, 0.12),
            borderWidth: 1.5,
            borderColor: alpha(color.cream, 0.25),
            borderRadius: radius.pill,
            paddingLeft: space.lg,
            paddingRight: space.sm,
            paddingVertical: 11,
          }}
        >
          <TextInput
            value={input}
            onChangeText={setInput}
            onSubmitEditing={sendMessage}
            placeholder="Ask something to start"
            placeholderTextColor={alpha(color.cream, 0.55)}
            style={{ flex: 1, fontFamily: font.body, fontSize: 14, color: color.cream }}
          />
          <Pressable
            onPress={sendMessage}
            style={{ width: 30, height: 30, borderRadius: radius.pill, backgroundColor: color.coral, alignItems: 'center', justifyContent: 'center' }}
          >
            <Text style={{ fontSize: 14, color: color.white }}>→</Text>
          </Pressable>
        </View>
      </View>
    </View>
  );
}
