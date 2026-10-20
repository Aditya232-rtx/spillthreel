import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, AppState, Pressable, ScrollView, Text, View } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import { Svg, Path, Circle } from 'react-native-svg';
import { SafeAreaView } from 'react-native-safe-area-context';
import { PillButton } from '@/components/PillButton';
import { ProgressDots } from '@/components/ProgressDots';
import {
  ApiError,
  api,
  commitImportPreview,
  createImportPreview,
  getImportPreviewCandidates,
  type ImportPreview,
  type ImportPreviewCandidate,
} from '@/lib/api';
import { useAuth } from '@/hooks/useAuth';
import { isValidOpaqueImportId } from '@/lib/notification-routing';
import { getPendingEmail, resendConfirmation } from '@/lib/verification';
import { border, color, font, fontSize, radius, space } from '@/theme/tokens';

type ImportFile = {
  uri: string;
  name: string;
  type: string;
  idempotencyKey: string;
  preview?: ImportPreview;
  selectedCandidateIds?: string[];
};

type ImportStatus = {
  import_id: string;
  state: string;
  total_parsed: number;
  text_indexed_done: number;
  full_queued: number;
  full_done: number;
  failed: number;
  failure_reason: string | null;
  started_at: string | null;
  finished_at: string | null;
};

type ImportsPage = { imports: ImportStatus[]; next_cursor: string | null };
const STATUS_REFRESH_MS = 4000;
const ACTIVE_STATES = new Set(['processing', 'indexing']);
const PENDING_IMPORT_KEY_PREFIX = 'spill/import-pending/v1/';

function pendingImportStorageKey(userId: string): string {
  return `${PENDING_IMPORT_KEY_PREFIX}${userId}`;
}

function isImportFile(value: unknown): value is ImportFile {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<ImportFile>;
  return typeof candidate.uri === 'string'
    && typeof candidate.name === 'string'
    && typeof candidate.type === 'string'
    && typeof candidate.idempotencyKey === 'string'
    && (candidate.preview === undefined || isImportPreview(candidate.preview))
    && (candidate.selectedCandidateIds === undefined || (
      Array.isArray(candidate.selectedCandidateIds)
      && candidate.selectedCandidateIds.every((id) => typeof id === 'string')
    ));
}

function isImportPreview(value: unknown): value is ImportPreview {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<ImportPreview>;
  return typeof candidate.preview_id === 'string'
    && typeof candidate.expires_at === 'string'
    && typeof candidate.total_candidates === 'number';
}

function idempotencyKey(): string {
  return `mobile-import-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isActiveImport(value: ImportStatus): boolean {
  return ACTIVE_STATES.has(value.state);
}

function isSupportedImportFileName(name: string): boolean {
  const lowerName = name.toLowerCase();
  return lowerName.endsWith('.zip') || name === 'saved_posts.html';
}

function importFileLabel(name: string): string {
  return name.toLowerCase().endsWith('.zip') ? 'ZIP archive' : 'saved posts HTML file';
}

function importDescription(value: ImportStatus): string {
  if (value.state === 'processing') return 'Your archive is being checked and queued.';
  if (value.state === 'indexing') return 'Accepted items are being processed for your library.';
  if (value.state === 'complete') {
    return value.failed > 0
      ? `Finished with ${value.full_done} ready and ${value.failed} needing attention.`
      : `${value.full_done} ${value.full_done === 1 ? 'item is' : 'items are'} ready in your library.`;
  }
  if (value.state === 'failed') return value.failure_reason || 'This archive could not be processed.';
  return `Import status: ${value.state}.`;
}

function isRetryableIntakeError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 0 || error.status >= 500 || error.status === 408 || error.status === 429;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'import_preview_retention_policy_not_configured') {
      return 'Import previews are temporarily unavailable while archive retention is being finalized. Your selected file is still on this device; you can retry later.';
    }
    if (error.code === 'import_preview_expired' || error.status === 410) {
      return 'This preview has expired. Create a fresh preview from your selected file to choose items again.';
    }
    if (error.code === 'import_allowance_exceeded') {
      return 'This account has used its one-time bulk import allowance. No items were added.';
    }
    if (error.code === 'bulk_import_policy_not_configured') {
      return 'Bulk imports are temporarily unavailable while the import policy is being set up.';
    }
    if (error.code === 'import_allowance_exceeded') {
      return 'This account has used its one-time bulk import allowance.';
    }
    if (error.code === 'import_upload_too_large' || error.code === 'import_archive_processing_limit') {
      return 'This export file is larger than the current import limits. Choose a smaller file.';
    }
    if (error.code === 'import_parser_at_capacity') return 'Import is busy right now. Try again shortly.';
    if (error.code === 'idempotency_key_payload_conflict') {
      return 'This retry key is linked to a different file. Select the export again to start a fresh request.';
    }
    if (error.status === 400) return 'This file could not be read as a supported Instagram export. Choose the ZIP or saved_posts.html from your download.';
    if (error.status === 401) return 'Your session has expired. Sign in again to continue.';
  }
  return 'We could not confirm the upload. Retry with the same selected file; the request is safe to repeat.';
}

export default function Import3Screen() {
  const router = useRouter();
  const params = useLocalSearchParams?.<{ notification_import_id?: string | string[] }>();
  const { session } = useAuth();
  const [file, setFile] = useState<ImportFile | null>(null);
  const [uploading, setUploading] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [candidatesBusy, setCandidatesBusy] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [candidates, setCandidates] = useState<ImportPreviewCandidate[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pageCursors, setPageCursors] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<Set<string>>(new Set());
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewUnavailable, setPreviewUnavailable] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [pickerBusy, setPickerBusy] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [currentImport, setCurrentImport] = useState<ImportStatus | null>(null);
  const [recentImports, setRecentImports] = useState<ImportStatus[]>([]);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [pendingEmail, setPendingEmailState] = useState<string | null>(null);
  const [resendNote, setResendNote] = useState<string | null>(null);
  const [resending, setResending] = useState(false);
  const accountId = session?.user.id ?? null;
  const rawNotificationImportId = params?.notification_import_id;
  const notificationImportId = Array.isArray(rawNotificationImportId)
    ? rawNotificationImportId[0]
    : rawNotificationImportId;
  const importRef = useRef<string | null>(null);
  const requestInFlight = useRef(false);
  const refreshingRef = useRef(false);
  const accountGenerationRef = useRef(0);

  useEffect(() => {
    if (!session) getPendingEmail().then(setPendingEmailState).catch(() => setPendingEmailState(null));
    else setPendingEmailState(null);
  }, [session]);

  useEffect(() => {
    let active = true;
    accountGenerationRef.current += 1;
    refreshingRef.current = false;
    setFile(null);
    setPreview(null);
    setCandidates([]);
    setNextCursor(null);
    setPageCursors([null]);
    setPageIndex(0);
    setSelectedCandidateIds(new Set());
    setPreviewError(null);
    setPreviewUnavailable(false);
    setCommitting(false);
    setCandidatesBusy(false);
    setCurrentImport(null);
    setRecentImports([]);
    setUploading(false);
    setPreviewBusy(false);
    setPickerBusy(false);
    setUploadError(null);
    setRetryable(false);
    importRef.current = null;
    requestInFlight.current = false;
    if (!accountId) return () => { active = false; };
    void AsyncStorage.getItem(pendingImportStorageKey(accountId)).then((raw) => {
      if (!active || !raw) return;
      try {
        const value: unknown = JSON.parse(raw);
        if (isImportFile(value)) {
          setFile(value);
          if (value.preview) {
            setPreview(value.preview);
            setSelectedCandidateIds(new Set(value.selectedCandidateIds ?? []));
          }
        }
      } catch {
        void AsyncStorage.removeItem(pendingImportStorageKey(accountId));
      }
    }).catch(() => undefined);
    return () => { active = false; };
  }, [accountId]);

  const refreshImport = useCallback(async (importId?: string) => {
    if (!session || refreshingRef.current) return;
    const generation = accountGenerationRef.current;
    refreshingRef.current = true;
    setRefreshing(true);
    try {
      const selectedId = importId ?? importRef.current;
      if (selectedId) {
        const status = await api.get<ImportStatus>(`/v1/imports/${encodeURIComponent(selectedId)}`, { timeoutMs: 15_000, expectedUserId: accountId ?? undefined });
        if (generation !== accountGenerationRef.current) return;
        if (status.import_id !== selectedId) throw new Error('Import response did not match the requested import.');
        setCurrentImport(status);
        importRef.current = status.import_id;
      } else {
        const page = await api.get<ImportsPage>('/v1/imports?limit=20', { timeoutMs: 15_000, expectedUserId: accountId ?? undefined });
        if (generation !== accountGenerationRef.current) return;
        // A list result cannot prove which request produced it after a lost
        // POST response. Let the user choose a persisted import explicitly.
        setRecentImports(page.imports);
      }
      setStatusError(null);
    } catch {
      if (generation === accountGenerationRef.current) {
        setStatusError('Could not refresh import status. If the request was accepted, it remains saved; try again.');
      }
    } finally {
      if (generation === accountGenerationRef.current) {
        refreshingRef.current = false;
        setRefreshing(false);
      }
    }
  }, [accountId, session]);

  useFocusEffect(useCallback(() => {
    if (!session) return;
    if (notificationImportId && isValidOpaqueImportId(notificationImportId)) {
      importRef.current = notificationImportId;
      void refreshImport(notificationImportId);
      return;
    }
    void refreshImport();
    if (preview && candidates.length === 0 && !candidatesBusy && !previewError && accountId) {
      void loadCandidatePage(preview, null, accountGenerationRef.current, accountId);
    }
  }, [accountId, candidates.length, candidatesBusy, notificationImportId, preview, previewError, refreshImport, session]));

  useEffect(() => {
    if (!session || !currentImport || !isActiveImport(currentImport)) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    const poll = async () => {
      if (cancelled || AppState.currentState !== 'active') return;
      await refreshImport(currentImport.import_id);
      if (!cancelled) timer = setTimeout(poll, STATUS_REFRESH_MS);
    };
    timer = setTimeout(poll, STATUS_REFRESH_MS);
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active' && !cancelled) {
        if (timer) clearTimeout(timer);
        void refreshImport(currentImport.import_id);
        timer = setTimeout(poll, STATUS_REFRESH_MS);
      }
    });
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      subscription.remove();
    };
  }, [currentImport, refreshImport, session]);

  async function handleResend(): Promise<void> {
    if (!pendingEmail || resending) return;
    setResending(true);
    const error = await resendConfirmation(pendingEmail);
    setResending(false);
    setResendNote(error ?? `Fresh link sent to ${pendingEmail}.`);
  }

  async function pickFile(): Promise<void> {
    if (uploading || pickerBusy) return;
    const generation = accountGenerationRef.current;
    const requestAccountId = accountId;
    setPickerBusy(true);
    setUploadError(null);
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['application/zip', 'application/x-zip-compressed', 'text/html'],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (generation !== accountGenerationRef.current) return;
      const selected = result.assets?.[0];
      if (!result.canceled && selected) {
        if (!isSupportedImportFileName(selected.name)) {
          setUploadError('Choose the ZIP export or its saved_posts.html file.');
          setRetryable(false);
          return;
        }
        const selection: ImportFile = {
          uri: selected.uri,
          name: selected.name,
          type: selected.name.toLowerCase().endsWith('.zip') ? 'application/zip' : 'text/html',
          // Choosing a new file starts a new logical preview request. Retries
          // use the key already stored with the currently selected file.
          idempotencyKey: idempotencyKey(),
        };
        setFile(selection);
        setPreview(null);
        setCandidates([]);
        setNextCursor(null);
        setPageCursors([null]);
        setPageIndex(0);
        setSelectedCandidateIds(new Set());
        setPreviewError(null);
        setPreviewUnavailable(false);
        if (requestAccountId) {
          try {
            await AsyncStorage.setItem(pendingImportStorageKey(requestAccountId), JSON.stringify(selection));
          } catch {
            if (generation === accountGenerationRef.current) {
              setUploadError('The selected file can be retried now, but this device could not save its retry key for later.');
            }
          }
        }
        if (generation !== accountGenerationRef.current) return;
        setUploadError(null);
        setRetryable(false);
        setCurrentImport(null);
        importRef.current = null;
      }
    } catch {
      if (generation === accountGenerationRef.current) {
        setUploadError('Files could not open. Try choosing the export again.');
        setRetryable(false);
      }
    } finally {
      if (generation === accountGenerationRef.current) setPickerBusy(false);
    }
  }

  async function savePendingImport(nextFile: ImportFile, ownerId: string): Promise<void> {
    await AsyncStorage.setItem(pendingImportStorageKey(ownerId), JSON.stringify(nextFile));
  }

  async function createPreview(sourceFile: ImportFile | null = file): Promise<void> {
    if (!sourceFile || !session || !accountId || requestInFlight.current) return;
    const generation = accountGenerationRef.current;
    const requestAccountId = accountId;
    requestInFlight.current = true;
    setPreviewBusy(true);
    setUploading(true);
    setPreviewError(null);
    setPreviewUnavailable(false);
    setRetryable(false);
    try {
      const created = await createImportPreview(sourceFile.uri, sourceFile.name, sourceFile.idempotencyKey, {
        timeoutMs: 60_000,
        expectedUserId: requestAccountId,
      });
      if (generation !== accountGenerationRef.current) return;
      const updatedFile: ImportFile = { ...sourceFile, preview: created, selectedCandidateIds: [] };
      setPreview(created);
      setSelectedCandidateIds(new Set());
      setCandidates([]);
      setNextCursor(null);
      setPageCursors([null]);
      setPageIndex(0);
      await savePendingImport(updatedFile, requestAccountId);
      setFile(updatedFile);
      await loadCandidatePage(created, null, generation, requestAccountId);
    } catch (error) {
      if (generation === accountGenerationRef.current) {
        setPreviewError(errorMessage(error));
        setPreviewUnavailable(error instanceof ApiError && error.code === 'import_preview_retention_policy_not_configured');
        setRetryable(isRetryableIntakeError(error));
      }
    } finally {
      requestInFlight.current = false;
      if (generation === accountGenerationRef.current) {
        setUploading(false);
        setPreviewBusy(false);
      }
    }
  }

  async function renewExpiredPreview(): Promise<void> {
    if (!file || !accountId || previewBusy || committing) return;
    const freshFile: ImportFile = {
      uri: file.uri,
      name: file.name,
      type: file.type,
      idempotencyKey: idempotencyKey(),
      selectedCandidateIds: [],
    };
    setFile(freshFile);
    setPreview(null);
    setCandidates([]);
    setSelectedCandidateIds(new Set());
    setPageCursors([null]);
    setPageIndex(0);
    setPreviewError(null);
    try {
      await savePendingImport(freshFile, accountId);
      await createPreview(freshFile);
    } catch {
      setPreviewError('Your export file is still selected, but its retry details could not be saved on this device. Try again while this screen remains open.');
    }
  }

  async function loadCandidatePage(
    currentPreview: ImportPreview,
    cursor: string | null,
    generation: number,
    ownerId: string,
  ): Promise<void> {
    if (Date.parse(currentPreview.expires_at) <= Date.now()) {
      setPreviewError('This preview has expired. Create a fresh preview from your selected export file to choose items again.');
      setCandidates([]);
      return;
    }
    setCandidatesBusy(true);
    setPreviewError(null);
    try {
      const page = await getImportPreviewCandidates(currentPreview.preview_id, {
        limit: 100,
        cursor,
        expectedUserId: ownerId,
      });
      if (generation !== accountGenerationRef.current) return;
      setCandidates(page.candidates);
      setNextCursor(page.next_cursor);
    } catch (error) {
      if (generation === accountGenerationRef.current) {
        setPreviewError(errorMessage(error));
        if (error instanceof ApiError && (error.status === 410 || error.code === 'import_preview_expired')) {
          setPreview({ ...currentPreview, expires_at: new Date(0).toISOString() });
          setCandidates([]);
        }
      }
    } finally {
      if (generation === accountGenerationRef.current) setCandidatesBusy(false);
    }
  }

  function changeCandidatePage(cursor: string | null, index: number): void {
    if (!preview || candidatesBusy || !accountId) return;
    const generation = accountGenerationRef.current;
    setPageIndex(index);
    void loadCandidatePage(preview, cursor, generation, accountId);
  }

  function goToNextCandidatePage(): void {
    if (!nextCursor || candidatesBusy) return;
    const cursor = nextCursor;
    const nextIndex = pageIndex + 1;
    setPageCursors((pages) => [...pages.slice(0, nextIndex), cursor]);
    changeCandidatePage(cursor, nextIndex);
  }

  function toggleCandidate(candidateId: string): void {
    if (!file || !accountId || committing) return;
    const next = new Set(selectedCandidateIds);
    if (next.has(candidateId)) next.delete(candidateId);
    else if (next.size < 1000) next.add(candidateId);
    else {
      setPreviewError('You can select up to 1,000 items in one import.');
      return;
    }
    setSelectedCandidateIds(next);
    const updatedFile = { ...file, selectedCandidateIds: [...next] };
    setFile(updatedFile);
    void savePendingImport(updatedFile, accountId).catch(() => {
      setPreviewError('Your selection is active, but this device could not save it for later. Keep the app open until you confirm the import.');
    });
  }

  async function commitSelection(): Promise<void> {
    if (!preview || !file || !accountId || selectedCandidateIds.size === 0 || committing) return;
    if (Date.parse(preview.expires_at) <= Date.now()) {
      setPreviewError('This preview has expired. Create a fresh preview from your selected export file to choose items again.');
      await renewExpiredPreview();
      return;
    }
    const generation = accountGenerationRef.current;
    const requestAccountId = accountId;
    setCommitting(true);
    setPreviewError(null);
    try {
      const result = await commitImportPreview(preview.preview_id, [...selectedCandidateIds], {
        timeoutMs: 30_000,
        expectedUserId: requestAccountId,
      });
      if (generation !== accountGenerationRef.current) return;
      importRef.current = result.import_id;
      const status = await api.get<ImportStatus>(`/v1/imports/${encodeURIComponent(result.import_id)}`, {
        timeoutMs: 15_000,
        expectedUserId: requestAccountId,
      });
      if (generation !== accountGenerationRef.current) return;
      setCurrentImport(status);
      setRecentImports((imports) => [status, ...imports.filter((item) => item.import_id !== status.import_id)].slice(0, 20));
      setStatusError(null);
      setFile(null);
      setPreview(null);
      await AsyncStorage.removeItem(pendingImportStorageKey(requestAccountId));
    } catch (error) {
      if (generation === accountGenerationRef.current) {
        setPreviewError(errorMessage(error));
        if (error instanceof ApiError && (error.status === 410 || error.code === 'import_preview_expired')) {
          setPreview({ ...preview, expires_at: new Date(0).toISOString() });
          setCandidates([]);
        }
        // Retain the exact selection. Retrying the same set is idempotent after
        // an ambiguous response and recovers the original import result.
      }
    } finally {
      if (generation === accountGenerationRef.current) setCommitting(false);
    }
  }

  function confirmSelection(): void {
    const count = selectedCandidateIds.size;
    if (count === 0) return;
    Alert.alert(
      `Import ${count} ${count === 1 ? 'item' : 'items'}?`,
      'Each selected item will be fully processed, including its media, so it can be searched. This may take time.',
      [
        { text: 'Review selection', style: 'cancel' },
        { text: 'Start full import', onPress: () => void commitSelection() },
      ],
    );
  }

  const active = currentImport ? isActiveImport(currentImport) : false;
  const finished = currentImport && !active;
  const previewExpired = Boolean(preview && Date.parse(preview.expires_at) <= Date.now());
  const primaryLabel = uploading
    ? previewBusy ? 'Preparing preview…' : 'Starting import…'
    : active
      ? 'Import in progress'
      : file
        ? preview
          ? `Review ${selectedCandidateIds.size} selected`
          : previewExpired ? 'Create a fresh preview' : retryable ? 'Retry preview' : 'Create preview'
        : finished ? 'Open your library →' : 'Choose an export file';

  function handlePrimary(): void {
    if (uploading || active || committing) return;
    if (previewExpired) void renewExpiredPreview();
    else if (file && preview && selectedCandidateIds.size > 0) confirmSelection();
    else if (file && session) void createPreview();
    else if (finished) router.replace('/(app)/library' as never);
    else if (!session) router.replace('/(auth)/login');
    else void pickFile();
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: color.cream }} edges={['top', 'bottom']}>
      <ScrollView
        contentContainerStyle={{ flexGrow: 1, paddingHorizontal: space.xl, paddingTop: space.lg, paddingBottom: space.xl, gap: space.xl }}
        keyboardShouldPersistTaps="handled"
      >
        <ProgressDots step={3} />

        <Text style={{ fontFamily: font.display, fontSize: fontSize.displayMd, lineHeight: 36, letterSpacing: -0.3, color: color.ink, textTransform: 'uppercase' }}>
          BRING YOUR{ '\n' }SAVED REELS.
        </Text>

        {!session && pendingEmail ? (
          <View style={{ backgroundColor: '#F6E3D3', borderWidth: border.standard, borderColor: color.ink, borderRadius: radius.md, padding: space.md, gap: space.sm }}>
            <Text style={{ fontFamily: font.display, fontSize: 15, color: color.ink, textTransform: 'uppercase' }}>Confirm your email to import</Text>
            <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
              We sent a link to {pendingEmail}. Confirm it, then return here to import your archive.
            </Text>
            <Pressable onPress={handleResend} disabled={resending} accessibilityRole="button" accessibilityLabel="Resend confirmation link" accessibilityState={{ disabled: resending, busy: resending }} style={{ minHeight: 48, justifyContent: 'center' }}>
              <Text style={{ fontFamily: font.body, fontSize: 14, color: color.coral, textDecorationLine: 'underline' }}>{resending ? 'Sending…' : 'Resend confirmation link'}</Text>
            </Pressable>
            {resendNote ? <Text accessibilityLiveRegion="polite" style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>{resendNote}</Text> : null}
          </View>
        ) : null}

        <View style={{ flex: 1, justifyContent: 'center', gap: space.xl, minHeight: 330 }}>
          <Pressable
            onPress={pickFile}
            disabled={uploading || pickerBusy || active}
            accessibilityRole="button"
            accessibilityLabel={file ? `Selected ${importFileLabel(file.name)} ${file.name}. Choose another export file` : 'Choose an Instagram export ZIP or saved_posts.html file'}
            accessibilityHint="Opens Files to select your Instagram data export ZIP or saved posts HTML file"
            accessibilityState={{ disabled: uploading || pickerBusy || active, busy: pickerBusy }}
            style={{ minHeight: 220, borderRadius: radius.xxl, borderWidth: border.standard, borderStyle: 'dashed', borderColor: color.ink, backgroundColor: color.cream, alignItems: 'center', justifyContent: 'center', gap: space.md, padding: space.xl, opacity: active ? 0.65 : 1 }}
          >
            <Svg viewBox="0 0 260 280" width={42} height={45} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
              <Path d="M 60 50 L 190 112 L 155 129 C 172 155, 172 198, 142 220 C 122 234, 100 223, 94 200 C 90 175, 102 158, 110 151 L 60 175 Z" fill={color.ink} stroke={color.ink} strokeWidth={9} strokeLinejoin="round" strokeLinecap="round" />
              <Circle cx={212} cy={232} r={17} fill={color.ink} stroke={color.ink} strokeWidth={9} />
            </Svg>
            <Text style={{ fontFamily: font.display, fontSize: 16, lineHeight: 22, color: color.ink, textAlign: 'center', textTransform: 'uppercase' }}>
              {file ? `${file.name.toLowerCase().endsWith('.zip') ? 'ZIP' : 'SAVED POSTS FILE'} SELECTED` : active ? 'IMPORT RUNNING' : pickerBusy ? 'OPENING FILES…' : 'CHOOSE YOUR INSTAGRAM EXPORT'}
            </Text>
            <Text numberOfLines={2} style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink, textAlign: 'center' }}>
              {file ? file.name : active ? 'You can leave this screen and return to check progress.' : 'Select the ZIP export or saved_posts.html from your Instagram data download.'}
            </Text>
          </Pressable>

          {uploadError ? (
            <View style={{ gap: space.sm }}>
              <Text accessibilityRole="alert" accessibilityLiveRegion="assertive" style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>{uploadError}</Text>
              {retryable ? <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>Your selected file and request key are kept so a retry won’t create a second import.</Text> : null}
            </View>
          ) : null}

          {file && !currentImport ? (
            <View style={{ gap: space.md, borderTopWidth: border.hairline, borderTopColor: color.ink, paddingTop: space.md }}>
              <Text style={{ fontFamily: font.display, fontSize: 16, lineHeight: 22, color: color.ink, textTransform: 'uppercase' }}>
                {preview ? 'Choose what to bring in' : 'Preview before importing'}
              </Text>
              <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
                {preview
                  ? `${selectedCandidateIds.size} selected · ${preview.total_candidates} found. Only selected items will be admitted, and each will receive full media processing.`
                  : 'We’ll inspect this export and show its items first. Nothing enters your library until you review and confirm your selection.'}
              </Text>
              {previewUnavailable ? (
                <Text accessibilityRole="alert" accessibilityLiveRegion="polite" style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
                  Archive retention policy is not configured yet. Your selected file and retry key are kept; there is no automatic import fallback.
                </Text>
              ) : null}
              {previewError ? (
                <Text accessibilityRole="alert" accessibilityLiveRegion="assertive" style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>{previewError}</Text>
              ) : null}
              {preview && !previewExpired && previewError && !candidatesBusy ? (
                <Pressable
                  onPress={() => changeCandidatePage(pageCursors[pageIndex] ?? null, pageIndex)}
                  accessibilityRole="button"
                  accessibilityLabel="Retry loading preview items"
                  style={{ minHeight: 48, justifyContent: 'center', alignSelf: 'flex-start' }}
                >
                  <Text style={{ fontFamily: font.body, fontSize: 14, color: color.ink, textDecorationLine: 'underline' }}>Retry loading items</Text>
                </Pressable>
              ) : null}
              {preview && previewExpired ? (
                <Text accessibilityRole="alert" style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
                  This preview has expired. Your file remains selected; create a fresh preview to continue.
                </Text>
              ) : null}
              {preview && !previewExpired ? (
                <>
                  {candidatesBusy ? (
                    <Text accessibilityLiveRegion="polite" style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>Loading items…</Text>
                  ) : candidates.length === 0 ? (
                    <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>No selectable items were found in this preview.</Text>
                  ) : candidates.map((candidate) => {
                    const selected = selectedCandidateIds.has(candidate.candidate_id);
                    const label = candidate.title || candidate.url || `Saved item ${candidate.ordinal}`;
                    const details = [candidate.source_bucket === 'instagram' ? 'Instagram' : 'Other app', candidate.kind, candidate.saved_at, ...(candidate.collection_names ?? [])].filter(Boolean).join(' · ');
                    return (
                      <Pressable
                        key={candidate.candidate_id}
                        onPress={() => toggleCandidate(candidate.candidate_id)}
                        disabled={committing}
                        accessibilityRole="checkbox"
                        accessibilityLabel={`${label}. ${details}`}
                        accessibilityState={{ checked: selected, disabled: committing }}
                        style={{ minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm, borderBottomWidth: border.hairline, borderBottomColor: color.ink }}
                      >
                        <View style={{ width: 24, height: 24, borderRadius: radius.sm, borderWidth: border.standard, borderColor: color.ink, backgroundColor: selected ? color.gold : color.cream, alignItems: 'center', justifyContent: 'center' }}>
                          {selected ? <Text style={{ fontFamily: font.body, fontSize: 15, color: color.ink }}>✓</Text> : null}
                        </View>
                        <View style={{ flex: 1, gap: 2 }}>
                          <Text numberOfLines={2} style={{ fontFamily: font.body, fontSize: 15, lineHeight: 21, color: color.ink }}>{label}</Text>
                          <Text numberOfLines={2} style={{ fontFamily: font.body, fontSize: 13, lineHeight: 18, color: color.ink }}>{details}</Text>
                        </View>
                      </Pressable>
                    );
                  })}
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: space.md }}>
                    {pageIndex > 0 ? (
                      <Pressable onPress={() => changeCandidatePage(pageCursors[pageIndex - 1] ?? null, pageIndex - 1)} disabled={candidatesBusy} accessibilityRole="button" style={{ minHeight: 48, justifyContent: 'center' }}>
                        <Text style={{ fontFamily: font.body, fontSize: 14, color: color.ink, textDecorationLine: 'underline' }}>Previous 100</Text>
                      </Pressable>
                    ) : <View />}
                    {nextCursor ? (
                      <Pressable onPress={goToNextCandidatePage} disabled={candidatesBusy} accessibilityRole="button" accessibilityState={{ disabled: candidatesBusy, busy: candidatesBusy }} style={{ minHeight: 48, justifyContent: 'center' }}>
                        <Text style={{ fontFamily: font.body, fontSize: 14, color: color.ink, textDecorationLine: 'underline' }}>{candidatesBusy ? 'Loading…' : 'Next 100 items'}</Text>
                      </Pressable>
                    ) : null}
                  </View>
                  <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
                    Select across pages. You can choose up to 1,000 items; your account’s one-time allowance is checked when you confirm.
                  </Text>
                </>
              ) : null}
            </View>
          ) : null}

          {currentImport ? (
            <View accessibilityLiveRegion={active ? 'polite' : 'none'} style={{ borderTopWidth: border.hairline, borderTopColor: color.ink, paddingTop: space.md, gap: space.xs }}>
              <Text style={{ fontFamily: font.display, fontSize: 15, color: color.ink, textTransform: 'uppercase' }}>{active ? 'Import status' : currentImport.state === 'complete' ? 'Import finished' : 'Import needs attention'}</Text>
              <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>{importDescription(currentImport)}</Text>
              <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
                {currentImport.full_done} ready · {currentImport.full_queued} queued · {currentImport.failed} failed
              </Text>
              {statusError ? <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>{statusError}</Text> : null}
              <Pressable onPress={() => void refreshImport(currentImport.import_id)} disabled={refreshing} accessibilityRole="button" accessibilityLabel="Refresh import status" accessibilityState={{ disabled: refreshing, busy: refreshing }} style={{ minHeight: 48, alignSelf: 'flex-start', justifyContent: 'center' }}>
                <Text style={{ fontFamily: font.body, fontSize: 14, color: color.coral, textDecorationLine: 'underline' }}>{refreshing ? 'Refreshing…' : 'Refresh status'}</Text>
              </Pressable>
            </View>
          ) : null}

          {!currentImport && recentImports.length > 0 ? (
            <View style={{ borderTopWidth: border.hairline, borderTopColor: color.ink, paddingTop: space.md, gap: space.xs }}>
              <Text style={{ fontFamily: font.display, fontSize: 15, color: color.ink, textTransform: 'uppercase' }}>Your recent imports</Text>
              {recentImports.slice(0, 5).map((item) => (
                <Pressable
                  key={item.import_id}
                  onPress={() => void refreshImport(item.import_id)}
                  accessibilityRole="button"
                  accessibilityLabel={`Open ${item.state} import with ${item.full_done} ready and ${item.full_queued} queued`}
                  style={{ minHeight: 48, justifyContent: 'center' }}
                >
                  <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink }}>
                    {item.state} · {item.full_done} ready · {item.full_queued} queued · {item.failed} failed
                  </Text>
                </Pressable>
              ))}
            </View>
          ) : null}

          <Text style={{ fontFamily: font.body, fontSize: 14, lineHeight: 20, color: color.ink, textAlign: 'center' }}>
            Your export is uploaded securely for preview and processing. Uncommitted preview details expire after one hour; selected media is fully processed so it can be searched.
          </Text>
        </View>

        <PillButton
          label={primaryLabel}
          variant={active ? 'outline' : file ? 'coral' : finished ? 'ink' : 'outline'}
          minHeight={48}
          fontSize={15}
          disabled={uploading || active || pickerBusy || committing || (Boolean(preview) && selectedCandidateIds.size === 0)}
          loading={uploading || committing}
          accessibilityHint={active ? 'Import processing continues in the background' : preview ? `Confirms ${selectedCandidateIds.size} selected items for full media processing` : file ? 'Creates a preview of this export without importing items' : finished ? 'Opens your library' : 'Select an Instagram export file'}
          onPress={handlePrimary}
        />
        <Pressable onPress={() => router.replace('/(app)/home')} accessibilityRole="button" accessibilityLabel="Not now, go to Home" style={{ alignSelf: 'center', minHeight: 48, justifyContent: 'center', paddingHorizontal: space.md }}>
          <Text style={{ fontFamily: font.body, fontSize: fontSize.bodyMd, color: color.ink, textDecorationLine: 'underline' }}>Not now</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}
