export interface ChatCard {
  image: number;
  creator: string;
  desc: string;
}

export interface ChatMessage {
  id: string;
  isUser: boolean;
  text: string;
  card?: ChatCard;
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
