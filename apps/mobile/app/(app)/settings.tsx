import { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { PillButton } from '@/components/PillButton';
import { SettingsRowView } from '@/components/SettingsRowView';
import { DEFAULT_TOGGLE_STATE, SETTINGS_GROUPS } from '@/data/settings';
import { border, color, font, radius, space } from '@/theme/tokens';

const TOAST_DURATION_MS = 2200;

export default function SettingsScreen() {
  const router = useRouter();
  const [toggles, setToggles] = useState(DEFAULT_TOGGLE_STATE);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), TOAST_DURATION_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  function handleRowPress(groupId: string, rowId: string) {
    if (rowId === 'delete-account') {
      setShowDeleteConfirm(true);
      return;
    }
    if (rowId === 'import') {
      router.push('/(import)/import2');
      return;
    }
    if (rowId === 'sign-out') {
      router.replace('/(auth)/welcome');
      return;
    }
    if (['auto-delete', 'save-ready', 'weekly-digest', 'failed-saves'].includes(rowId)) {
      setToggles((prev) => ({ ...prev, [rowId]: !prev[rowId] }));
      return;
    }
    setToast(`${rowId} tapped`);
  }

  return (
    <View style={{ flex: 1, backgroundColor: color.sage }}>
      <ScrollView contentContainerStyle={{ paddingHorizontal: 18, paddingTop: 54, paddingBottom: 130, gap: space.lg }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text style={{ fontFamily: font.display, fontSize: 30, color: color.ink }}>SETTINGS</Text>
          <Pressable
            onPress={() => router.push('/(app)/profile')}
            style={{ width: 32, height: 32, borderRadius: radius.pill, backgroundColor: color.violet, borderWidth: border.standard, borderColor: color.ink }}
          />
        </View>

        {SETTINGS_GROUPS.map((group) => (
          <View key={group.id} style={{ gap: space.sm }}>
            <Text style={{ fontFamily: font.body, fontSize: 10.5, letterSpacing: 0.6, color: 'rgba(21,23,15,0.55)' }}>{group.eyebrow}</Text>
            <View style={{ gap: space.sm }}>
              {group.rows.map((row) => (
                <SettingsRowView
                  key={row.id}
                  row={row}
                  toggled={toggles[row.id]}
                  onPress={() => handleRowPress(group.id, row.id)}
                />
              ))}
            </View>
          </View>
        ))}

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
        >
          <View
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
            <Text style={{ fontFamily: font.display, fontSize: 20, color: color.ink, textTransform: 'uppercase' }}>Delete account?</Text>
            <Text style={{ fontFamily: font.body, fontSize: 13, color: 'rgba(21,23,15,0.75)' }}>
              This removes every saved reel and cannot be undone.
            </Text>
            <PillButton
              label="Delete my account"
              variant="coral"
              minHeight={46}
              fontSize={14}
              onPress={() => {
                setShowDeleteConfirm(false);
                router.replace('/(auth)/welcome');
              }}
            />
            <PillButton label="Cancel" variant="outline" minHeight={46} fontSize={14} onPress={() => setShowDeleteConfirm(false)} />
          </View>
        </View>
      ) : null}

      {toast ? (
        <View
          style={{
            position: 'absolute',
            left: '50%',
            bottom: 110,
            transform: [{ translateX: -80 }],
            backgroundColor: 'rgba(21,23,15,0.92)',
            borderWidth: border.standard,
            borderColor: color.cream,
            borderRadius: radius.pill,
            paddingHorizontal: space.lg,
            paddingVertical: 10,
          }}
        >
          <Text style={{ fontFamily: font.body, fontSize: 12, letterSpacing: 0.6, color: color.cream }}>{toast}</Text>
        </View>
      ) : null}
    </View>
  );
}
