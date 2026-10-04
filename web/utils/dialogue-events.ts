/**
 * Lightweight app-wide signal that the conversation list changed (new chat saved, reply finished,
 * chat deleted). The sidebar listens and refetches, so new chats appear without a page reload.
 */
export const DIALOGUES_CHANGED_EVENT = 'kavir:dialogues-changed';

export function notifyDialoguesChanged(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(DIALOGUES_CHANGED_EVENT));
}
