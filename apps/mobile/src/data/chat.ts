import type { SearchCard, SearchCitation } from '@/lib/api';

export interface ChatCard {
  image: number;
  creator: string;
  desc: string;
}

/** Failure mode of an assistant turn — drives error copy + Retry visibility. */
export type ChatErrorKind = 'auth' | 'retryable' | 'generic';

export interface ChatMessage {
  id: string;
  isUser: boolean;
  text: string;
  /**
   * Legacy v1 single card (require() image). Never written anymore — v1
   * history is migrated by dropping this field and keeping text.
   */
  card?: ChatCard;
  /** Backend /v1/search hits, newest format. */
  cards?: SearchCard[];
  /** Backend /v1/search citations; [n] markers render inline in text. */
  citations?: SearchCitation[];
  /** Set on failed assistant turns to render the Retry button. */
  error?: ChatErrorKind;
  /** Original query, so Retry can re-fire without retyping. */
  retryQuery?: string;
}

export const INITIAL_MESSAGES: ChatMessage[] = [
  { id: '1', isUser: true, text: 'what were those cool ui libraries?' },
  {
    id: '2',
    isUser: false,
    text: 'Found it — post by @can.adityaa. List of them are:\nfancycomponents.dev - text effects\nui.aceternity.com - templates\nmagicui.design - dark themed\nlightswind.com - huge collection\n21st.dev - for everything you need',
    card: {
      image: require('../../assets/images/chat-ui-libraries.png'),
      creator: '@can.adityaa',
      desc: 'UI Library Edition — steal these assets.',
    },
  },
];

export const SUGGESTION_CHIPS = ['🍳 Show my recipes', '💪 Workout reels', '✈️ Travel saves'];
