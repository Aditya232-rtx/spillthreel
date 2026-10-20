export function collectionQueryIsCurrent(
  loadedOwnerId: string | null,
  loadedItemId: string | undefined,
  accountId: string,
  itemId: string | undefined,
): boolean {
  return loadedOwnerId === accountId && loadedItemId === itemId;
}
