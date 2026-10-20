jest.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    body: unknown;
    code: string;
    constructor(status: number, body: unknown, message: string, code = 'http_error') {
      super(message);
      this.status = status;
      this.body = body;
      this.code = code;
    }
  },
  api: {
    get: require('@jest/globals').jest.fn(),
    post: require('@jest/globals').jest.fn(),
  },
  createImportPreview: require('@jest/globals').jest.fn(),
  getImportPreviewCandidates: require('@jest/globals').jest.fn(),
  commitImportPreview: require('@jest/globals').jest.fn(),
}));

jest.mock('@/hooks/useAuth', () => ({
  useAuth: require('@jest/globals').jest.fn(),
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: require('@jest/globals').jest.fn(),
    setItem: require('@jest/globals').jest.fn(),
    removeItem: require('@jest/globals').jest.fn(),
  },
}));

jest.mock('expo-router', () => {
  const React = require('react');
  return {
    useRouter: () => ({ replace: require('@jest/globals').jest.fn() }),
    useFocusEffect: (callback: () => void | (() => void)) => React.useEffect(callback, [callback]),
  };
});

jest.mock('expo-document-picker', () => ({
  getDocumentAsync: require('@jest/globals').jest.fn(),
}));

jest.mock('@/lib/verification', () => ({
  getPendingEmail: require('@jest/globals').jest.fn().mockResolvedValue(null),
  resendConfirmation: require('@jest/globals').jest.fn().mockResolvedValue(null),
}));

jest.mock('@/components/PillButton', () => {
  const React = require('react');
  const { Pressable, Text } = require('react-native');
  return {
    PillButton: ({ label, onPress, disabled, accessibilityHint }: any) => React.createElement(
      Pressable,
      { onPress, disabled, accessibilityRole: 'button', accessibilityLabel: label, accessibilityHint },
      React.createElement(Text, null, label),
    ),
  };
});

jest.mock('@/components/ProgressDots', () => ({
  ProgressDots: () => null,
}));

import React from 'react';
import { Alert } from 'react-native';
import TestRenderer, { act } from 'react-test-renderer';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Import3Screen from '../../../app/(import)/import3';
import { api, commitImportPreview, createImportPreview, getImportPreviewCandidates } from '@/lib/api';
import { useAuth } from '@/hooks/useAuth';
import * as DocumentPicker from 'expo-document-picker';

const storage = AsyncStorage as unknown as {
  getItem: jest.Mock;
  setItem: jest.Mock;
  removeItem: jest.Mock;
};
const requests = api as unknown as { get: jest.Mock; post: jest.Mock };
const previewRequests = {
  create: createImportPreview as jest.Mock,
  candidates: getImportPreviewCandidates as jest.Mock,
  commit: commitImportPreview as jest.Mock,
};
const auth = useAuth as unknown as jest.Mock;

const importStatus = (importId: string, state = 'complete') => ({
  import_id: importId,
  state,
  total_parsed: 3,
  text_indexed_done: 3,
  full_queued: 0,
  full_done: 3,
  failed: 0,
  failure_reason: null,
  started_at: '2026-10-20T10:00:00Z',
  finished_at: '2026-10-20T10:01:00Z',
});

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function buttonByLabel(root: TestRenderer.ReactTestInstance, label: string) {
  return root.find((node) => node.props.accessibilityRole === 'button'
    && node.props.accessibilityLabel === label);
}

describe('Instagram import screen recovery contract', () => {
  beforeEach(() => {
    storage.getItem.mockReset().mockResolvedValue(null);
    storage.setItem.mockReset().mockResolvedValue(undefined);
    storage.removeItem.mockReset().mockResolvedValue(undefined);
    requests.get.mockReset();
    requests.post.mockReset();
    previewRequests.create.mockReset();
    previewRequests.candidates.mockReset();
    previewRequests.commit.mockReset();
    jest.spyOn(Alert, 'alert').mockImplementation((_title, _message, buttons) => {
      buttons?.[1]?.onPress?.();
    });
    auth.mockReset().mockReturnValue({ session: { user: { id: 'user-a' } } });
    requests.get.mockResolvedValue({ imports: [], next_cursor: null });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('shows listed imports without attaching to one, then reopens only the selected import ID', async () => {
    requests.get
      .mockResolvedValueOnce({ imports: [importStatus('server/import?2', 'indexing')], next_cursor: null })
      .mockResolvedValueOnce(importStatus('server/import?2'));

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(React.createElement(Import3Screen));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requests.get).toHaveBeenCalledTimes(1);
    expect(requests.get).toHaveBeenCalledWith('/v1/imports?limit=20', { timeoutMs: 15_000, expectedUserId: 'user-a' });
    expect(renderer.root.findAll((node) => node.props.accessibilityLabel === 'Refresh import status')).toHaveLength(0);

    await act(async () => {
      buttonByLabel(renderer.root, 'Open indexing import with 3 ready and 0 queued').props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(requests.get).toHaveBeenLastCalledWith('/v1/imports/server%2Fimport%3F2', { timeoutMs: 15_000, expectedUserId: 'user-a' });
    await act(async () => renderer.unmount());
  });

  it('loads retry identity by account, previews candidates and requires an explicit full-processing confirmation', async () => {
    const pending = {
      uri: 'file:///cache/instagram-export.zip',
      name: 'instagram-export.zip',
      type: 'application/zip',
      idempotencyKey: 'mobile-import-stable-retry-key',
    };
    storage.getItem.mockImplementation(async (key: string) => (
      key === 'spill/import-pending/v1/user-a' ? JSON.stringify(pending) : null
    ));
    previewRequests.create.mockResolvedValue({ preview_id: 'preview-1', expires_at: '2099-10-20T10:00:00Z', total_candidates: 1 });
    previewRequests.candidates.mockResolvedValue({ candidates: [{
      candidate_id: 'candidate-1', ordinal: 1, kind: 'media', source_bucket: 'instagram',
      url: 'https://instagram.com/reel/1', title: 'A saved reel', artist: null,
      saved_at: null, collection_names: [],
    }], next_cursor: null });
    previewRequests.commit.mockResolvedValue({ import_id: 'accepted-import', state: 'processing', total_parsed: 1 });
    requests.get.mockImplementation((path: string) => Promise.resolve(
      path.includes('/imports/') ? importStatus('accepted-import') : { imports: [], next_cursor: null },
    ));

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(React.createElement(Import3Screen));
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();

    expect(storage.getItem).toHaveBeenCalledWith('spill/import-pending/v1/user-a');
    expect(renderer.root.findAll((node) => String(node.type) === 'Text' && node.children.includes('instagram-export.zip'))).toHaveLength(1);

    await act(async () => {
      buttonByLabel(renderer.root, 'Create preview').props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();

    expect(previewRequests.create).toHaveBeenCalledWith(pending.uri, pending.name, pending.idempotencyKey, {
      timeoutMs: 60_000, expectedUserId: 'user-a',
    });
    expect(previewRequests.candidates).toHaveBeenCalledWith('preview-1', {
      limit: 100, cursor: null, expectedUserId: 'user-a',
    });
    await act(async () => {
      renderer.root.find((node) => node.props.accessibilityRole === 'checkbox').props.onPress();
    });
    await act(async () => {
      buttonByLabel(renderer.root, 'Review 1 selected').props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Import 1 item?',
      expect.stringContaining('fully processed'),
      expect.any(Array),
    );
    expect(previewRequests.commit).toHaveBeenCalledWith('preview-1', ['candidate-1'], {
      timeoutMs: 30_000, expectedUserId: 'user-a',
    });
    expect(storage.removeItem).toHaveBeenCalledWith('spill/import-pending/v1/user-a');
    await act(async () => renderer.unmount());
  });

  it('does not restore another account’s pending archive', async () => {
    storage.getItem.mockImplementation(async (key: string) => (
      key === 'spill/import-pending/v1/user-a'
        ? JSON.stringify({
          uri: 'file:///cache/user-a.zip',
          name: 'user-a.zip',
          type: 'application/zip',
          idempotencyKey: 'user-a-key',
        })
        : null
    ));
    requests.get.mockResolvedValue({ imports: [], next_cursor: null });

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(React.createElement(Import3Screen));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(storage.getItem).toHaveBeenCalledWith('spill/import-pending/v1/user-a');

    auth.mockReturnValue({ session: { user: { id: 'user-b' } } });
    await act(async () => {
      renderer.update(React.createElement(Import3Screen));
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();

    expect(storage.getItem).toHaveBeenCalledWith('spill/import-pending/v1/user-b');
    expect(renderer.root.findAll((node) => String(node.type) === 'Text' && node.children.includes('user-a.zip'))).toHaveLength(0);
    await act(async () => renderer.unmount());
  });

  it('accepts the standalone saved_posts.html export and rejects unrelated HTML files', async () => {
    const picker = DocumentPicker.getDocumentAsync as jest.Mock;
    picker.mockReset().mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///Download/saved_posts.html', name: 'saved_posts.html', mimeType: 'text/html' }],
    });
    previewRequests.create.mockResolvedValue({
      preview_id: 'html-preview', expires_at: '2099-10-20T10:00:00Z', total_candidates: 0,
    });
    previewRequests.candidates.mockResolvedValue({ candidates: [{
      candidate_id: 'html-candidate', ordinal: 1, kind: 'media', source_bucket: 'instagram',
      url: 'https://instagram.com/p/123', title: 'Saved post', artist: null,
      saved_at: null, collection_names: [],
    }], next_cursor: null });

    let renderer!: TestRenderer.ReactTestRenderer;
    await act(async () => {
      renderer = TestRenderer.create(React.createElement(Import3Screen));
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();

    await act(async () => {
      buttonByLabel(renderer.root, 'Choose an Instagram export ZIP or saved_posts.html file').props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();

    expect(picker).toHaveBeenCalledWith(expect.objectContaining({
      type: ['application/zip', 'application/x-zip-compressed', 'text/html'],
      multiple: false,
    }));
    expect(renderer.root.findAll((node) => String(node.type) === 'Text' && node.children.includes('saved_posts.html'))).toHaveLength(1);
    await act(async () => {
      buttonByLabel(renderer.root, 'Create preview').props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();
    expect(previewRequests.create).toHaveBeenCalledWith(
      'file:///Download/saved_posts.html',
      'saved_posts.html',
      expect.any(String),
      expect.objectContaining({ expectedUserId: 'user-a' }),
    );
    await act(async () => renderer.unmount());

    picker.mockReset().mockResolvedValue({
      canceled: false,
      assets: [{ uri: 'file:///Download/notes.html', name: 'notes.html', mimeType: 'text/html' }],
    });
    await act(async () => {
      renderer = TestRenderer.create(React.createElement(Import3Screen));
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();
    await act(async () => {
      buttonByLabel(renderer.root, 'Choose an Instagram export ZIP or saved_posts.html file').props.onPress();
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();
    expect(renderer.root.findAll((node) => String(node.type) === 'Text' && node.children.includes('Choose the ZIP export or its saved_posts.html file.'))).toHaveLength(1);
    expect(previewRequests.create).not.toHaveBeenCalledWith('file:///Download/notes.html', 'notes.html', expect.any(String), expect.anything());
    await act(async () => renderer.unmount());
  });

});
