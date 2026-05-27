export interface User {
  id: string;
  name: string;
  avatar: string;
  status: 'online' | 'offline';
  lastSeen?: string;
  isBot?: boolean;
  isDiscord?: boolean;
  isAdmin?: boolean;
}

export interface FileAttachment {
  id: string;
  name: string;
  size: number;
  mimeType: string;
  url: string;
  base64Data?: string; // Optional reference
}

export interface CallInfo {
  status: 'connecting' | 'ringing' | 'connected' | 'ended' | 'declined';
  duration?: number;
  isVideo: boolean;
  callerId: string;
  receiverId: string;
}

export interface Message {
  id: string;
  chatId: string; // "global" or private target userId
  senderId: string;
  senderName: string;
  senderAvatar: string;
  text: string;
  timestamp: string;
  type: 'text' | 'file' | 'audio' | 'call' | 'dice';
  fileInfo?: FileAttachment;
  callInfo?: CallInfo;
  reactions: { [emoji: string]: string[] }; // emoji -> array of userIds
  replyTo?: {
    id: string;
    senderName: string;
    text: string;
  };
  translatedText?: string;
  diceValue?: number;
}

export interface ChatSession {
  id: string; // "global" or peer userId
  name: string;
  avatar: string;
  isGroup: boolean;
  isBot?: boolean;
  unreadCount: number;
  lastMessage?: Message;
}

// WebSocket Event Structure
export type WsPayload =
  | { type: 'init'; user: User }
  | { type: 'presence'; users: User[] }
  | { type: 'message'; message: Message }
  | { type: 'reaction'; messageId: string; emoji: string; userId: string; isAdd: boolean }
  | { type: 'typing'; chatId: string; senderId: string; isTyping: boolean }
  | { type: 'call:request'; callerId: string; receiverId: string; isVideo: boolean; callId: string }
  | { type: 'call:ringing'; callerId: string; receiverId: string; callId: string }
  | { type: 'call:accept'; callerId: string; receiverId: string; callId: string }
  | { type: 'call:decline'; callerId: string; receiverId: string; callId: string }
  | { type: 'call:hangup'; callerId: string; receiverId: string; callId: string; duration: number };

export interface WsEvent {
  event: WsPayload['type'];
  payload: any;
}
