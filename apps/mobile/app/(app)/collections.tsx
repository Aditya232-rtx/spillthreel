import { useAuth } from '@/hooks/useAuth';
import {
  addItemToCollection,
  ApiError,
  createCollection,
  getCollections,
  removeItemFromCollection,
  type UserCollection,
} from '@/lib/api';
import { color, semantic } from '@/theme/tokens';
import { collectionQueryIsCurrent } from '@/lib/collection-state';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useColorScheme,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

type CollectionRow = UserCollection & { contains_item?: boolean };
type ActionState = { collectionId: string; action: 'add' | 'remove' } | null;

export default function CollectionsScreen() {
  const router = useRouter();
  const { itemId: rawItemId } = useLocalSearchParams<{ itemId?: string | string[] }>();
  const itemId = Array.isArray(rawItemId) ? rawItemId[0] : rawItemId;
  const { user } = useAuth();
  const accountId = user?.id ?? null;
  const isDark = useColorScheme() === 'dark';
  const palette = isDark ? darkPalette : lightPalette;

  const [collections, setCollections] = useState<CollectionRow[]>([]);
  const [loadedOwnerId, setLoadedOwnerId] = useState<string | null>(null);
  const [loadedItemId, setLoadedItemId] = useState<string | undefined>(undefined);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [action, setAction] = useState<ActionState>(null);
  const request = useRef<AbortController | null>(null);
  const activeRequests = useRef(new Set<AbortController>());
  const generation = useRef(0);

  const abortActiveRequests = useCallback(() => {
    request.current?.abort();
    request.current = null;
    activeRequests.current.forEach((controller) => controller.abort());
    activeRequests.current.clear();
  }, []);

  const load = useCallback(async () => {
    if (!accountId) {
      setCollections([]);
        setLoadedOwnerId(null);
        setLoadedItemId(undefined);
      setLoading(false);
      return;
    }
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    activeRequests.current.add(controller);
    const requestGeneration = generation.current;
    setLoading(true);
    setLoadError(null);
    try {
      const rows = await getCollections({
        itemId,
        expectedUserId: accountId,
        signal: controller.signal,
      });
      if (!controller.signal.aborted && requestGeneration === generation.current) {
        setCollections(rows);
        setLoadedOwnerId(accountId);
        setLoadedItemId(itemId);
      }
    } catch {
      if (!controller.signal.aborted && requestGeneration === generation.current) {
        setLoadedOwnerId(accountId);
        setLoadedItemId(itemId);
        setLoadError('Your collections could not be loaded. Check your connection and try again.');
      }
    } finally {
      activeRequests.current.delete(controller);
      if (!controller.signal.aborted && requestGeneration === generation.current) setLoading(false);
    }
  }, [accountId, itemId]);

  useEffect(() => {
    generation.current += 1;
    const requestGeneration = generation.current;
    abortActiveRequests();
    setCollections([]);
    setLoadedOwnerId(null);
    setLoadedItemId(undefined);
    setName('');
    setCreateError(null);
    setActionError(null);
    setLoadError(null);
    setCreating(false);
    setAction(null);
    setLoading(Boolean(accountId));
    if (accountId) void load();
    else setLoading(false);

    return () => {
      if (generation.current === requestGeneration) generation.current += 1;
      abortActiveRequests();
    };
  }, [accountId, abortActiveRequests, load]);

  const create = async () => {
    const trimmed = name.trim();
    if (!accountId || !trimmed || trimmed.length > 80 || creating || loading || loadedOwnerId !== accountId || loadedItemId !== itemId) return;
    const refreshAfterCreate = Boolean(loadError);
    const controller = new AbortController();
    activeRequests.current.add(controller);
    const requestGeneration = generation.current;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createCollection(trimmed, {
        expectedUserId: accountId,
        signal: controller.signal,
      });
      if (controller.signal.aborted || requestGeneration !== generation.current) return;
      setCollections((current) => [...current, created]);
      setLoadedOwnerId(accountId);
      setLoadedItemId(itemId);
      setLoadError(null);
      setName('');
      if (refreshAfterCreate) void load();
    } catch (error) {
      if (controller.signal.aborted || requestGeneration !== generation.current) return;
      setCreateError(
        error instanceof ApiError && error.status === 409
          ? 'You already have a collection with that name. Try another name.'
          : 'That collection could not be created. Please try again.',
      );
    } finally {
      activeRequests.current.delete(controller);
      if (!controller.signal.aborted && requestGeneration === generation.current) setCreating(false);
    }
  };

  const updateMembership = async (collection: CollectionRow, nextAction: 'add' | 'remove') => {
    if (!accountId || !itemId || action) return;
    const controller = new AbortController();
    activeRequests.current.add(controller);
    const requestGeneration = generation.current;
    setAction({ collectionId: collection.id, action: nextAction });
    setActionError(null);
    try {
      const options = { expectedUserId: accountId, signal: controller.signal };
      if (nextAction === 'add') {
        await addItemToCollection(collection.id, itemId, options);
      } else {
        await removeItemFromCollection(collection.id, itemId, options);
      }
      if (controller.signal.aborted || requestGeneration !== generation.current) return;
      setCollections((current) => current.map((row) => {
        if (row.id !== collection.id) return row;
        const previouslyContains = row.contains_item === true;
        return {
          ...row,
          contains_item: nextAction === 'add',
          item_count: Math.max(0, row.item_count + (nextAction === 'add' ? (previouslyContains ? 0 : 1) : (previouslyContains ? -1 : 0))),
        };
      }));
    } catch {
      if (!controller.signal.aborted && requestGeneration === generation.current) {
        setActionError('That change could not be saved. Try again.');
      }
    } finally {
      activeRequests.current.delete(controller);
      if (!controller.signal.aborted && requestGeneration === generation.current) setAction(null);
    }
  };

  if (!accountId) {
    return (
      <SafeAreaView style={[styles.screen, { backgroundColor: palette.background }]}>
        <View style={styles.centerState}>
          <Text style={[styles.title, { color: palette.ink }]}>Sign in to organize saves</Text>
          <Text style={[styles.body, { color: palette.muted }]}>Your private collections are tied to your account.</Text>
        </View>
      </SafeAreaView>
    );
  }

  const collectionsBelongToCurrentQuery = collectionQueryIsCurrent(loadedOwnerId, loadedItemId, accountId, itemId);

  return (
    <SafeAreaView style={[styles.screen, { backgroundColor: palette.background }]} edges={['top']}>
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={styles.header}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Back to library"
            onPress={() => router.back()}
            style={({ pressed }) => [styles.backButton, { borderColor: palette.border, opacity: pressed ? 0.72 : 1 }]}
          >
            <Text style={[styles.backLabel, { color: palette.ink }]}>Back</Text>
          </Pressable>
          <Text style={[styles.title, { color: palette.ink }]}>Collections</Text>
          <View style={styles.headerSpacer} />
        </View>

        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={styles.content}
        >
          <Text style={[styles.intro, { color: palette.muted }]}>Keep saved videos together in private collections.</Text>

          <View style={[styles.createSection, { borderColor: palette.border, backgroundColor: palette.surface }]}>
            <Text style={[styles.sectionTitle, { color: palette.ink }]}>New collection</Text>
            <TextInput
              accessibilityLabel="Collection name"
              accessibilityHint="Enter a name up to 80 characters"
              value={name}
              onChangeText={(value) => {
                setName(value);
                if (createError) setCreateError(null);
              }}
              placeholder="e.g. Weeknight recipes"
              placeholderTextColor={palette.muted}
              maxLength={80}
              returnKeyType="done"
              onSubmitEditing={() => void create()}
              editable={!creating}
              style={[styles.input, { color: palette.ink, borderColor: palette.border }]}
            />
            <Text style={[styles.helper, { color: palette.muted }]}>Up to 80 characters. Collections are private.</Text>
            {createError ? <Text accessibilityRole="alert" style={[styles.error, { color: palette.coral }]}>{createError}</Text> : null}
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={creating ? 'Creating collection' : 'Create collection'}
              accessibilityState={{ disabled: creating || loading || !collectionsBelongToCurrentQuery || !name.trim(), busy: creating }}
              disabled={creating || loading || !collectionsBelongToCurrentQuery || !name.trim() || name.trim().length > 80}
              onPress={() => void create()}
              style={({ pressed }) => [
                styles.primaryButton,
                { backgroundColor: palette.coral, opacity: pressed || creating || !name.trim() ? 0.75 : 1 },
              ]}
            >
              {creating ? <ActivityIndicator color={color.ink} size="small" /> : null}
              <Text style={styles.primaryButtonText}>{creating ? 'Creating…' : 'Create collection'}</Text>
            </Pressable>
          </View>

          <View style={styles.listHeading}>
            <Text style={[styles.sectionTitle, { color: palette.ink }]}>{itemId ? 'Organize this save' : 'Your collections'}</Text>
            {!loading && collectionsBelongToCurrentQuery && !loadError ? (
              <Text style={[styles.count, { color: palette.muted }]}>{collections.length}</Text>
            ) : null}
          </View>

          {itemId ? (
            <Text style={[styles.helper, styles.itemHint, { color: palette.muted }]}>Add this saved video to a collection, or remove it from one.</Text>
          ) : null}

          {loading || !collectionsBelongToCurrentQuery ? (
            <View style={styles.inlineState}>
              <ActivityIndicator color={palette.coral} />
              <Text style={[styles.body, { color: palette.muted }]}>Loading your collections…</Text>
            </View>
          ) : loadError ? (
            <View style={styles.inlineState}>
              <Text style={[styles.body, { color: palette.muted }]}>{loadError}</Text>
              <ActionButton label="Try again" onPress={() => void load()} palette={palette} />
            </View>
          ) : collections.length === 0 ? (
            <View style={[styles.emptyState, { borderColor: palette.border }]}>
              <Text style={[styles.emptyTitle, { color: palette.ink }]}>No collections yet</Text>
              <Text style={[styles.body, { color: palette.muted }]}>Create one above, then add saved videos as you find them.</Text>
            </View>
          ) : (
            <View style={styles.collectionList}>
              {collections.map((collection) => {
                const busyAction = action?.collectionId === collection.id ? action.action : null;
                const containsItem = collection.contains_item === true;
                return (
                  <View key={collection.id} style={[styles.collectionRow, { backgroundColor: palette.surface, borderColor: palette.border }]}>
                    <View style={styles.collectionCopy}>
                      <Text style={[styles.collectionName, { color: palette.ink }]}>{collection.name}</Text>
                      <Text style={[styles.collectionMeta, { color: palette.muted }]}>
                        {collection.item_count} {collection.item_count === 1 ? 'saved video' : 'saved videos'}
                      </Text>
                    </View>
                    {itemId ? (
                      <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={`${containsItem ? 'Remove from' : 'Add to'} ${collection.name}`}
                        accessibilityState={{ disabled: Boolean(action), busy: Boolean(busyAction) }}
                        disabled={Boolean(action)}
                        onPress={() => void updateMembership(collection, containsItem ? 'remove' : 'add')}
                        style={({ pressed }) => [
                          styles.membershipButton,
                          {
                            backgroundColor: containsItem ? palette.tint : palette.coral,
                            borderColor: containsItem ? palette.border : palette.coral,
                            opacity: pressed || action ? 0.72 : 1,
                          },
                        ]}
                      >
                        {busyAction ? <ActivityIndicator color={palette.ink} size="small" /> : null}
                        <Text style={[styles.membershipText, { color: palette.ink }]}>
                          {busyAction ? (busyAction === 'add' ? 'Adding…' : 'Removing…') : (containsItem ? 'Remove' : 'Add')}
                        </Text>
                      </Pressable>
                    ) : null}
                  </View>
                );
              })}
            </View>
          )}
          {actionError ? <Text accessibilityRole="alert" style={[styles.error, { color: palette.coral }]}>{actionError}</Text> : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function ActionButton({ label, onPress, palette }: { label: string; onPress: () => void; palette: Palette }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      style={({ pressed }) => [styles.secondaryButton, { borderColor: palette.border, opacity: pressed ? 0.72 : 1 }]}
    >
      <Text style={[styles.secondaryButtonText, { color: palette.ink }]}>{label}</Text>
    </Pressable>
  );
}

type Palette = { background: string; surface: string; ink: string; muted: string; coral: string; tint: string; border: string };

const lightPalette: Palette = {
  background: semantic.canvas,
  surface: semantic.surface,
  ink: semantic.textPrimary,
  muted: semantic.textSecondary,
  coral: color.coral,
  tint: semantic.selected,
  border: semantic.border,
};

const darkPalette: Palette = {
  background: semantic.askCanvas,
  surface: semantic.askSurface,
  ink: semantic.askTextPrimary,
  muted: semantic.askTextSecondary,
  coral: color.coral,
  tint: '#34362D',
  border: semantic.askBorder,
};

const styles = StyleSheet.create({
  flex: { flex: 1 },
  screen: { flex: 1 },
  header: { minHeight: 64, paddingHorizontal: 20, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  backButton: { minHeight: 48, minWidth: 64, borderWidth: StyleSheet.hairlineWidth, borderRadius: 24, paddingHorizontal: 14, justifyContent: 'center', alignItems: 'center' },
  backLabel: { fontSize: 14, fontWeight: '600' },
  title: { fontSize: 25, lineHeight: 32, fontWeight: '700', letterSpacing: -0.4 },
  headerSpacer: { width: 64 },
  content: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 36, gap: 12 },
  centerState: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32, gap: 12 },
  intro: { fontSize: 16, lineHeight: 24, maxWidth: 340, marginBottom: 4 },
  createSection: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 20, padding: 16, gap: 9 },
  sectionTitle: { fontSize: 18, lineHeight: 24, fontWeight: '700' },
  input: { minHeight: 52, borderWidth: StyleSheet.hairlineWidth, borderRadius: 14, paddingHorizontal: 14, fontSize: 16 },
  helper: { fontSize: 13, lineHeight: 19 },
  error: { fontSize: 14, lineHeight: 20 },
  primaryButton: { minHeight: 48, borderRadius: 24, paddingHorizontal: 20, alignSelf: 'flex-start', justifyContent: 'center', alignItems: 'center', flexDirection: 'row', gap: 8, marginTop: 2 },
  primaryButtonText: { color: color.ink, fontSize: 15, fontWeight: '700' },
  listHeading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 },
  count: { fontSize: 14, fontVariant: ['tabular-nums'] },
  itemHint: { marginTop: -8 },
  inlineState: { minHeight: 136, alignItems: 'center', justifyContent: 'center', gap: 12, paddingHorizontal: 18 },
  emptyState: { minHeight: 136, borderWidth: StyleSheet.hairlineWidth, borderRadius: 18, paddingHorizontal: 20, paddingVertical: 22, alignItems: 'center', justifyContent: 'center', gap: 8 },
  emptyTitle: { fontSize: 17, fontWeight: '700' },
  body: { fontSize: 15, lineHeight: 23, textAlign: 'center', maxWidth: 320 },
  collectionList: { gap: 8 },
  collectionRow: { minHeight: 76, borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, paddingVertical: 10, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 10 },
  collectionCopy: { flex: 1, gap: 4 },
  collectionName: { fontSize: 16, lineHeight: 22, fontWeight: '600' },
  collectionMeta: { fontSize: 13, lineHeight: 18 },
  membershipButton: { minHeight: 48, minWidth: 76, borderRadius: 24, borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 14, flexDirection: 'row', justifyContent: 'center', alignItems: 'center', gap: 6 },
  membershipText: { fontSize: 14, fontWeight: '700' },
  secondaryButton: { minHeight: 48, borderWidth: StyleSheet.hairlineWidth, borderRadius: 24, paddingHorizontal: 18, justifyContent: 'center', alignItems: 'center' },
  secondaryButtonText: { fontSize: 15, fontWeight: '600' },
});
