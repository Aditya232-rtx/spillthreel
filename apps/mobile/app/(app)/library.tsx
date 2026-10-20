import { useAuth } from '@/hooks/useAuth';
import { api } from '@/lib/api';
import { color, semantic } from '@/theme/tokens';
import { useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useColorScheme,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

type LibraryItem = {
  id: string;
  source_url: string;
  platform: string;
  state: string;
  saved_at: string;
  title: string | null;
  summary: string | null;
  thumbnail_url: string | null;
  owner_username: string | null;
};

type ItemsResponse = { items: LibraryItem[]; next_cursor: string | null; total_count: number };
type Category = {
  id: string;
  name: string;
  source: string;
  emoji: string | null;
  bg_color: string | null;
  item_count: number;
};

const PAGE_SIZE = 30;

type LibraryCount = { scope: string; total: number };

export function getVisibleLibraryCount(
  count: LibraryCount | null,
  currentScope: string,
  isBusy: boolean,
  hasError: boolean,
): string {
  if (!count || count.scope !== currentScope || isBusy || hasError) return '';
  return `${count.total} saved`;
}

function humanize(value: string) {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatSavedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Saved';
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
}

export default function LibraryScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const accountId = user?.id ?? null;
  const isDark = useColorScheme() === 'dark';
  const palette = isDark ? darkPalette : lightPalette;

  const [items, setItems] = useState<LibraryItem[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [libraryCount, setLibraryCount] = useState<LibraryCount | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [itemsError, setItemsError] = useState<string | null>(null);
  const [categoriesError, setCategoriesError] = useState<string | null>(null);
  const listRequest = useRef<AbortController | null>(null);
  const moreRequest = useRef<AbortController | null>(null);
  const categoriesRequest = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const queryScope = JSON.stringify([accountId, selectedPlatform, selectedCategory]);

  const queryPath = useCallback(
    (cursor?: string | null) => {
      const params = new URLSearchParams();
      params.set('limit', String(PAGE_SIZE));
      if (cursor) params.set('cursor', cursor);
      if (selectedPlatform) params.set('platform', selectedPlatform);
      if (selectedCategory) params.set('category_id', selectedCategory);
      return `/v1/items?${params.toString()}`;
    },
    [selectedCategory, selectedPlatform],
  );

  const loadFirstPage = useCallback(
    async (mode: 'initial' | 'refresh' | 'retry' = 'initial') => {
      if (!accountId) {
        setItems([]);
        setNextCursor(null);
        setLibraryCount(null);
        setLoading(false);
        setRefreshing(false);
        return;
      }

      listRequest.current?.abort();
      moreRequest.current?.abort();
      const controller = new AbortController();
      listRequest.current = controller;
      const requestGeneration = generation.current;

      if (mode === 'refresh') setRefreshing(true);
      else setLoading(true);
      setItemsError(null);
      setNextCursor(null);

      try {
        const response = await api.get<ItemsResponse>(queryPath(), { signal: controller.signal });
        if (controller.signal.aborted || requestGeneration !== generation.current) return;
        setItems(response.items);
        setNextCursor(response.next_cursor);
        setLibraryCount({ scope: queryScope, total: response.total_count });
        setPlatforms((current) => {
          const values = new Set(current);
          response.items.forEach((item) => values.add(item.platform));
          return Array.from(values).sort((a, b) => a.localeCompare(b));
        });
      } catch (error) {
        if (controller.signal.aborted || requestGeneration !== generation.current) return;
        setItemsError('Your saved items could not be loaded. Check your connection and try again.');
      } finally {
        if (!controller.signal.aborted && requestGeneration === generation.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [accountId, queryPath, queryScope],
  );

  useEffect(() => {
    generation.current += 1;
    const requestGeneration = generation.current;
    listRequest.current?.abort();
    moreRequest.current?.abort();
    setItems([]);
    setPlatforms([]);
    setNextCursor(null);
    setLibraryCount(null);
    setSelectedPlatform(null);
    setSelectedCategory(null);
    setItemsError(null);
    setLoading(true);
    setRefreshing(false);
    setCategories([]);
    setCategoriesError(null);

    if (!accountId) {
      setLoading(false);
      return () => {
        generation.current += 1;
      };
    }

    categoriesRequest.current?.abort();
    const categoriesController = new AbortController();
    categoriesRequest.current = categoriesController;
    void api
      .get<Category[]>('/v1/categories', { signal: categoriesController.signal })
      .then((response) => {
        if (!categoriesController.signal.aborted && requestGeneration === generation.current) {
          setCategories(response);
        }
      })
      .catch(() => {
        if (!categoriesController.signal.aborted && requestGeneration === generation.current) {
          setCategoriesError('Categories could not be loaded.');
        }
      });

    return () => {
      listRequest.current?.abort();
      moreRequest.current?.abort();
      categoriesRequest.current?.abort();
      generation.current += 1;
    };
  }, [accountId]);

  useEffect(() => {
    if (!accountId) return;
    void loadFirstPage('initial');
  }, [accountId, selectedPlatform, selectedCategory, loadFirstPage]);

  const loadMore = useCallback(async () => {
    if (!accountId || !nextCursor || loadingMore) return;
    moreRequest.current?.abort();
    const controller = new AbortController();
    moreRequest.current = controller;
    const requestGeneration = generation.current;
    setLoadingMore(true);
    setItemsError(null);
    try {
      const response = await api.get<ItemsResponse>(queryPath(nextCursor), { signal: controller.signal });
      if (controller.signal.aborted || requestGeneration !== generation.current) return;
      setItems((current) => {
        const seen = new Set(current.map((item) => item.id));
        return [...current, ...response.items.filter((item) => !seen.has(item.id))];
      });
      setNextCursor(response.next_cursor);
      setLibraryCount({ scope: queryScope, total: response.total_count });
      setPlatforms((current) => {
        const values = new Set(current);
        response.items.forEach((item) => values.add(item.platform));
        return Array.from(values).sort((a, b) => a.localeCompare(b));
      });
    } catch {
      if (!controller.signal.aborted && requestGeneration === generation.current) {
        setItemsError('More saved items could not be loaded. Try again.');
      }
    } finally {
      if (!controller.signal.aborted && requestGeneration === generation.current) setLoadingMore(false);
    }
  }, [accountId, loadingMore, nextCursor, queryPath, queryScope]);

  const onRefresh = useCallback(() => {
    void loadFirstPage('refresh');
  }, [loadFirstPage]);

  const visibleCategories = useMemo(() => categories, [categories]);

  if (!accountId) {
    return (
      <SafeAreaView style={[styles.screen, { backgroundColor: palette.background }]}>
        <View style={styles.centerState}>
          <Text style={[styles.stateTitle, { color: palette.ink }]}>Sign in to see your library</Text>
          <Text style={[styles.stateBody, { color: palette.muted }]}>Your saved videos and collections live here.</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={[styles.screen, { backgroundColor: palette.background }]} edges={['top']}>
      <View style={styles.header}>
        <View>
          <Text style={[styles.eyebrow, { color: palette.coral }]}>YOUR SAVED REELS</Text>
          <Text style={[styles.title, { color: palette.ink }]}>Library</Text>
        </View>
        <View style={styles.headerActions}>
          <Text style={[styles.resultCount, { color: palette.muted }]} accessibilityLiveRegion="polite">
            {getVisibleLibraryCount(libraryCount, queryScope, loading || refreshing, Boolean(itemsError))}
          </Text>
          <View style={styles.headerLinks}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Browse categories"
              accessibilityHint="Opens your saved categories"
              onPress={() => router.push('/(app)/categories' as never)}
              style={({ pressed }) => [
                styles.categoryLink,
                { backgroundColor: palette.surface, borderColor: palette.border, opacity: pressed ? 0.78 : 1 },
              ]}
            >
              <Text style={[styles.categoryLinkText, { color: palette.ink }]}>Categories</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Manage collections"
              accessibilityHint="Create and organize private collections"
              onPress={() => router.push('/(app)/collections' as never)}
              style={({ pressed }) => [
                styles.categoryLink,
                { backgroundColor: palette.surface, borderColor: palette.border, opacity: pressed ? 0.78 : 1 },
              ]}
            >
              <Text style={[styles.categoryLinkText, { color: palette.ink }]}>Collections</Text>
            </Pressable>
          </View>
        </View>
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.chipRow}
        accessibilityLabel="Filter by platform"
      >
        <FilterChip label="Everything" selected={!selectedPlatform} onPress={() => setSelectedPlatform(null)} palette={palette} />
        {platforms.map((platform) => (
          <FilterChip
            key={platform}
            label={humanize(platform)}
            selected={selectedPlatform === platform}
            onPress={() => setSelectedPlatform((current) => (current === platform ? null : platform))}
            palette={palette}
          />
        ))}
      </ScrollView>

      {visibleCategories.length > 0 ? (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={[styles.chipRow, styles.categoryRow]}
          accessibilityLabel="Filter by category"
        >
          <FilterChip label="All categories" selected={!selectedCategory} onPress={() => setSelectedCategory(null)} palette={palette} subtle />
          {visibleCategories.map((category) => (
            <FilterChip
              key={category.id}
              label={`${category.emoji ? `${category.emoji} ` : ''}${category.name}`}
              selected={selectedCategory === category.id}
              onPress={() => setSelectedCategory((current) => (current === category.id ? null : category.id))}
              palette={palette}
              subtle
            />
          ))}
        </ScrollView>
      ) : categoriesError ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Retry loading categories"
          onPress={() => {
            categoriesRequest.current?.abort();
            const controller = new AbortController();
            categoriesRequest.current = controller;
            setCategoriesError(null);
            void api
              .get<Category[]>('/v1/categories', { signal: controller.signal })
              .then(setCategories)
              .catch(() => setCategoriesError('Categories could not be loaded.'));
          }}
          style={styles.collectionError}
        >
          <Text style={[styles.smallMuted, { color: palette.muted }]}>{categoriesError} Tap to retry.</Text>
        </Pressable>
      ) : null}

      {loading && items.length === 0 ? (
        <View style={styles.centerState}>
          <ActivityIndicator color={palette.coral} />
          <Text style={[styles.stateBody, { color: palette.muted }]}>Finding your saved videos…</Text>
        </View>
      ) : itemsError && items.length === 0 ? (
        <View style={styles.centerState}>
          <Text style={[styles.stateTitle, { color: palette.ink }]}>Your library didn’t load</Text>
              <Text style={[styles.stateBody, { color: palette.muted }]}>{itemsError}</Text>
          <ActionButton label="Try again" onPress={() => void loadFirstPage('retry')} palette={palette} />
        </View>
      ) : items.length === 0 ? (
        <ScrollView
          contentContainerStyle={styles.emptyScroll}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={palette.coral} />}
        >
          <View style={styles.emptyMark} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
            <Text style={styles.emptyEmoji}>✳</Text>
          </View>
          <Text style={[styles.stateTitle, { color: palette.ink }]}>A little space for your next “save”</Text>
          <Text style={[styles.stateBody, { color: palette.muted }]}>
            When you save a reel, it will show up here—ready when you want to find it again.
          </Text>
        </ScrollView>
      ) : (
        <ScrollView
          contentContainerStyle={styles.listContent}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={palette.coral} />}
        >
          {items.map((item) => (
            <View
              key={item.id}
              style={[styles.itemRow, { backgroundColor: palette.surface }]}
            >
              {item.thumbnail_url ? (
                <Image source={{ uri: item.thumbnail_url }} style={styles.thumbnail} />
              ) : (
                <View style={[styles.thumbnail, styles.thumbnailFallback, { backgroundColor: palette.tint }]}>
                  <Text style={[styles.fallbackGlyph, { color: palette.coral }]}>{item.platform.slice(0, 1).toUpperCase()}</Text>
                </View>
              )}
              <View style={styles.itemContent}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`${item.title || 'Saved video'}, ${humanize(item.platform)}, saved ${formatSavedAt(item.saved_at)}`}
                  accessibilityHint="Opens the saved item details"
                  onPress={() => router.push({ pathname: '/(app)/item/[id]', params: { id: item.id } })}
                  style={({ pressed }) => [styles.itemCopy, { opacity: pressed ? 0.72 : 1 }]}
                >
                  <Text numberOfLines={2} style={[styles.itemTitle, { color: palette.ink }]}>
                    {item.title || item.summary || item.owner_username || item.source_url}
                  </Text>
                  <Text numberOfLines={1} style={[styles.itemMeta, { color: palette.muted }]}>
                    {humanize(item.platform)} · {formatSavedAt(item.saved_at)}
                  </Text>
                  {item.state !== 'ready' ? (
                    <Text style={[styles.itemState, { color: palette.coral }]}>{humanize(item.state)}</Text>
                  ) : null}
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`Organize ${item.title || 'saved video'} into a collection`}
                  accessibilityHint="Opens your collections for this saved item"
                  onPress={() => router.push({ pathname: '/(app)/collections', params: { itemId: item.id } } as never)}
                  style={({ pressed }) => [
                    styles.organizeButton,
                    { borderColor: palette.border, opacity: pressed ? 0.72 : 1 },
                  ]}
                >
                  <Text style={[styles.organizeText, { color: palette.ink }]}>Organize</Text>
                </Pressable>
              </View>
            </View>
          ))}

          {itemsError ? <Text style={[styles.inlineError, { color: palette.coral }]}>{itemsError}</Text> : null}
          {nextCursor ? (
            <ActionButton
              label={loadingMore ? 'Loading…' : 'Load more'}
              onPress={() => void loadMore()}
              disabled={loadingMore}
              palette={palette}
            />
          ) : null}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

function FilterChip({
  label,
  selected,
  onPress,
  palette,
  subtle = false,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  palette: Palette;
  subtle?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.chip,
        {
          backgroundColor: selected ? (subtle ? palette.tint : palette.ink) : palette.surface,
          borderColor: selected ? (subtle ? palette.coral : palette.ink) : palette.border,
          opacity: pressed ? 0.78 : 1,
        },
      ]}
    >
      <Text style={[styles.chipLabel, { color: selected && !subtle ? palette.onInk : palette.ink }]}>{label}</Text>
    </Pressable>
  );
}

function ActionButton({
  label,
  onPress,
  palette,
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  palette: Palette;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled, busy: label === 'Loading…' }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.actionButton, { backgroundColor: palette.coral, opacity: pressed || disabled ? 0.78 : 1 }]}
    >
      {label === 'Loading…' ? <ActivityIndicator color="#1E211E" size="small" /> : null}
      <Text style={styles.actionText}>{label}</Text>
    </Pressable>
  );
}

type Palette = {
  background: string;
  surface: string;
  ink: string;
  onInk: string;
  muted: string;
  coral: string;
  tint: string;
  border: string;
};

const lightPalette: Palette = {
  background: semantic.canvas,
  surface: semantic.surface,
  ink: semantic.textPrimary,
  onInk: color.cream,
  muted: semantic.textSecondary,
  coral: color.coral,
  tint: semantic.selected,
  border: semantic.border,
};

const darkPalette: Palette = {
  background: semantic.askCanvas,
  surface: semantic.askSurface,
  ink: semantic.askTextPrimary,
  onInk: color.ink,
  muted: semantic.askTextSecondary,
  coral: color.coral,
  tint: '#34362D',
  border: semantic.askBorder,
};

const styles = StyleSheet.create({
  screen: { flex: 1 },
  header: { paddingHorizontal: 22, paddingTop: 10, paddingBottom: 12, flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between' },
  headerActions: { alignItems: 'flex-end', gap: 7 },
  headerLinks: { flexDirection: 'row', gap: 6 },
  categoryLink: { minHeight: 48, borderRadius: 24, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 16, justifyContent: 'center', alignItems: 'center' },
  categoryLinkText: { fontSize: 14, fontWeight: '600' },
  eyebrow: { fontSize: 11, fontWeight: '700', letterSpacing: 1.6, marginBottom: 5 },
  title: { fontSize: 32, lineHeight: 38, fontWeight: '700', letterSpacing: -0.7 },
  resultCount: { fontSize: 13, paddingBottom: 5 },
  chipRow: { paddingHorizontal: 20, paddingVertical: 7, gap: 8, alignItems: 'center' },
  categoryRow: { paddingTop: 3, paddingBottom: 12 },
  chip: { minHeight: 48, borderRadius: 24, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 16, justifyContent: 'center', alignItems: 'center' },
  chipLabel: { fontSize: 14, fontWeight: '600' },
  collectionError: { minHeight: 48, paddingHorizontal: 22, justifyContent: 'center' },
  smallMuted: { fontSize: 13 },
  centerState: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 34, paddingBottom: 70, gap: 12 },
  stateTitle: { fontSize: 20, lineHeight: 27, fontWeight: '700', textAlign: 'center' },
  stateBody: { fontSize: 16, lineHeight: 24, textAlign: 'center', maxWidth: 310 },
  emptyScroll: { flexGrow: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32, paddingBottom: 80 },
  emptyMark: { width: 70, height: 70, borderRadius: 35, backgroundColor: '#FCE7DF', justifyContent: 'center', alignItems: 'center', marginBottom: 20 },
  emptyEmoji: { color: '#D97058', fontSize: 31 },
  listContent: { paddingHorizontal: 16, paddingTop: 4, paddingBottom: 34, gap: 10 },
  itemRow: { minHeight: 104, borderRadius: 18, padding: 10, flexDirection: 'row', alignItems: 'center', gap: 12 },
  thumbnail: { width: 72, height: 76, borderRadius: 12, backgroundColor: '#EAE7DF' },
  thumbnailFallback: { justifyContent: 'center', alignItems: 'center' },
  fallbackGlyph: { fontSize: 25, fontWeight: '700' },
  itemContent: { flex: 1, minHeight: 76, justifyContent: 'center', gap: 6 },
  itemCopy: { minHeight: 48, justifyContent: 'center', gap: 4 },
  itemTitle: { fontSize: 15, lineHeight: 20, fontWeight: '600' },
  itemMeta: { fontSize: 13, lineHeight: 18 },
  itemState: { fontSize: 12, fontWeight: '600' },
  organizeButton: { minHeight: 48, minWidth: 90, alignSelf: 'flex-start', paddingHorizontal: 12, borderRadius: 16, borderWidth: StyleSheet.hairlineWidth, justifyContent: 'center', alignItems: 'center' },
  organizeText: { fontSize: 13, fontWeight: '600' },
  inlineError: { fontSize: 14, textAlign: 'center', padding: 8 },
  actionButton: { minHeight: 48, alignSelf: 'center', paddingHorizontal: 22, borderRadius: 24, justifyContent: 'center', alignItems: 'center', flexDirection: 'row', gap: 8, marginVertical: 10 },
  actionText: { color: '#1E211E', fontSize: 15, fontWeight: '700' },
});
