import { collectionQueryIsCurrent } from '@/lib/collection-state';

describe('collection screen query fence', () => {
  it('hides membership loaded for another item until the new query settles', () => {
    expect(collectionQueryIsCurrent('user-a', 'item-a', 'user-a', 'item-b')).toBe(false);
    expect(collectionQueryIsCurrent('user-a', 'item-b', 'user-a', 'item-b')).toBe(true);
  });

  it('hides membership loaded for another account even when the item matches', () => {
    expect(collectionQueryIsCurrent('user-a', 'item-a', 'user-b', 'item-a')).toBe(false);
  });
});
