/**
 * API client — every backend call goes through here.
 *
 * Authenticated requests capture the session that supplied their bearer token.
 * A 401 is returned to the caller without mutating auth state: Supabase has no
 * compare-and-sign-out operation, so a delayed response could clear a new login.
 * Request bodies may be JSON objects or FormData. Callers may provide a stable
 * idempotency key when retrying a logical mutation.
 */
import { supabase } from '@/lib/supabase';

const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL ||
  (__DEV__ ? 'http://localhost:8000' : 'https://api.spillthereel.app');

export type ApiErrorCode = string;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
    message: string,
    public readonly code: ApiErrorCode = 'http_error',
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

type RequestBody = Record<string, unknown> | FormData | null;

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: RequestBody;
  /** Include auth header? Default true. Set false for public routes. */
  auth?: boolean;
  /** Caller cancellation. User cancellation remains an AbortError. */
  signal?: AbortSignal;
  /** Per-request total fetch deadline in milliseconds. */
  timeoutMs?: number;
  /** Reuse the same value when retrying the same logical mutation. */
  idempotencyKey?: string;
  /** Fail closed if the current session no longer belongs to the queue owner. */
  expectedUserId?: string;
}

const DEFAULT_TIMEOUT_MS = 15_000;

interface RequestAbort {
  signal: AbortSignal;
  didTimeout: () => boolean;
  cleanup: () => void;
}

function createRequestAbort(caller: AbortSignal | undefined, timeoutMs: number): RequestAbort {
  const controller = new AbortController();
  let timedOut = false;
  const onCallerAbort = (): void => {
    controller.abort(caller?.reason ?? createAbortError());
  };

  if (caller?.aborted) onCallerAbort();
  else caller?.addEventListener('abort', onCallerAbort, { once: true });

  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(createTimeoutError());
  }, timeoutMs);

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    cleanup: () => {
      clearTimeout(timer);
      caller?.removeEventListener('abort', onCallerAbort);
    },
  };
}

function createAbortError(): Error {
  const error = new Error('The request was cancelled.');
  error.name = 'AbortError';
  return error;
}

function createTimeoutError(): Error {
  const error = new Error('The request deadline elapsed.');
  error.name = 'TimeoutError';
  return error;
}

function isFormData(body: RequestBody): body is FormData {
  return typeof FormData !== 'undefined' && body instanceof FormData;
}

async function getSessionBeforeDeadline(
  signal: AbortSignal,
  didTimeout: () => boolean,
): Promise<Awaited<ReturnType<typeof supabase.auth.getSession>>> {
  let rejectOnAbort: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    rejectOnAbort = () => reject(didTimeout() ? timeoutApiError() : createAbortError());
    if (signal.aborted) rejectOnAbort();
    else signal.addEventListener('abort', rejectOnAbort, { once: true });
  });
  try {
    return await Promise.race([supabase.auth.getSession(), aborted]);
  } finally {
    if (rejectOnAbort) signal.removeEventListener('abort', rejectOnAbort);
  }
}

function errorDetails(payload: unknown, status: number): { message: string; code: ApiErrorCode } {
  if (typeof payload === 'object' && payload !== null) {
    const root = payload as Record<string, unknown>;
    const detail = typeof root.detail === 'object' && root.detail !== null
      ? (root.detail as Record<string, unknown>)
      : root;
    const messageValue = detail.message ?? detail.detail ?? root.message ?? root.detail;
    const codeValue = detail.code ?? root.code;
    return {
      message: typeof messageValue === 'string' ? messageValue : `HTTP ${status}`,
      code: typeof codeValue === 'string' ? codeValue : 'http_error',
    };
  }
  return {
    message: typeof payload === 'string' && payload.length > 0 ? payload : `HTTP ${status}`,
    code: 'http_error',
  };
}

async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = 'GET',
    body = null,
    auth = true,
    signal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    idempotencyKey,
    expectedUserId,
  } = options;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new RangeError('timeoutMs must be a positive finite number');
  }

  const requestAbort = createRequestAbort(signal, timeoutMs);
  let capturedAccessToken: string | undefined;
  try {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== null && !isFormData(body)) headers['Content-Type'] = 'application/json';
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;

    if (auth) {
      const { data } = await getSessionBeforeDeadline(
        requestAbort.signal,
        requestAbort.didTimeout,
      );
      if (requestAbort.signal.aborted) {
        if (requestAbort.didTimeout()) throw timeoutApiError();
        throw createAbortError();
      }
      capturedAccessToken = data.session?.access_token;
      if (!capturedAccessToken || !data.session?.user.id) {
        throw new ApiError(401, null, 'no_active_session', 'no_active_session');
      }
      if (expectedUserId && data.session.user.id !== expectedUserId) {
        throw new ApiError(401, null, 'account_scope_mismatch', 'account_scope_mismatch');
      }
      headers.Authorization = `Bearer ${capturedAccessToken}`;
    }

    let response: Response;
    try {
      response = await fetch(`${API_BASE_URL}${path}`, {
        method,
        headers,
        body: body === null ? undefined : isFormData(body) ? body : JSON.stringify(body),
        signal: requestAbort.signal,
      });
    } catch (error) {
      if (requestAbort.didTimeout()) throw timeoutApiError();
      if (signal?.aborted) throw error;
      throw error;
    }

    if (requestAbort.signal.aborted) {
      if (requestAbort.didTimeout()) throw timeoutApiError();
      throw createAbortError();
    }
    const isJson = response.headers.get('content-type')?.includes('application/json');
    const payload = isJson ? await response.json() : await response.text();
    if (requestAbort.signal.aborted) {
      if (requestAbort.didTimeout()) throw timeoutApiError();
      throw createAbortError();
    }

    if (!response.ok) {
      const details = errorDetails(payload, response.status);
      throw new ApiError(
        response.status,
        payload,
        details.message,
        response.status === 401 && details.code === 'http_error'
          ? 'session_rejected'
          : details.code,
      );
    }

    return payload as T;
  } finally {
    requestAbort.cleanup();
  }
}

function timeoutApiError(): ApiError {
  return new ApiError(0, null, 'request_timeout', 'request_timeout');
}

export const api = {
  get: <T,>(path: string, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'GET' }),

  post: <T,>(path: string, body: RequestBody, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'POST', body }),

  put: <T,>(path: string, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'PUT' }),

  patch: <T,>(path: string, body: RequestBody, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'PATCH', body }),

  delete: <T,>(path: string, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'DELETE' }),
};

export interface SearchFilters {
  platforms?: string[];
  categories?: string[];
  collection_ids?: string[];
  only_fully_indexed?: boolean;
  since?: string;
}

export interface SearchCard {
  item_id: string;
  title: string;
  creator?: string;
  platform: string;
  thumbnail_url?: string;
  deep_link: string;
  score: number;
}

export interface SearchCitation {
  item_id: string;
  index: number;
  span?: string;
}

export interface SearchMeta {
  latency_ms: number;
  hits: number;
  model: string;
  fallback?: boolean;
}

export interface SearchResponse {
  text: string;
  cards: SearchCard[];
  citations: SearchCitation[];
  meta: SearchMeta;
}

export interface SearchOptions {
  top_k?: number;
  signal?: AbortSignal;
  timeoutMs?: number;
  idempotencyKey?: string;
}

export function search(
  query: string,
  filters?: SearchFilters,
  opts?: SearchOptions,
): Promise<SearchResponse> {
  return api.post<SearchResponse>(
    '/v1/search',
    {
      query,
      ...(filters ? { filters } : null),
      ...(typeof opts?.top_k === 'number' ? { top_k: opts.top_k } : null),
    },
    { ...opts, timeoutMs: opts?.timeoutMs ?? 25_000 },
  );
}

export interface ItemDetail {
  id: string;
  source_url: string;
  platform: string;
  state: string;
  saved_at: string;
  title: string | null;
  summary: string | null;
  thumbnail_url: string | null;
  owner_username: string | null;
  transcript: string | null;
  on_screen_text: string | null;
  caption: string | null;
  hashtags: string[];
  owner_name: string | null;
  owner_url: string | null;
  duration_seconds: number | null;
  failure_reason: string | null;
}

export function getItem(id: string, opts?: { signal?: AbortSignal; timeoutMs?: number }): Promise<ItemDetail> {
  return api.get<ItemDetail>(`/v1/items/${encodeURIComponent(id)}`, opts);
}

export interface ItemActionResponse {
  item_id: string;
  state: string;
}

export function retryItem(
  id: string,
  opts?: { signal?: AbortSignal; timeoutMs?: number; idempotencyKey?: string },
): Promise<ItemActionResponse> {
  return api.post<ItemActionResponse>(`/v1/items/${encodeURIComponent(id)}/retry`, {}, opts);
}

export function deleteMe(
  opts?: { signal?: AbortSignal; timeoutMs?: number; idempotencyKey?: string },
): Promise<{ status: string }> {
  return api.delete<{ status: string }>('/v1/me', opts);
}

export interface UserCollection {
  id: string;
  name: string;
  privacy: string;
  source: string;
  item_count: number;
  contains_item: boolean;
}

export interface CollectionRequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  expectedUserId?: string;
  itemId?: string;
}

export function getCollections(opts?: CollectionRequestOptions): Promise<UserCollection[]> {
  const { itemId, ...requestOptions } = opts ?? {};
  const path = itemId
    ? `/v1/collections?item_id=${encodeURIComponent(itemId)}`
    : '/v1/collections';
  return api.get<UserCollection[]>(path, requestOptions);
}

export function createCollection(
  name: string,
  opts?: CollectionRequestOptions,
): Promise<UserCollection> {
  return api.post<UserCollection>('/v1/collections', { name }, opts);
}

export function addItemToCollection(
  collectionId: string,
  itemId: string,
  opts?: CollectionRequestOptions,
): Promise<void> {
  return api.put<void>(
    `/v1/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`,
    opts,
  );
}

export function removeItemFromCollection(
  collectionId: string,
  itemId: string,
  opts?: CollectionRequestOptions,
): Promise<void> {
  return api.delete<void>(
    `/v1/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(itemId)}`,
    opts,
  );
}

export interface ImportPreview {
  preview_id: string;
  expires_at: string;
  total_candidates: number;
}

export interface ImportPreviewCandidate {
  candidate_id: string;
  ordinal: number;
  kind: 'media' | 'audio';
  source_bucket: 'instagram' | 'other';
  url: string | null;
  title: string | null;
  artist: string | null;
  saved_at: string | null;
  collection_names: string[];
}

export interface ImportPreviewCandidatesPage {
  candidates: ImportPreviewCandidate[];
  next_cursor: string | null;
}

export interface ImportPreviewCommitResult {
  import_id: string;
  state: string;
  total_parsed: number;
}

export interface ImportPreviewRequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  expectedUserId?: string;
}

export function createImportPreview(
  uri: string,
  fileName: string,
  idempotencyKey: string,
  opts?: ImportPreviewRequestOptions,
): Promise<ImportPreview> {
  const body = new FormData();
  body.append('file', {
    uri,
    name: fileName,
    type: fileName.toLowerCase().endsWith('.zip') ? 'application/zip' : 'text/html',
  } as unknown as Blob);
  return api.post<ImportPreview>('/v1/imports/previews', body, {
    ...opts,
    idempotencyKey,
    timeoutMs: opts?.timeoutMs ?? 60_000,
  });
}

export function getImportPreviewCandidates(
  previewId: string,
  options: ImportPreviewRequestOptions & { limit?: number; cursor?: string | null } = {},
): Promise<ImportPreviewCandidatesPage> {
  const query = new URLSearchParams({ limit: String(options.limit ?? 100) });
  if (options.cursor) query.set('cursor', options.cursor);
  return api.get<ImportPreviewCandidatesPage>(
    `/v1/imports/previews/${encodeURIComponent(previewId)}/candidates?${query.toString()}`,
    options,
  );
}

export function commitImportPreview(
  previewId: string,
  candidateIds: string[],
  opts?: ImportPreviewRequestOptions,
): Promise<ImportPreviewCommitResult> {
  return api.post<ImportPreviewCommitResult>(
    `/v1/imports/previews/${encodeURIComponent(previewId)}/commit`,
    { candidate_ids: candidateIds },
    { ...opts, timeoutMs: opts?.timeoutMs ?? 30_000 },
  );
}
