import { useState } from 'react';
import { Modal, Pressable, Text, View } from 'react-native';
import { PillButton } from '@/components/PillButton';
import { TextField } from '@/components/TextField';
import { USER_CATEGORY_EMOJI_CHOICES } from '@/data/categories';
import { border, color, font, radius, space } from '@/theme/tokens';

interface AddCategoryModalProps {
  visible: boolean;
  onClose: () => void;
  onSubmit: (input: { name: string; emoji: string }) => void | Promise<void>;
}

export function AddCategoryModal({ visible, onClose, onSubmit }: AddCategoryModalProps) {
  const [name, setName] = useState('');
  const [emoji, setEmoji] = useState(USER_CATEGORY_EMOJI_CHOICES[0]);

  async function handleSubmit() {
    if (!name.trim()) return;
    await onSubmit({ name, emoji });
    setName('');
    setEmoji(USER_CATEGORY_EMOJI_CHOICES[0]);
    onClose();
  }

  return (
    <Modal transparent visible={visible} animationType="fade" onRequestClose={onClose}>
      <Pressable
        onPress={onClose}
        style={{
          flex: 1,
          backgroundColor: 'rgba(21,23,15,0.55)',
          alignItems: 'center',
          justifyContent: 'center',
          padding: space.xxl,
        }}
      >
        <Pressable
          onPress={(e) => e.stopPropagation()}
          style={{
            width: '100%',
            backgroundColor: color.cream,
            borderWidth: border.heavy,
            borderColor: color.ink,
            borderRadius: radius.lg,
            padding: space.xxl,
            gap: space.md,
          }}
        >
          <Text
            style={{
              fontFamily: font.display,
              fontSize: 22,
              color: color.ink,
              textTransform: 'uppercase',
              letterSpacing: -0.4,
            }}
          >
            NEW CATEGORY
          </Text>
          <Text style={{ fontFamily: font.body, fontSize: 13, color: 'rgba(21,23,15,0.7)' }}>
            We'll ask our AI to auto-sort your reels into it as they come in.
          </Text>

          <View style={{ gap: space.sm }}>
            <Text style={{ fontFamily: font.body, fontSize: 10.5, letterSpacing: 0.5, color: 'rgba(21,23,15,0.55)' }}>
              NAME
            </Text>
            <TextField
              value={name}
              onChangeText={setName}
              placeholder="e.g. FOOD SPOTS"
              autoCapitalize="characters"
              maxLength={20}
            />
          </View>

          <View style={{ gap: space.sm }}>
            <Text style={{ fontFamily: font.body, fontSize: 10.5, letterSpacing: 0.5, color: 'rgba(21,23,15,0.55)' }}>
              PICK AN EMOJI
            </Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
              {USER_CATEGORY_EMOJI_CHOICES.map((choice) => {
                const active = choice === emoji;
                return (
                  <Pressable
                    key={choice}
                    onPress={() => setEmoji(choice)}
                    style={{
                      width: 44,
                      height: 44,
                      borderRadius: radius.pill,
                      borderWidth: 1.5,
                      borderColor: color.ink,
                      backgroundColor: active ? color.ink : color.cream,
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Text style={{ fontSize: 22 }}>{choice}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>

          <View style={{ gap: space.sm, marginTop: space.sm }}>
            <PillButton
              label="Create category"
              variant="coral"
              minHeight={48}
              fontSize={14}
              disabled={!name.trim()}
              onPress={handleSubmit}
            />
            <PillButton label="Cancel" variant="outline" minHeight={44} fontSize={13} onPress={onClose} />
          </View>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
