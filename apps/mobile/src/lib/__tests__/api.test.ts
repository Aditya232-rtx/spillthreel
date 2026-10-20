jest.mock('@/lib/supabase', () => ({
  supabase: {
    auth: {
      getSession: require('@jest/globals').jest.fn(),
      signOut: require('@jest/globals').jest.fn(),
    },
  },
}));

import {
  ApiError,
  addItemToCollection,
  api,
  commitImportPreview,
  createCollection,
  createImportPreview,
  getCollections,
  getImportPreviewCandidates,
  removeItemFromCollection,
} from '@/lib/api';
import { signOutCurrentSession } from '@/lib/auth-session';
import { supabase } from '@/lib/supabase';

const auth = supabase.auth as unknown as { getSession: any; signOut: any };

const session = (id: string, token: string) => ({
  access_token: token,
  user: { id },
});

const jsonResponse = (status: number, payload: unknown): Response =>
  ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }) as unknown as Response;

describe('mobile API request contracts', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    auth.getSession.mockReset();
    auth.signOut.mockReset().mockResolvedValue({ error: null });
    auth.getSession.mockResolvedValue({ data: { session: session('user-a', 'token-a') } });
    global.fetch = jest.fn();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('sends multipart FormData without setting a JSON content type', async () => {
    const form = new FormData();
    form.append('file', 'media');
    (global.fetch as any).mockResolvedValue(jsonResponse(200, { ok: true }));

    await api.post('/v1/imports', form);

    const [, init] = (global.fetch as any).mock.calls[0];
    expect(init.body).toBe(form);
    expect(init.headers['Content-Type']).toBeUndefined();
    expect(init.headers.Authorization).toBe('Bearer token-a');
  });

  it('creates an account-scoped preview with a stable retry key and multipart file', async () => {
    (global.fetch as any).mockResolvedValue(jsonResponse(201, {
      preview_id: 'preview-1', expires_at: '2026-10-20T01:00:00Z', total_candidates: 12,
    }));

    const result = await createImportPreview('file:///export.zip', 'export.zip', 'preview-key', {
      expectedUserId: 'user-a',
    });

    expect(result.preview_id).toBe('preview-1');
    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/v1/imports/previews');
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.headers['Idempotency-Key']).toBe('preview-key');
    expect(init.headers.Authorization).toBe('Bearer token-a');
    expect(init.headers['Content-Type']).toBeUndefined();
  });

  it('sets multipart MIME from the supported import filename', async () => {
    const OriginalFormData = global.FormData;
    class RecordingFormData {
      _parts: Array<[string, { name: string; type: string }]> = [];
      append(name: string, value: { name: string; type: string }) { this._parts.push([name, value]); }
    }
    global.FormData = RecordingFormData as unknown as typeof FormData;
    (global.fetch as any).mockResolvedValue(jsonResponse(201, {
      preview_id: 'preview-2', expires_at: '2026-10-20T01:00:00Z', total_candidates: 1,
    }));
    try {
      await createImportPreview('file:///EXPORT.ZIP', 'EXPORT.ZIP', 'upper-zip-key', { expectedUserId: 'user-a' });
      const zipBody = (global.fetch as any).mock.calls[0][1].body as RecordingFormData;
      expect(zipBody._parts[0][1]).toEqual(expect.objectContaining({ name: 'EXPORT.ZIP', type: 'application/zip' }));

      await createImportPreview('file:///saved_posts.html', 'saved_posts.html', 'html-key', { expectedUserId: 'user-a' });
      const htmlBody = (global.fetch as any).mock.calls[1][1].body as RecordingFormData;
      expect(htmlBody._parts[0][1]).toEqual(expect.objectContaining({ name: 'saved_posts.html', type: 'text/html' }));
    } finally {
      global.FormData = OriginalFormData;
    }
  });

  it('requests a bounded candidate page with its cursor and account fence', async () => {
    (global.fetch as any).mockResolvedValue(jsonResponse(200, { candidates: [], next_cursor: null }));

    await getImportPreviewCandidates('preview/with space', {
      limit: 100,
      cursor: 'opaque+/cursor',
      expectedUserId: 'user-a',
    });

    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/v1/imports/previews/preview%2Fwith%20space/candidates?');
    expect(url).toContain('limit=100');
    expect(url).toContain('cursor=opaque%2B%2Fcursor');
    expect(init.headers.Authorization).toBe('Bearer token-a');
  });

  it('commits the exact selected candidate set under the preview resource', async () => {
    (global.fetch as any).mockResolvedValue(jsonResponse(202, {
      import_id: 'import-1', state: 'processing', total_parsed: 2,
    }));

    await commitImportPreview('preview-1', ['candidate-b', 'candidate-a'], {
      expectedUserId: 'user-a',
    });

    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/v1/imports/previews/preview-1/commit');
    expect(JSON.parse(init.body)).toEqual({ candidate_ids: ['candidate-b', 'candidate-a'] });
    expect(init.headers.Authorization).toBe('Bearer token-a');
    expect(init.headers['Content-Type']).toContain('application/json');
  });

  it('lists collections with the authenticated account fence', async () => {
    const collections = [{
      id: 'collection-1', name: 'Watch later', privacy: 'private',
      source: 'user_created', item_count: 2,
    }];
    (global.fetch as any).mockResolvedValue(jsonResponse(200, collections));

    await expect(getCollections({ expectedUserId: 'user-a' })).resolves.toEqual(collections);

    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/v1/collections');
    expect(init.method).toBe('GET');
    expect(init.headers.Authorization).toBe('Bearer token-a');
  });

  it('encodes the item filter when listing collections and keeps the authenticated account fence', async () => {
    (global.fetch as any).mockResolvedValue(jsonResponse(200, []));

    await getCollections({ itemId: 'item/1', expectedUserId: 'user-a' });

    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/v1/collections?item_id=item%2F1');
    expect(init.method).toBe('GET');
    expect(init.headers.Authorization).toBe('Bearer token-a');
  });

  it('creates a collection with a JSON name body and the authenticated account fence', async () => {
    const collection = {
      id: 'collection-1', name: 'Watch later', privacy: 'private',
      source: 'user_created', item_count: 0,
    };
    (global.fetch as any).mockResolvedValue(jsonResponse(201, collection));

    await expect(createCollection('Watch later', { expectedUserId: 'user-a' }))
      .resolves.toEqual(collection);

    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/v1/collections');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ name: 'Watch later' });
    expect(init.headers['Content-Type']).toContain('application/json');
    expect(init.headers.Authorization).toBe('Bearer token-a');
  });

  it('adds a collection item using encoded path IDs and the authenticated account fence', async () => {
    (global.fetch as any).mockResolvedValue(jsonResponse(204, null));

    await addItemToCollection('collection/with space', 'item?one/#', {
      expectedUserId: 'user-a',
    });

    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/v1/collections/collection%2Fwith%20space/items/item%3Fone%2F%23');
    expect(init.method).toBe('PUT');
    expect(init.headers.Authorization).toBe('Bearer token-a');
  });

  it('removes a collection item using encoded path IDs and the authenticated account fence', async () => {
    (global.fetch as any).mockResolvedValue(jsonResponse(204, null));

    await removeItemFromCollection('collection/with space', 'item?one/#', {
      expectedUserId: 'user-a',
    });

    const [url, init] = (global.fetch as any).mock.calls[0];
    expect(url).toContain('/v1/collections/collection%2Fwith%20space/items/item%3Fone%2F%23');
    expect(init.method).toBe('DELETE');
    expect(init.headers.Authorization).toBe('Bearer token-a');
  });

  it('rejects collection requests before sending when the active session belongs to another account', async () => {
    await expect(getCollections({ expectedUserId: 'user-b' }))
      .rejects.toMatchObject({ code: 'account_scope_mismatch' });
    await expect(createCollection('Private', { expectedUserId: 'user-b' }))
      .rejects.toMatchObject({ code: 'account_scope_mismatch' });
    await expect(addItemToCollection('collection-1', 'item-1', { expectedUserId: 'user-b' }))
      .rejects.toMatchObject({ code: 'account_scope_mismatch' });
    await expect(removeItemFromCollection('collection-1', 'item-1', { expectedUserId: 'user-b' }))
      .rejects.toMatchObject({ code: 'account_scope_mismatch' });

    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('reports a deadline separately from a caller abort', async () => {
    (global.fetch as any).mockImplementation((_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true });
      }),
    );

    const timedOut = api.get('/v1/items', { timeoutMs: 40 });
    const timeoutAssertion = expect(timedOut).rejects.toMatchObject({ code: 'request_timeout' });
    await jest.advanceTimersByTimeAsync(40);
    await timeoutAssertion;

    const controller = new AbortController();
    const cancelled = api.get('/v1/items', { signal: controller.signal, timeoutMs: 1000 });
    const cancelledAssertion = expect(cancelled).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    await cancelledAssertion;
  });

  it('bounds session hydration as part of the request deadline', async () => {
    auth.getSession.mockReturnValue(new Promise(() => undefined));

    const request = api.get('/v1/me', { timeoutMs: 40 });
    const timeoutAssertion = expect(request).rejects.toMatchObject({ code: 'request_timeout' });
    await jest.advanceTimersByTimeAsync(40);

    await timeoutAssertion;
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('keeps an in-flight request bound to its account when the active account changes', async () => {
    auth.getSession
      .mockResolvedValueOnce({ data: { session: session('old-user', 'old-token') } })
      .mockResolvedValueOnce({ data: { session: session('new-user', 'new-token') } });

    const pendingResponses: Array<(response: Response) => void> = [];
    const fetchStarted: Array<Promise<void>> = [];
    const notifyFetchStarted: Array<() => void> = [];
    for (let index = 0; index < 2; index += 1) {
      fetchStarted.push(new Promise<void>((resolve) => notifyFetchStarted.push(resolve)));
    }
    (global.fetch as any).mockImplementation((_url: string, _init: RequestInit) => {
      notifyFetchStarted[pendingResponses.length]?.();
      return new Promise<Response>((resolve) => pendingResponses.push(resolve));
    });

    const oldAccountRequest = api.get('/v1/me');
    await fetchStarted[0];
    const newAccountRequest = api.get('/v1/me');
    await fetchStarted[1];

    // The new account can make a request while the old account's response is
    // still pending; each request must keep the token captured at its start.
    expect((global.fetch as any).mock.calls.map((call: any[]) => call[1].headers.Authorization))
      .toEqual(['Bearer old-token', 'Bearer new-token']);

    pendingResponses[1](jsonResponse(200, { id: 'new-user' }));
    await expect(newAccountRequest).resolves.toEqual({ id: 'new-user' });
    pendingResponses[0](jsonResponse(401, { detail: { code: 'expired', message: 'Expired' } }));

    await expect(oldAccountRequest).rejects.toMatchObject({
      status: 401,
      code: 'expired',
      message: 'Expired',
    } satisfies Partial<ApiError>);

    expect(auth.signOut).not.toHaveBeenCalled();
    expect(auth.getSession).toHaveBeenCalledTimes(2);
  });

  it('returns a structured session rejection without an unsafe sign-out', async () => {
    (global.fetch as any).mockResolvedValue(jsonResponse(401, { detail: 'session expired' }));

    await expect(api.get('/v1/me')).rejects.toMatchObject({
      status: 401,
      code: 'session_rejected',
    });

    expect(auth.signOut).not.toHaveBeenCalled();
  });

  it('rejects an account-scoped request before sending the wrong session token', async () => {
    await expect(
      api.post(
        '/v1/saves',
        { url: 'https://example.test/item', source: 'share_extension' },
        { expectedUserId: 'user-b' },
      ),
    ).rejects.toMatchObject({ code: 'account_scope_mismatch' });

    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('reuses a caller-supplied idempotency key on retries', async () => {
    (global.fetch as any).mockResolvedValue(jsonResponse(200, { accepted: true }));
    const key = 'save-user-a-item-123';

    await api.post('/v1/items', { source_url: 'https://example.test/item' }, { idempotencyKey: key });
    await api.post('/v1/items', { source_url: 'https://example.test/item' }, { idempotencyKey: key });

    expect((global.fetch as any).mock.calls.map((call: any[]) => call[1].headers['Idempotency-Key']))
      .toEqual([key, key]);
  });

  it('signs out only this session and surfaces Supabase auth/storage errors', async () => {
    auth.signOut.mockReset().mockResolvedValue({ error: null });
    await signOutCurrentSession();
    expect(auth.signOut).toHaveBeenCalledWith({ scope: 'local' });

    const signOutError = new Error('secure storage unavailable');
    auth.signOut.mockResolvedValueOnce({ error: signOutError });
    await expect(signOutCurrentSession()).rejects.toBe(signOutError);
  });
});
