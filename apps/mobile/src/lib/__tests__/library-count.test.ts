jest.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ user: null }) }));
jest.mock('@/lib/api', () => ({ api: { get: jest.fn() } }));

import { getVisibleLibraryCount } from '../../../app/(app)/library';

describe('getVisibleLibraryCount', () => {
  const scope = JSON.stringify(['user-a', null, null]);

  it('uses the server total instead of the number of pages loaded', () => {
    expect(getVisibleLibraryCount({ scope, total: 117 }, scope, false, false)).toBe('117 saved');
  });

  it('shows a confirmed zero for an empty result', () => {
    expect(getVisibleLibraryCount({ scope, total: 0 }, scope, false, false)).toBe('0 saved');
  });

  it('hides a count from another owner or filter scope', () => {
    expect(getVisibleLibraryCount({ scope, total: 117 }, JSON.stringify(['user-b', null, null]), false, false)).toBe('');
    expect(getVisibleLibraryCount({ scope, total: 117 }, JSON.stringify(['user-a', 'instagram', null]), false, false)).toBe('');
  });

  it('hides a count while the current query is loading or failed', () => {
    expect(getVisibleLibraryCount({ scope, total: 117 }, scope, true, false)).toBe('');
    expect(getVisibleLibraryCount({ scope, total: 117 }, scope, false, true)).toBe('');
  });
});
