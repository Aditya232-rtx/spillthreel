import { View } from 'react-native';
import { Slot, useRouter, useSegments } from 'expo-router';
import { TabBar, type TabKey } from '@/components/TabBar';
import { color } from '@/theme/tokens';

const ROUTE_BY_TAB: Record<TabKey, string> = {
  home: '/(app)/home',
  library: '/(app)/library',
  chat: '/(app)/chat',
  profile: '/(app)/profile',
  settings: '/(app)/settings',
};

export default function AppLayout() {
  const router = useRouter();
  const segments = useSegments() as string[];
  const routeSegment = segments[1];
  // Categories remains a reachable sub-screen; item details belong to Library.
  const activeTab: TabKey = routeSegment === 'categories' || routeSegment === 'item'
    ? 'library'
    : ((routeSegment as TabKey | undefined) ?? 'home');
  const isChat = activeTab === 'chat';

  return (
    <View style={{ flex: 1, backgroundColor: color.cream }}>
      <Slot />
      {!isChat ? (
        <TabBar active={activeTab} onChange={(tab) => router.replace(ROUTE_BY_TAB[tab] as never)} />
      ) : null}
    </View>
  );
}
