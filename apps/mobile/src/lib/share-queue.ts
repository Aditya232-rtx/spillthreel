/** Account-scoped, durable delivery queue for URL shares. */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { randomUUID } from 'expo-crypto';
import { ApiError, api } from '@/lib/api';

export interface ShareQueueEntry {
  id: string;
  url: string;
  createdAt: string;
}

export interface ShareQueueFlushResult {
  accepted: number;
  remaining: number;
  errorCode?: string;
}

interface QueueDocument {
  version: 1;
  entries: ShareQueueEntry[];
}

const STORAGE_PREFIX = 'spillthereel:share-queue:v1:';
const _ownerLocks = new Map<string, Promise<void>>();
const _deletionFencedOwners = new Set<string>();
const _activeFlushes = new Map<string, {
  rerunRequested: boolean;
  promise: Promise<ShareQueueFlushResult>;
}>();

function ownerKey(ownerId: string): string {
  if (!ownerId) throw new Error('share_queue_owner_required');
  return `${STORAGE_PREFIX}${encodeURIComponent(ownerId)}`;
}

function deletionFenceKey(ownerId: string): string {
  return `${ownerKey(ownerId)}:deletion-fence`;
}

async function isDeletionFenced(ownerId: string): Promise<boolean> {
  if (_deletionFencedOwners.has(ownerId)) return true;
  const fenced = (await AsyncStorage.getItem(deletionFenceKey(ownerId))) === '1';
  if (fenced) _deletionFencedOwners.add(ownerId);
  return fenced;
}

function normalizeShareUrl(rawUrl: string): string {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl.trim());
  } catch {
    throw new Error('share_url_invalid');
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new Error('share_url_unsupported');
  }
  parsed.hash = '';
  return parsed.toString();
}

async function withOwnerLock<T>(ownerId: string, work: () => Promise<T>): Promise<T> {
  const previous = _ownerLocks.get(ownerId) ?? Promise.resolve();
  let release = (): void => {};
  const current = new Promise<void>((resolve) => {
    release = resolve;
  });
  const tail = previous.catch(() => {}).then(() => current);
  _ownerLocks.set(ownerId, tail);
  await previous.catch(() => {});
  try {
    return await work();
  } finally {
    release();
    if (_ownerLocks.get(ownerId) === tail) _ownerLocks.delete(ownerId);
  }
}

async function readEntries(ownerId: string): Promise<ShareQueueEntry[]> {
  const encoded = await AsyncStorage.getItem(ownerKey(ownerId));
  if (encoded === null) return [];
  let parsed: QueueDocument;
  try {
    parsed = JSON.parse(encoded) as QueueDocument;
  } catch {
    throw new Error('share_queue_corrupt');
  }
  if (
    parsed.version !== 1 ||
    !Array.isArray(parsed.entries) ||
    parsed.entries.some(
      (entry) =>
        typeof entry?.id !== 'string' ||
        typeof entry.url !== 'string' ||
        typeof entry.createdAt !== 'string',
    )
  ) {
    throw new Error('share_queue_corrupt');
  }
  return parsed.entries;
}

async function writeEntries(ownerId: string, entries: ShareQueueEntry[]): Promise<void> {
  const document: QueueDocument = { version: 1, entries };
  await AsyncStorage.setItem(ownerKey(ownerId), JSON.stringify(document));
}

export async function enqueueSharedUrl(
  ownerId: string,
  rawUrl: string,
): Promise<ShareQueueEntry> {
  if (await isDeletionFenced(ownerId)) throw new Error('share_queue_account_deleting');
  const url = normalizeShareUrl(rawUrl);
  return withOwnerLock(ownerId, async () => {
    if (await isDeletionFenced(ownerId)) throw new Error('share_queue_account_deleting');
    const entries = await readEntries(ownerId);
    const existing = entries.find((entry) => entry.url === url);
    if (existing) return existing;
    const entry: ShareQueueEntry = {
      id: randomUUID(),
      url,
      createdAt: new Date().toISOString(),
    };
    await writeEntries(ownerId, [...entries, entry]);
    return entry;
  });
}

export async function getQueuedShares(ownerId: string): Promise<ShareQueueEntry[]> {
  return withOwnerLock(ownerId, () => readEntries(ownerId));
}

export async function removeQueuedShare(ownerId: string, entryId: string): Promise<void> {
  await withOwnerLock(ownerId, async () => {
    const entries = await readEntries(ownerId);
    const remaining = entries.filter((entry) => entry.id !== entryId);
    if (remaining.length !== entries.length) await writeEntries(ownerId, remaining);
  });
}

/** Remove this account's unsent local shares (used for explicit clear and account deletion). */
export async function clearQueuedShares(ownerId: string): Promise<void> {
  await withOwnerLock(ownerId, () => AsyncStorage.removeItem(ownerKey(ownerId)));
}

/** Fence local sends, settle any active request, then erase pending shares before deletion. */
export async function prepareShareQueueForAccountDeletion(ownerId: string): Promise<void> {
  _deletionFencedOwners.add(ownerId);
  try {
    await AsyncStorage.setItem(deletionFenceKey(ownerId), '1');
  } catch (error) {
    _deletionFencedOwners.delete(ownerId);
    throw error;
  }
  const active = _activeFlushes.get(ownerId);
  if (active) await active.promise.catch(() => {});
  await clearQueuedShares(ownerId);
}

/** Restore sharing only when the server rejected the deletion request. */
export async function restoreShareQueueAfterDeletionFailure(ownerId: string): Promise<void> {
  _deletionFencedOwners.delete(ownerId);
  await AsyncStorage.removeItem(deletionFenceKey(ownerId));
}

async function flushOwnerQueue(ownerId: string): Promise<ShareQueueFlushResult> {
  let accepted = 0;
  let errorCode: string | undefined;

  for (const entry of await getQueuedShares(ownerId)) {
    if (await isDeletionFenced(ownerId)) break;
    try {
      await api.post(
        '/v1/saves',
        { url: entry.url, source: 'share_extension' },
        { expectedUserId: ownerId, timeoutMs: 20_000 },
      );
    } catch (error) {
      errorCode = error instanceof ApiError ? error.code : 'network_error';
      break;
    }
    // A lost storage ACK can replay the API call later; the server's unique
    // owner+normalized-URL constraint makes that replay return the same item.
    await removeQueuedShare(ownerId, entry.id);
    accepted += 1;
  }

  return {
    accepted,
    remaining: (await getQueuedShares(ownerId)).length,
    ...(errorCode ? { errorCode } : {}),
  };
}

export function flushSharedQueue(ownerId: string): Promise<ShareQueueFlushResult> {
  const active = _activeFlushes.get(ownerId);
  if (active) {
    active.rerunRequested = true;
    return active.promise;
  }

  const state: { rerunRequested: boolean; promise: Promise<ShareQueueFlushResult> } = {
    rerunRequested: false,
    promise: Promise.resolve({ accepted: 0, remaining: 0 }),
  };
  _activeFlushes.set(ownerId, state);
  state.promise = (async () => {
    let result: ShareQueueFlushResult = { accepted: 0, remaining: 0 };
    let accepted = 0;
    while (!(await isDeletionFenced(ownerId))) {
      state.rerunRequested = false;
      result = await flushOwnerQueue(ownerId);
      accepted += result.accepted;
      if (!state.rerunRequested || result.errorCode) break;
    }
    return { ...result, accepted };
  })().finally(() => {
    if (_activeFlushes.get(ownerId) === state) _activeFlushes.delete(ownerId);
  });
  return state.promise;
}
