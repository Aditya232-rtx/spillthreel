import {
  decideImportSummaryRoute,
  importSummaryTargetForOwner,
  notificationResponseKey,
  parseImportSummaryNotification,
} from '@/lib/notification-routing';

const validPayload = {
  type: 'import_summary',
  owner_id: 'b91b5ae2-2cd0-4a3a-80ae-f1e8d863cc1b',
  import_id: 'opaque/import id?42',
};

describe('import summary notification routing', () => {
  it('accepts a strict payload and routes only for the matching owner', () => {
    expect(parseImportSummaryNotification(validPayload)).toEqual(validPayload);
    expect(importSummaryTargetForOwner(validPayload, validPayload.owner_id)).toBe(validPayload.import_id);
    expect(importSummaryTargetForOwner(validPayload, 'another-owner')).toBeNull();
  });

  it('retains a valid cold-start tap while signed out, then routes or discards after login', () => {
    expect(decideImportSummaryRoute(validPayload, null, true)).toEqual({ status: 'defer' });
    expect(decideImportSummaryRoute(validPayload, null, false)).toEqual({ status: 'defer' });
    expect(decideImportSummaryRoute(validPayload, validPayload.owner_id, false)).toEqual({
      status: 'route',
      importId: validPayload.import_id,
    });
    expect(decideImportSummaryRoute(validPayload, 'another-owner', false)).toEqual({ status: 'discard' });
  });

  it.each([
    null,
    [],
    { ...validPayload, type: 'item_ready' },
    { ...validPayload, owner_id: 'not-a-uuid' },
    { ...validPayload, import_id: '' },
    { ...validPayload, import_id: 'bad\nidentifier' },
    { ...validPayload, extra: true },
  ])('rejects malformed or unsupported payload %p', (payload) => {
    expect(parseImportSummaryNotification(payload)).toBeNull();
    expect(importSummaryTargetForOwner(payload, validPayload.owner_id)).toBeNull();
  });

  it('uses a stable response key and isolates malformed data', () => {
    expect(notificationResponseKey('request-1', 'default', validPayload))
      .toBe(`request-1:default:${validPayload.owner_id}:${validPayload.import_id}`);
    expect(notificationResponseKey('request-1', 'default', { bad: true }))
      .toBe('request-1:default:invalid');
    expect(notificationResponseKey(null, 'default', validPayload)).toBeNull();
  });
});
