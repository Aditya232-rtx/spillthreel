import { Pressable, Text, View } from 'react-native';
import { alpha, color, radius } from '@/theme/tokens';

export type TabKey = 'home' | 'categories' | 'chat' | 'profile' | 'settings';

interface TabDef {
  key: TabKey;
  icon: string;
}

const TABS: TabDef[] = [
  { key: 'home', icon: '⌂' },
  { key: 'categories', icon: '◎' },
  { key: 'chat', icon: '✦' },
  { key: 'profile', icon: '☰' },
  { key: 'settings', icon: '⚙' },
];

interface TabBarProps {
  active: TabKey;
  onChange: (tab: TabKey) => void;
}

/** Floating glass pill tab bar. Hidden entirely on the Chat screen. */
export function TabBar({ active, onChange }: TabBarProps) {
  return (
    <View
      style={{
        position: 'absolute',
        left: 20,
        right: 20,
        bottom: 26,
        height: 66,
        borderRadius: radius.pill,
        backgroundColor: alpha(color.white, 0.5),
        borderWidth: 1.5,
        borderColor: alpha(color.white, 0.7),
        flexDirection: 'row',
        justifyContent: 'space-around',
        alignItems: 'center',
        shadowColor: color.ink,
        shadowOffset: { width: 0, height: 10 },
        shadowOpacity: 0.18,
        shadowRadius: 30,
        elevation: 8,
      }}
    >
      {TABS.map((tab) => {
        const isActive = tab.key === active;
        const isChat = tab.key === 'chat';
        return (
          <Pressable
            key={tab.key}
            onPress={() => onChange(tab.key)}
            style={{
              width: 46,
              height: 46,
              borderRadius: radius.pill,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: isActive ? (isChat ? color.violet : color.ink) : 'transparent',
            }}
          >
            <Text style={{ fontSize: 20, color: isActive ? color.cream : color.ink }}>{tab.icon}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}
