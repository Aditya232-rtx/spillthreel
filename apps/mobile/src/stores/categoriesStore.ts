import { useCallback, useEffect, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  SEED_CATEGORIES,
  USER_CATEGORY_PALETTE,
  type CategoryDef,
} from '@/data/categories';

const STORAGE_KEY = 'spillthereel.userCategories.v1';

/**
 * Categories store — combines the AI-inferred seed set with any
 * user-created categories, persisting the user set to AsyncStorage.
 *
 * Backend contract (see trd.md §6.3, F8 flow): once wired up, seed
 * categories arrive from `GET /v1/categories?source=auto` and mirror
 * the server's Cognee-driven auto-tagging. User categories POST to
 * `/v1/categories` and drive an incremental re-tag over the user's
 * library (server-side background job). This hook shims that surface
 * for v1 with local-only persistence — swap the AsyncStorage reads/
 * writes for API calls once the backend is live.
 */

interface UserCategoryInput {
  name: string;
  emoji: string;
}

interface StoredUserCategory {
  id: string;
  name: string;
  emoji: string;
  createdAt: string;
}

function paletteFor(index: number): (typeof USER_CATEGORY_PALETTE)[number] {
  return USER_CATEGORY_PALETTE[index % USER_CATEGORY_PALETTE.length];
}

function hydrate(stored: StoredUserCategory[]): CategoryDef[] {
  return stored.map((row, i) => {
    const palette = paletteFor(i);
    return {
      id: row.id,
      name: row.name.toUpperCase(),
      count: 0,
      bg: palette.bg,
      text: palette.text,
      subText: palette.subText,
      icon: null,
      emoji: row.emoji,
      tags: [],
      source: 'user',
    };
  });
}

export function useCategories() {
  const [userStored, setUserStored] = useState<StoredUserCategory[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    AsyncStorage.getItem(STORAGE_KEY)
      .then((raw) => {
        if (cancelled) return;
        if (raw) {
          try {
            const parsed = JSON.parse(raw) as StoredUserCategory[];
            setUserStored(parsed);
          } catch {
            // corrupt payload — start fresh rather than crash
            setUserStored([]);
          }
        }
        setReady(true);
      })
      .catch(() => setReady(true));
    return () => {
      cancelled = true;
    };
  }, []);

  const addCategory = useCallback(
    async (input: UserCategoryInput) => {
      const trimmed = input.name.trim();
      if (!trimmed) return;
      const next: StoredUserCategory = {
        id: `user_${Date.now()}`,
        name: trimmed,
        emoji: input.emoji,
        createdAt: new Date().toISOString(),
      };
      const nextList = [...userStored, next];
      setUserStored(nextList);
      await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(nextList));
    },
    [userStored],
  );

  const categories: CategoryDef[] = [...SEED_CATEGORIES, ...hydrate(userStored)];

  return { categories, addCategory, ready };
}
