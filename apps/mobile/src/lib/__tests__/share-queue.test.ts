jest.mock('@react-native-async-storage/async-storage', () => {
  const store = new Map<string, string>();
  return {
    __esModule: true,
    default: {
      getItem: jest.fn(async (key: string) => store.get(key) ?? null),
      setItem: jest.fn(async (key: string, value: string) => { store.set(key, value); }),
      removeItem: jest.fn(async (key: string) => { store.delete(key); }),
      __store: store,
    },
  };
});

jest.mock('expo-crypto', () => ({
  randomUUID: jest.fn(() => `share-id-${++mockUuidCounter}`),
}));

jest.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    code: string;

    constructor(status: number, _body: unknown, message: string, code: string) {
      super(message);
      this.status = status;
      this.code = code;
    }
  },
  api: { post: jest.fn() },
}));

let mockUuidCounter = 0;

import AsyncStorage from '@react-native-async-storage/async-storage';
import { ApiError, api } from '@/lib/api';
import {
  enqueueSharedUrl,
  flushSharedQueue,
  clearQueuedShares,
  getQueuedShares,
  prepareShareQueueForAccountDeletion,
  removeQueuedShare,
  restoreShareQueueAfterDeletionFailure,
} from '@/lib/share-queue';

const storage = AsyncStorage as typeof AsyncStorage & { __store: Map<string, string> };
const post = api.post as jest.Mock;

describe('account-scoped share delivery queue', () => {
  beforeEach(() => {
    storage.__store.clear();
    post.mockReset().mockResolvedValue({ accepted: true });
    mockUuidCounter = 0;
  });

  it('keeps each account’s queued URLs isolated and only flushes the requested owner', async () => {
    await enqueueSharedUrl('user-a', 'https://example.com/a');
    await enqueueSharedUrl('user-b', 'https://example.com/b');

    const result = await flushSharedQueue('user-a');

    expect(result).toMatchObject({ accepted: 1, remaining: 0 });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      '/v1/saves',
      { url: 'https://example.com/a', source: 'share_extension' },
      expect.objectContaining({ expectedUserId: 'user-a' }),
    );
    await expect(getQueuedShares('user-a')).resolves.toEqual([]);
    await expect(getQueuedShares('user-b')).resolves.toMatchObject([
      { url: 'https://example.com/b' },
    ]);
  });

  it('normalizes URLs and deduplicates the same queued URL for one owner only', async () => {
    const first = await enqueueSharedUrl('user-a', '  https://example.com/watch?id=1#section  ');
    const duplicate = await enqueueSharedUrl('user-a', 'https://example.com/watch?id=1');
    const otherOwner = await enqueueSharedUrl('user-b', 'https://example.com/watch?id=1');

    expect(duplicate).toEqual(first);
    expect(first.url).toBe('https://example.com/watch?id=1');
    expect(otherOwner.id).not.toBe(first.id);
    await expect(getQueuedShares('user-a')).resolves.toHaveLength(1);
    await expect(getQueuedShares('user-b')).resolves.toHaveLength(1);
  });

  it('rejects non-http URLs before persisting them', async () => {
    await expect(enqueueSharedUrl('user-a', 'javascript:alert(1)'))
      .rejects.toThrow('share_url_unsupported');
    await expect(enqueueSharedUrl('user-a', 'not a URL'))
      .rejects.toThrow('share_url_invalid');
    await expect(getQueuedShares('user-a')).resolves.toEqual([]);
  });

  it('removes an entry only from the specified owner’s queue', async () => {
    const ownerA = await enqueueSharedUrl('user-a', 'https://example.com/same');
    const ownerB = await enqueueSharedUrl('user-b', 'https://example.com/same');

    await removeQueuedShare('user-a', ownerA.id);

    await expect(getQueuedShares('user-a')).resolves.toEqual([]);
    await expect(getQueuedShares('user-b')).resolves.toEqual([ownerB]);
  });

  it('can clear only one account’s persisted pending links', async () => {
    await enqueueSharedUrl('user-a', 'https://example.com/a');
    await enqueueSharedUrl('user-b', 'https://example.com/b');

    await clearQueuedShares('user-a');

    await expect(getQueuedShares('user-a')).resolves.toEqual([]);
    await expect(getQueuedShares('user-b')).resolves.toHaveLength(1);
  });

  it('drains a link added while an earlier flush is in flight', async () => {
    await enqueueSharedUrl('user-a', 'https://example.com/first');
    let resolveFirst!: (value: unknown) => void;
    let requestStarted!: () => void;
    const started = new Promise<void>((resolve) => { requestStarted = resolve; });
    post.mockImplementationOnce(() => new Promise((resolve) => {
      resolveFirst = resolve;
      requestStarted();
    }));

    const initialFlush = flushSharedQueue('user-a');
    await started;
    await enqueueSharedUrl('user-a', 'https://example.com/second');
    const coalescedFlush = flushSharedQueue('user-a');
    resolveFirst({ accepted: true });

    const [firstResult, coalescedResult] = await Promise.all([initialFlush, coalescedFlush]);
    expect(firstResult).toMatchObject({ accepted: 2, remaining: 0 });
    expect(coalescedResult).toEqual(firstResult);
    expect(post).toHaveBeenCalledTimes(2);
    await expect(getQueuedShares('user-a')).resolves.toEqual([]);
  });

  it('fences new shares and settles an active request before deletion clears the queue', async () => {
    await enqueueSharedUrl('user-a', 'https://example.com/pending');
    let resolveRequest!: (value: unknown) => void;
    let requestStarted!: () => void;
    const started = new Promise<void>((resolve) => { requestStarted = resolve; });
    post.mockImplementationOnce(() => new Promise((resolve) => {
      resolveRequest = resolve;
      requestStarted();
    }));

    const flush = flushSharedQueue('user-a');
    await started;
    const deletionPreparation = prepareShareQueueForAccountDeletion('user-a');
    await expect(enqueueSharedUrl('user-a', 'https://example.com/late'))
      .rejects.toThrow('share_queue_account_deleting');
    resolveRequest({ accepted: true });
    await flush;
    await deletionPreparation;

    await expect(getQueuedShares('user-a')).resolves.toEqual([]);
    await expect(flushSharedQueue('user-a')).resolves.toMatchObject({ accepted: 0, remaining: 0 });
    expect(post).toHaveBeenCalledTimes(1);
    await restoreShareQueueAfterDeletionFailure('user-a');
    await expect(enqueueSharedUrl('user-a', 'https://example.com/after-rejected-delete'))
      .resolves.toMatchObject({ url: 'https://example.com/after-rejected-delete' });
  });

  it('removes an entry only after the API confirms acceptance', async () => {
    const entry = await enqueueSharedUrl('user-a', 'https://example.com/accepted');

    const result = await flushSharedQueue('user-a');

    expect(result).toMatchObject({ accepted: 1, remaining: 0 });
    expect(post).toHaveBeenCalledWith(
      '/v1/saves',
      { url: entry.url, source: 'share_extension' },
      expect.objectContaining({ expectedUserId: 'user-a' }),
    );
    await expect(getQueuedShares('user-a')).resolves.toEqual([]);
  });

  it('retains a share after a lost acknowledgement and safely replays it', async () => {
    await enqueueSharedUrl('user-a', 'https://example.com/lost-ack');
    post.mockRejectedValueOnce(new Error('connection closed after server commit'))
      .mockResolvedValueOnce({ accepted: true, replay: true });

    const firstAttempt = await flushSharedQueue('user-a');
    expect(firstAttempt).toMatchObject({ accepted: 0, remaining: 1, errorCode: 'network_error' });
    await expect(getQueuedShares('user-a')).resolves.toHaveLength(1);

    const replay = await flushSharedQueue('user-a');
    expect(replay).toMatchObject({ accepted: 1, remaining: 0 });
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
    await expect(getQueuedShares('user-a')).resolves.toEqual([]);
  });

  it.each([
    ['request timeout', new ApiError(0, null, 'Timed out', 'request_timeout'), 'request_timeout'],
    ['server failure', new ApiError(503, null, 'Unavailable', 'upstream_unavailable'), 'upstream_unavailable'],
  ])('retains queued work after a %s', async (_label, error, errorCode) => {
    await enqueueSharedUrl('user-a', 'https://example.com/retry');
    post.mockRejectedValue(error);

    const result = await flushSharedQueue('user-a');

    expect(result).toMatchObject({ accepted: 0, remaining: 1, errorCode });
    await expect(getQueuedShares('user-a')).resolves.toHaveLength(1);
  });

  it('does not send a queued share when the active session belongs to another account', async () => {
    await enqueueSharedUrl('user-a', 'https://example.com/private-to-a');
    post.mockRejectedValue(new ApiError(401, null, 'Account changed', 'account_scope_mismatch'));

    const result = await flushSharedQueue('user-a');

    expect(result).toMatchObject({ accepted: 0, remaining: 1, errorCode: 'account_scope_mismatch' });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      '/v1/saves',
      { url: 'https://example.com/private-to-a', source: 'share_extension' },
      expect.objectContaining({ expectedUserId: 'user-a' }),
    );
    await expect(getQueuedShares('user-a')).resolves.toHaveLength(1);
  });
});
