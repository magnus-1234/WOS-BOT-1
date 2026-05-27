import { useEffect, useRef, useState, useMemo } from 'react';
import { Sparkles, MessageCircle, Laptop, Smile, User as UserIcon, Settings, Globe, Palette, Check, Sliders, X, Volume2, VolumeX, Shield, Image } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { User, Message, ChatSession, FileAttachment, WsEvent } from './types';
import Sidebar from './components/Sidebar';
import ChatArea from './components/ChatArea';
import CallOverlay from './components/CallOverlay';
import RightProfileSidebar from './components/RightProfileSidebar';
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from 'react-resizable-panels';

// Anonymous / hacker themed profile avatars
const PRESET_AVATARS = [
  'https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?w=120&h=120&fit=crop', // matrix code
  'https://images.unsplash.com/photo-1537245942-88d447ecadfe?w=120&h=120&fit=crop', // guy with hoodie
  'https://images.unsplash.com/photo-1496366579203-b0959d9c24ce?w=120&h=120&fit=crop', // hacker/hoodie 2
  'https://images.unsplash.com/photo-1517433670267-08bbd4be890f?w=120&h=120&fit=crop', // anonymous dark mask-like
  'https://images.unsplash.com/photo-1614064009669-9f4464c5fb43?w=120&h=120&fit=crop', // anonymous abstract
  'https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=120&h=120&fit=crop', // cyber security
  'https://images.unsplash.com/photo-1515694346937-94d85e41e6f0?w=120&h=120&fit=crop', // technology matrix
  'https://images.unsplash.com/photo-1504384308090-c894fdcc538d?w=120&h=120&fit=crop', // data flowing
  'https://images.unsplash.com/photo-1603539958744-f8b1e4c73f4d?w=120&h=120&fit=crop', // hacker hands on keyboard
  'https://images.unsplash.com/photo-1534423861386-85a16f5d13fd?w=120&h=120&fit=crop', // skull/vr
];

const MYSTERIOUS_PREFIXES = ['agent', 'anonymous', 'cipher', 'ghost', 'shadow', 'phantom', 'hacker', 'neon', 'glitch', 'specter', 'matrix'];

export default function App() {
  // 1. User state
  const [currentUser, setCurrentUser] = useState<(User & { isAdmin?: boolean }) | null>(null);
  const [avatarInput, setAvatarInput] = useState(PRESET_AVATARS[0]);
  const [nameInput, setNameInput] = useState('');
  const [adminCodeInput, setAdminCodeInput] = useState('');
  const [isOnboarding, setIsOnboarding] = useState(true);

  // User Custom Settings and Chat Background states
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [chatBg, setChatBg] = useState<{ type: string; value: string }>(() => {
    const saved = localStorage.getItem('wos_chat_background');
    if (saved) {
      try {
        return JSON.parse(saved);
      } catch (err) {}
    }
    return { type: 'gradient', value: 'linear-gradient(to bottom right, #020617, #0f172a)' };
  });

  const [settingsTab, setSettingsTab] = useState<'profile' | 'background' | 'tactical'>('profile');
  const [editName, setEditName] = useState('');
  const [editAvatar, setEditAvatar] = useState('');
  const [customBgInput, setCustomBgInput] = useState('');
  const [soundMuted, setSoundMuted] = useState(false);
  const [compactMode, setCompactMode] = useState(false);
  const [customStatus, setCustomStatus] = useState('Active Chief');

  useEffect(() => {
    if (showSettingsModal && currentUser) {
      setEditName(currentUser.name);
      setEditAvatar(currentUser.avatar);
      setCustomStatus((currentUser as any).customStatus || 'Active Chief');
    }
  }, [showSettingsModal, currentUser]);

  // Onboarding Tabs and Discord Integration Popover
  const [onboardingTab, setOnboardingTab] = useState<'discord' | 'guest'>('discord');
  const [showDiscordPopup, setShowDiscordPopup] = useState(false);
  const [isDiscordAuthorizing, setIsDiscordAuthorizing] = useState(false);
  const [selectedDiscordMockUser] = useState(() => {
    const prefix = MYSTERIOUS_PREFIXES[Math.floor(Math.random() * MYSTERIOUS_PREFIXES.length)];
    const number = Math.floor(Math.random() * 10000);
    return {
      name: `${prefix}${number}`,
      avatar: PRESET_AVATARS[Math.floor(Math.random() * PRESET_AVATARS.length)]
    };
  });

  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  const [activeMobileView, setActiveMobileView] = useState<'sidebar' | 'chat' | 'profile'>('sidebar');

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 2. Chat states
  const [currentSessionId, setCurrentSessionId] = useState<string>('global');
  const [messages, setMessages] = useState<Message[]>([]);
  const [activeUsers, setActiveUsers] = useState<User[]>([]);
  const [unreadCounts, setUnreadCounts] = useState<{ [id: string]: number }>({});
  
  // Right sidebar and profile view states
  const [showRightSidebar, setShowRightSidebar] = useState<boolean>(true);
  const [selectedProfileUserId, setSelectedProfileUserId] = useState<string | null>(null);

  // 3. Media call states
  const [currentCall, setCurrentCall] = useState<{
    status: 'connecting' | 'ringing' | 'connected' | 'ended' | 'declined';
    isVideo: boolean;
    callerId: string;
    receiverId: string;
    role: 'caller' | 'receiver';
    callId: string;
    peer: User;
  } | null>(null);

  // 4. Partner typing states
  const [typingStates, setTypingStates] = useState<{ [chatId: string]: { [senderId: string]: boolean } }>({});

  // 5. Blizzard & Administration theme modes
  const [isBlizzardActive, setIsBlizzardActive] = useState(false);
  const [currentAnnouncement, setCurrentAnnouncement] = useState<string | null>(null);

  // 6. Muted Users & Friends Local States
  const [mutedUserIds, setMutedUserIds] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem('tg_chat_muted_users');
      return stored ? JSON.parse(stored) : [];
    } catch { return []; }
  });

  const [friendUserIds, setFriendUserIds] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem('tg_chat_friends');
      return stored ? JSON.parse(stored) : [];
    } catch { return []; }
  });

  useEffect(() => {
    localStorage.setItem('tg_chat_muted_users', JSON.stringify(mutedUserIds));
  }, [mutedUserIds]);

  useEffect(() => {
    localStorage.setItem('tg_chat_friends', JSON.stringify(friendUserIds));
  }, [friendUserIds]);

  const wsRef = useRef<WebSocket | null>(null);

  // Load user profile from LocalStorage on mount
  useEffect(() => {
    const savedUser = localStorage.getItem('tg_chat_user');
    if (savedUser) {
      try {
        const parsed = JSON.parse(savedUser);
        setCurrentUser(parsed);
        setIsOnboarding(false);
      } catch (err) {
        localStorage.removeItem('tg_chat_user');
      }
    } else {
      // prefill profile randomly
      const prefix = MYSTERIOUS_PREFIXES[Math.floor(Math.random() * MYSTERIOUS_PREFIXES.length)];
      const number = Math.floor(Math.random() * 10000);
      setNameInput(`${prefix}${number}`);
      setAvatarInput(PRESET_AVATARS[Math.floor(Math.random() * PRESET_AVATARS.length)]);
    }
  }, []);

  // Initialize Socket link upon user login
  useEffect(() => {
    if (!currentUser) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socketUrl = `${protocol}//${window.location.host}`;
    
    console.log(`Linking WebSocket server node: ${socketUrl}`);
    const ws = new WebSocket(socketUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('Sockets node handshaked. Initializing parameters...');
      // Register client on the server
      ws.send(JSON.stringify({
        type: 'init',
        user: currentUser,
      }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        
        switch (data.type) {
          case 'init_ok': {
            setMessages(data.history || []);
            setActiveUsers(data.users || []);
            if (data.blizzardMode !== undefined) {
              setIsBlizzardActive(data.blizzardMode);
            }
            break;
          }

          case 'deleted_user': {
            const deletedId = data.userId;
            setFriendUserIds((prev) => prev.filter(id => id !== deletedId));
            setMutedUserIds((prev) => prev.filter(id => id !== deletedId));
            // Optional: you could also clear current session if you are chatting with them,
            // but we'll let it naturally sit there as a dead channel to preserve messages.
            break;
          }

          case 'presence': {
            setActiveUsers(data.users || []);
            break;
          }

          case 'message': {
            const msg: Message = data.message;
            setMessages((prev) => {
              // Ensure we don't insert duplicate message IDs
              if (prev.some((m) => m.id === msg.id)) return prev;
              return [...prev, msg];
            });

            // Increment unread count if message is not in currently active chat and not from ourselves
            if (msg.senderId !== currentUser.id) {
              const chatRefId = msg.chatId === 'global' ? 'global' : msg.senderId;
              if (chatRefId !== currentSessionId) {
                setUnreadCounts((prev) => ({
                  ...prev,
                  [chatRefId]: (prev[chatRefId] || 0) + 1,
                }));
              }
            }
            break;
          }

          case 'delete_message': {
            const { messageId } = data;
            setMessages((prev) => prev.filter((m) => m.id !== messageId));
            break;
          }

          case 'admin:blizzard': {
            const { isFrozen } = data;
            setIsBlizzardActive(isFrozen);
            break;
          }

          case 'history_cleared': {
            setMessages(data.history || []);
            break;
          }

          case 'admin:announcement': {
            const { alertText } = data;
            setCurrentAnnouncement(alertText);
            // Hide the administrative announcement automatically after 8 seconds
            setTimeout(() => {
              setCurrentAnnouncement((curr) => curr === alertText ? null : curr);
            }, 8000);
            break;
          }

          case 'reaction': {
            const { messageId, emoji, userId, isAdd } = data;
            setMessages((prev) =>
              prev.map((m) => {
                if (m.id !== messageId) return m;
                const nextReactions = { ...m.reactions };
                if (!nextReactions[emoji]) nextReactions[emoji] = [];
                
                if (isAdd) {
                  if (!nextReactions[emoji].includes(userId)) {
                    nextReactions[emoji].push(userId);
                  }
                } else {
                  nextReactions[emoji] = nextReactions[emoji].filter((u) => u !== userId);
                }
                return { ...m, reactions: nextReactions };
              })
            );
            break;
          }

          case 'typing': {
            const { chatId, senderId, isTyping } = data;
            setTypingStates((prev) => ({
              ...prev,
              [chatId]: {
                ...(prev[chatId] || {}),
                [senderId]: isTyping,
              },
            }));
            break;
          }

          // Real-time voice & video calls event routing
          case 'call:request': {
            // Only accept inbound call if not already in an active session
            if (currentCall) {
              // Decline automatically due to busy state
              ws.send(JSON.stringify({
                type: 'call:decline',
                callerId: data.callerId,
                receiverId: currentUser.id,
                callId: data.callId,
              }));
              return;
            }

            const callerUser = activeUsers.find((u) => u.id === data.callerId) || {
              id: data.callerId,
              name: 'Unknown Peer',
              avatar: '',
              status: 'online',
            } as User;

            setCurrentCall({
              status: 'ringing',
              isVideo: data.isVideo,
              callerId: data.callerId,
              receiverId: currentUser.id,
              role: 'receiver',
              callId: data.callId,
              peer: callerUser,
            });
            break;
          }

          case 'call:ringing': {
            if (currentCall && currentCall.callId === data.callId) {
              setCurrentCall((prev) => prev ? { ...prev, status: 'ringing' } : null);
            }
            break;
          }

          case 'call:accept': {
            if (currentCall && currentCall.callId === data.callId) {
              setCurrentCall((prev) => prev ? { ...prev, status: 'connected' } : null);
            }
            break;
          }

          case 'call:decline':
          case 'call:hangup': {
            if (currentCall && currentCall.callId === data.callId) {
              // Close camera triggers
              setCurrentCall(null);
              
              // Post visual signal bubble log in-line in Private chat history
              const isDecline = data.type === 'call:decline';
              const textContent = isDecline
                ? `📞 Missed / Declined Call (${currentCall.isVideo ? 'Video' : 'Voice'})`
                : `📞 Call Ended • Duration: ${data.duration || 0}s`;
              
              // Trigger a local system text message insertion for UX consistency
              const systemMsg: Message = {
                id: `call-event-${Date.now()}`,
                chatId: currentSessionId,
                senderId: 'system',
                senderName: 'System',
                senderAvatar: '',
                text: textContent,
                timestamp: new Date().toISOString(),
                type: 'call',
                reactions: {},
              };
              setMessages((prev) => [...prev, systemMsg]);
            }
            break;
          }
        }
      } catch (err) {
        console.error('Socket notification parsing failed:', err);
      }
    };

    ws.onclose = () => {
      console.log('Socket link offline. Attempting automated reconnection in 3s...');
      setTimeout(() => {
        if (currentUser) {
          setCurrentUser({ ...currentUser }); // triggers effect reload
        }
      }, 3000);
    };

    return () => {
      ws.close();
    };
  }, [currentUser]);

  // Read message logs trigger: clear unread indicators of the active workspace session
  useEffect(() => {
    if (unreadCounts[currentSessionId]) {
      setUnreadCounts((prev) => ({
        ...prev,
        [currentSessionId]: 0,
      }));
    }
  }, [currentSessionId, messages]);

  const handleOnboardingEnter = () => {
    if (nameInput.trim() === '') return;
    const isUserAdmin = adminCodeInput === 'survival100' || nameInput.trim() === 'survival100';
    const userPayload: User & { isAdmin?: boolean } = {
      id: `user-${Date.now()}`,
      name: nameInput.trim(),
      avatar: avatarInput,
      status: 'online',
      isAdmin: isUserAdmin,
      isDiscord: false,
    };
    setCurrentUser(userPayload);
    localStorage.setItem('tg_chat_user', JSON.stringify(userPayload));
    setIsOnboarding(false);
  };

  const handleDiscordConfirm = (username: string, avatarUrl: string) => {
    const userPayload: User & { isAdmin?: boolean } = {
      id: `user-discord-${Date.now()}`,
      name: username,
      avatar: avatarUrl,
      status: 'online',
      isAdmin: false,
      isDiscord: true,
    };
    setCurrentUser(userPayload);
    localStorage.setItem('tg_chat_user', JSON.stringify(userPayload));
    setIsOnboarding(false);
    setShowDiscordPopup(false);
  };

  const handleResetProfile = () => {
    localStorage.removeItem('tg_chat_user');
    setCurrentUser(null);
    setIsOnboarding(true);
    setAdminCodeInput('');
  };

  const handleSaveSettings = () => {
    if (!currentUser) return;
    const updatedUser: User & { isAdmin?: boolean; customStatus?: string } = {
      ...currentUser,
      name: editName.trim() || currentUser.name,
      avatar: editAvatar.trim() || currentUser.avatar,
      customStatus: customStatus.trim(),
    };
    setCurrentUser(updatedUser);
    localStorage.setItem('tg_chat_user', JSON.stringify(updatedUser));

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'init',
        user: updatedUser,
      }));
    }

    localStorage.setItem('wos_chat_background', JSON.stringify(chatBg));
    setShowSettingsModal(false);
  };

  // Claim Admin command inside standard workspace settings
  const handleClaimAdmin = (code: string) => {
    if (code === 'survival100' && currentUser) {
      const updated = { ...currentUser, isAdmin: true };
      setCurrentUser(updated);
      localStorage.setItem('tg_chat_user', JSON.stringify(updated));
      return true;
    }
    return false;
  };

  // Chat message transmitters supporting Replies and Dice rollers
  const handleSendMessage = (
    text: string,
    type: 'text' | 'file' | 'audio' | 'dice',
    fileInfo?: FileAttachment,
    replyTo?: { id: string; senderName: string; text: string },
    diceValue?: number
  ) => {
    if (!currentUser || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    // Standard non-admin survivors are muted in blizzard
    if (isBlizzardActive && !currentUser.isAdmin) {
      console.warn("Muted in global communication channel by Frost Command Blizzard!");
      return;
    }

    const newMsg: Message = {
      id: `msg-${Date.now()}-${Math.floor(Math.random() * 1000)}`,
      chatId: currentSessionId,
      senderId: currentUser.id,
      senderName: currentUser.name,
      senderAvatar: currentUser.avatar,
      text,
      timestamp: new Date().toISOString(),
      type,
      fileInfo,
      reactions: {},
      replyTo,
      diceValue,
    };

    // Send payload over Socket
    wsRef.current.send(JSON.stringify({
      type: 'message',
      message: newMsg,
    }));
  };

  const handleDeleteMessage = (messageId: string) => {
    // 1. Optimistic UI update for instantaneous visual feedback
    setMessages((prev) => prev.filter((m) => m.id !== messageId));

    // 2. Transmit deletion message
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'delete_message',
        messageId,
        chatId: currentSessionId,
      }));
    }
  };

  const handleTranslateMessage = async (messageId: string, targetLang: string) => {
    const targetMsg = messages.find((m) => m.id === messageId);
    if (!targetMsg) return;

    // Toggle behavior: if already translated, clear it to show original text
    if (targetMsg.translatedText) {
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, translatedText: undefined } : m))
      );
      return;
    }

    try {
      const res = await fetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: targetMsg.text, targetLang }),
      });
      const parsed = await res.json();
      if (parsed.translatedText) {
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? { ...m, translatedText: parsed.translatedText } : m))
        );
      }
    } catch (err) {
      console.error('Translation failed:', err);
    }
  };

  const handleToggleBlizzard = (isFrozen: boolean) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({
      type: 'admin:blizzard',
      isFrozen,
    }));
  };

  const handleClearLogs = () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({
      type: 'admin:clear',
    }));
  };

  const handleBroadcastAnnouncement = (alertText: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({
      type: 'admin:announcement',
      alertText,
    }));
  };

  // Reactions toggle actions
  const handleSendReaction = (messageId: string, emoji: string) => {
    if (!currentUser || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    const targetMsg = messages.find((m) => m.id === messageId);
    if (!targetMsg) return;

    const hasOurReaction = targetMsg.reactions[emoji]?.includes(currentUser.id);
    
    wsRef.current.send(JSON.stringify({
      type: 'reaction',
      chatId: currentSessionId,
      messageId,
      emoji,
      userId: currentUser.id,
      isAdd: !hasOurReaction,
    }));
  };

  // Typing broadcasts
  const handleSendTyping = (isTyping: boolean) => {
    if (!currentUser || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    
    wsRef.current.send(JSON.stringify({
      type: 'typing',
      chatId: currentSessionId,
      senderId: currentUser.id,
      isTyping,
    }));
  };

  // Outbound Calls
  const handleInitiateCall = (isVideo: boolean) => {
    if (!currentUser || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    
    const callId = `call-${Date.now()}`;
    const peerUser = activeUsers.find((u) => u.id === currentSessionId);
    if (!peerUser) return;

    setCurrentCall({
      status: 'connecting',
      isVideo,
      callerId: currentUser.id,
      receiverId: currentSessionId,
      role: 'caller',
      callId,
      peer: peerUser,
    });

    wsRef.current.send(JSON.stringify({
      type: 'call:request',
      callerId: currentUser.id,
      receiverId: currentSessionId,
      isVideo,
      callId,
    }));
  };

  // Caller and Receiver actions
  const handleAcceptCall = () => {
    if (!currentUser || !currentCall || !wsRef.current) return;
    wsRef.current.send(JSON.stringify({
      type: 'call:accept',
      callerId: currentCall.callerId,
      receiverId: currentCall.receiverId,
      callId: currentCall.callId,
    }));
  };

  const handleDeclineCall = () => {
    if (!currentUser || !currentCall || !wsRef.current) return;
    wsRef.current.send(JSON.stringify({
      type: 'call:decline',
      callerId: currentCall.callerId,
      receiverId: currentCall.receiverId,
      callId: currentCall.callId,
    }));
    setCurrentCall(null);
  };

  const handleHangupCall = (durationSeconds: number) => {
    if (!currentUser || !currentCall || !wsRef.current) return;
    wsRef.current.send(JSON.stringify({
      type: 'call:hangup',
      callerId: currentCall.callerId,
      receiverId: currentCall.receiverId,
      callId: currentCall.callId,
      duration: durationSeconds,
    }));
    setCurrentCall(null);
  };

  // Aggregate current session list (pre-seeded list of chat folder nodes)
  const availableSessions: ChatSession[] = useMemo(() => {
    const list: ChatSession[] = [
      {
        id: 'global',
        name: 'Global Group Chat',
        avatar: '',
        isGroup: true,
        unreadCount: unreadCounts['global'] || 0,
        lastMessage: messages.filter((m) => m.chatId === 'global').pop(),
      },
      {
        id: 'gemini_bot',
        name: 'WOS BOT',
        avatar: 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=120&h=120&fit=crop',
        isGroup: false,
        isBot: true,
        unreadCount: unreadCounts['gemini_bot'] || 0,
        lastMessage: messages.filter((m) => m.chatId === 'gemini_bot' || (m.senderId === 'gemini_bot' && m.chatId === currentUser?.id)).pop(),
      },
    ];

    // Map other active WebSocket clients as Private Chats
    const addedIds = new Set<string>();
    
    activeUsers.forEach((u) => {
      if (currentUser && u.id !== currentUser.id && u.id !== 'gemini_bot') {
        addedIds.add(u.id);
        list.push({
          id: u.id,
          name: u.name,
          avatar: u.avatar,
          isGroup: false,
          unreadCount: unreadCounts[u.id] || 0,
          lastMessage: messages.filter((m) => m.chatId === u.id || (m.chatId === currentUser.id && m.senderId === u.id)).pop(),
        });
      }
    });

    // Add offline users that we have chatted with
    if (currentUser) {
      messages.forEach(m => {
        if (m.chatId !== 'global' && m.chatId !== 'gemini_bot') {
          const otherUserId = m.senderId === currentUser.id ? m.chatId : m.senderId;
          if (otherUserId !== currentUser.id && otherUserId !== 'gemini_bot' && !addedIds.has(otherUserId)) {
            addedIds.add(otherUserId);
            // Default name since they are offline and we don't have their true name
            let name = m.senderId === otherUserId ? m.senderName : `User ${otherUserId.substring(0, 4)}`;
            let avatar = m.senderId === otherUserId ? (m.senderAvatar || '') : '';
            
            // Look for a message sent by them to grab their name/avatar reliably if possible
            const theirMsg = messages.find(msg => msg.senderId === otherUserId);
            if (theirMsg) {
              name = theirMsg.senderName;
              avatar = theirMsg.senderAvatar || '';
            }

            list.push({
              id: otherUserId,
              name: name,
              avatar: avatar,
              isGroup: false,
              unreadCount: unreadCounts[otherUserId] || 0,
              lastMessage: messages.filter((msg) => msg.chatId === otherUserId || (msg.chatId === currentUser.id && msg.senderId === otherUserId)).pop(),
            });
          }
        }
      });
    }

    return list;
  }, [activeUsers, messages, unreadCounts, currentUser]);

  // Selected session record object
  const selectedSession = useMemo(() => {
    return availableSessions.find((s) => s.id === currentSessionId) || null;
  }, [availableSessions, currentSessionId]);

  // Filter messages for current active screen
  const handleLogout = () => {
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ type: 'logout' }));
      wsRef.current.close();
    }
    localStorage.removeItem('tg_chat_user');
    setCurrentUser(null);
    setIsOnboarding(true);
    setShowSettingsModal(false);
  };

  const currentChatMessages = useMemo(() => {
    if (currentSessionId === 'global') {
      return messages.filter((m) => m.chatId === 'global' && !mutedUserIds.includes(m.senderId));
    }
    if (currentSessionId === 'gemini_bot') {
      return messages.filter(
        (m) => m.chatId === 'gemini_bot' || (m.senderId === 'gemini_bot' && m.chatId === currentUser?.id)
      );
    }
    return messages.filter(
      (m) =>
        (m.chatId === currentSessionId && m.senderId === currentUser?.id) ||
        (m.chatId === currentUser?.id && m.senderId === currentSessionId)
    );
  }, [messages, currentSessionId, currentUser, mutedUserIds]);

  const handleToggleMute = (userId: string) => {
    setMutedUserIds((prev) => 
      prev.includes(userId) ? prev.filter((id) => id !== userId) : [...prev, userId]
    );
  };

  const handleToggleFriend = (userId: string) => {
    setFriendUserIds((prev) => 
      prev.includes(userId) ? prev.filter((id) => id !== userId) : [...prev, userId]
    );
  };

  const startPrivateChat = (userId: string) => {
    setCurrentSessionId(userId);
    setShowRightSidebar(false);
    setActiveMobileView('chat');
  };

  // Determine typing states for current chat
  const isOtherUserCurrentlyTyping = useMemo(() => {
    const chatTyping = typingStates[currentSessionId];
    if (!chatTyping) return false;
    return Object.entries(chatTyping).some(([senderId, isTyping]) => isTyping && senderId !== currentUser?.id);
  }, [typingStates, currentSessionId, currentUser]);

  const currentlyTypingUserNames = useMemo(() => {
    const chatTyping = typingStates[currentSessionId];
    if (!chatTyping) return 'Someone';
    const typingId = Object.keys(chatTyping).find((id) => chatTyping[id] && id !== currentUser?.id);
    if (!typingId) return 'Someone';
    if (typingId === 'gemini_bot') return 'WOS BOT';
    return activeUsers.find((u) => u.id === typingId)?.name || 'Someone';
  }, [typingStates, currentSessionId, activeUsers, currentUser]);

  return (
    <div className={`flex h-screen w-screen overflow-hidden antialiased font-sans transition-colors duration-700 ${isBlizzardActive ? 'bg-indigo-950/20 ring-4 ring-cyan-500/25' : 'bg-gray-100 dark:bg-gray-950'}`}>
      
      {/* Blizzard Winter Overlay Effect with animated icons */}
      {isBlizzardActive && (
        <div className="absolute inset-0 z-40 pointer-events-none select-none border-4 border-cyan-400/40 bg-cyan-900/5 backdrop-brightness-95 overflow-hidden animate-pulse">
          <div className="absolute inset-0 bg-gradient-to-b from-cyan-500/10 to-transparent" />
          <div className="absolute top-2 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1 bg-cyan-500 text-white text-[10px] font-bold tracking-widest uppercase rounded-full shadow-lg shadow-cyan-500/20">
            <span className="h-1.5 w-1.5 rounded-full bg-white animate-ping" />
            <span>Blizzard active • Global chat frozen</span>
          </div>
        </div>
      )}

      {/* Floating Global Announcement Alert Banner */}
      <AnimatePresence>
        {currentAnnouncement && (
          <motion.div
            initial={{ opacity: 0, y: -50, x: '-50%' }}
            animate={{ opacity: 1, y: 16, x: '-50%' }}
            exit={{ opacity: 0, y: -50, x: '-50%' }}
            className="absolute top-4 left-1/2 z-50 w-full max-w-md px-4 pointer-events-auto"
          >
            <div className="flex items-center gap-3 bg-amber-500 text-white rounded-2xl p-4 shadow-xl border border-amber-400">
              <div className="text-xl">📢</div>
              <div className="flex-1 min-w-0">
                <span className="block text-[9px] uppercase font-mono font-bold tracking-wider opacity-85">Global Order</span>
                <p className="text-xs font-semibold leading-tight mt-0.5">{currentAnnouncement}</p>
              </div>
              <button onClick={() => setCurrentAnnouncement(null)} className="hover:bg-white/15 p-1 rounded-full text-xs transition">✕</button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Onboarding Dialog Modal overlay for unregistered profile nodes */}
      <AnimatePresence>
        {isOnboarding && (
          <motion.div
            id="onboarding-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/45 p-4 backdrop-blur-md"
          >
            <motion.div
              initial={{ scale: 0.95, y: 15 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.95, y: 15 }}
              className="w-full max-w-[420px] rounded-3xl bg-white p-8 shadow-2xl border border-gray-100 dark:bg-gray-900 dark:border-gray-800"
            >
              <div className="text-center">
                <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-cyan-500/10 text-cyan-500 mb-4 animate-bounce">
                  <Sparkles className="h-6 w-6" />
                </div>
                <h2 className="text-xl font-extrabold tracking-tight text-gray-900 dark:text-gray-100 font-display">Whiteout Survival Console</h2>
                <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                  Pick your profile authentication node to connect dynamically onto the frozen wilderness global communications network.
                </p>
              </div>

              {/* Login Mode Tabs */}
              <div className="flex gap-1.5 p-1.5 bg-gray-50 dark:bg-gray-950 rounded-2xl mt-5 border border-gray-200/50 dark:border-gray-800">
                <button
                  onClick={() => setOnboardingTab('discord')}
                  className={`flex-1 py-2.5 rounded-xl text-xs font-bold transition flex items-center justify-center gap-1.5 cursor-pointer ${
                    onboardingTab === 'discord'
                      ? 'bg-[#5865F2] text-white shadow-md shadow-indigo-500/10'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200'
                  }`}
                >
                  <svg className="h-4 w-4 fill-current" viewBox="0 0 127.14 96.36">
                    <path d="M107.7,8.07A105.15,105.15,0,0,0,77.26,0a77.19,77.19,0,0,0-3.3,6.83A96.67,96.67,0,0,0,53.22,6.83,77.19,77.19,0,0,0,49.88,0,105.15,105.15,0,0,0,19.44,8.07C3.66,31.58-1.86,54.65,1,77.53A105.73,105.73,0,0,0,32,96.36a77.7,77.7,0,0,0,6.63-10.85,68.43,68.43,0,0,1-10.43-5c.87-.64,1.72-1.31,2.53-2a75.37,75.37,0,0,0,72.9,0c.81,.69,1.66,1.36,2.53,2a68.43,68.43,0,0,1-10.43,5,77.7,77.7,0,0,0,6.63,10.85,105.73,105.73,0,0,0,31-18.83C129.87,48.42,123.37,25.68,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53S36.18,40.36,42.45,40.36,54,46,53.92,53,48.81,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.24,60,73.24,53S78.41,40.36,84.69,40.36,96.22,46,96.14,53,91,65.69,84.69,65.69Z" />
                  </svg>
                  <span>Connect Discord</span>
                </button>
                <button
                  onClick={() => setOnboardingTab('guest')}
                  className={`flex-1 py-2.5 rounded-xl text-xs font-bold transition cursor-pointer ${
                    onboardingTab === 'guest'
                      ? 'bg-cyan-600 text-white shadow-md shadow-cyan-600/10'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200'
                  }`}
                >
                  Incognito Guest
                </button>
              </div>

              {/* Tab Form entries */}
              <div className="mt-6 space-y-4">
                {onboardingTab === 'discord' ? (
                  <div className="space-y-4 pt-1 text-center">
                    <p className="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
                      Sync your real-time Discord profile context nodes instantly into the combat terminal log frames. You'll carry a specialized Discord integration badge.
                    </p>
                    
                    <button
                      onClick={() => setShowDiscordPopup(true)}
                      className="w-full py-3.5 px-4 bg-[#5865F2] hover:bg-[#4752C4] text-white rounded-xl font-bold transition flex items-center justify-center gap-2.5 shadow-lg shadow-indigo-600/30 active:scale-97 cursor-pointer"
                    >
                      <svg className="h-4.5 w-4.5 fill-current" viewBox="0 0 127.14 96.36">
                        <path d="M107.7,8.07A105.15,105.15,0,0,0,77.26,0a77.19,77.19,0,0,0-3.3,6.83A96.67,96.67,0,0,0,53.22,6.83,77.19,77.19,0,0,0,49.88,0,105.15,105.15,0,0,0,19.44,8.07C3.66,31.58-1.86,54.65,1,77.53A105.73,105.73,0,0,0,32,96.36a77.7,77.7,0,0,0,6.63-10.85,68.43,68.43,0,0,1-10.43-5c.87-.64,1.72-1.31,2.53-2a75.37,75.37,0,0,0,72.9,0c.81,.69,1.66,1.36,2.53,2a68.43,68.43,0,0,1-10.43,5,77.7,77.7,0,0,0,6.63,10.85,105.73,105.73,0,0,0,31-18.83C129.87,48.42,123.37,25.68,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53S36.18,40.36,42.45,40.36,54,46,53.92,53,48.81,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.24,60,73.24,53S78.41,40.36,84.69,40.36,96.22,46,96.14,53,91,65.69,84.69,65.69Z" />
                      </svg>
                      <span>Connect via Discord Oauth2</span>
                    </button>
                    
                    <div className="text-[10px] uppercase font-mono tracking-wider font-extrabold text-blue-500/80 bg-blue-500/5 py-1 rounded">
                      ⚡ Secure Single Sign-On Ready
                    </div>
                  </div>
                ) : (
                  <>
                    {/* Avatar Row */}
                    <div>
                      <label className="text-xs font-bold uppercase tracking-wider text-gray-400 block mb-2 select-none">
                        Select Survivor Frame
                      </label>
                      <div className="flex flex-wrap justify-between gap-1 gap-y-2">
                        {PRESET_AVATARS.map((url) => (
                          <button
                            key={url}
                            type="button"
                            onClick={() => setAvatarInput(url)}
                            className={`h-10 w-10 rounded-full overflow-hidden border-2 transition active:scale-90 flex-shrink-0 ${
                              avatarInput === url ? 'border-cyan-500 scale-110 ring-4 ring-cyan-500/20' : 'border-gray-200 dark:border-gray-800 hover:border-cyan-400'
                            }`}
                          >
                            <img src={url} alt="preset avatar" className="h-full w-full object-cover" />
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Nickname Input */}
                    <div>
                      <label className="text-xs font-bold uppercase tracking-wider text-gray-400 block mb-1.5 select-none text-left">
                        Survivor Name
                      </label>
                      <input
                        id="onboarding-nickname-input"
                        type="text"
                        placeholder="e.g. Chief Hunter"
                        value={nameInput}
                        onChange={(e) => setNameInput(e.target.value)}
                        maxLength={24}
                        className="w-full px-4 py-2.5 border border-gray-200 dark:border-gray-800 rounded-xl bg-gray-50 dark:bg-gray-950 font-semibold focus:outline-none focus:ring-2 focus:ring-cyan-500/40 text-xs text-gray-900 dark:text-gray-100"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleOnboardingEnter();
                        }}
                      />
                    </div>

                    {/* Optional Admin Passcode */}
                    <div>
                      <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400 block mb-1.5 select-none text-left">
                        Admin Passcode (Optional)
                      </label>
                      <input
                        id="onboarding-admincode-input"
                        type="password"
                        placeholder="Enter survival100 for Command Deck"
                        value={adminCodeInput}
                        onChange={(e) => setAdminCodeInput(e.target.value)}
                        className="w-full px-4 py-2 border border-gray-200 dark:border-gray-800 rounded-xl bg-slate-50 dark:bg-slate-950 text-xs focus:outline-none focus:ring-2 focus:ring-cyan-500/30 text-gray-900 dark:text-gray-100 placeholder-slate-400"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleOnboardingEnter();
                        }}
                      />
                    </div>

                    {/* Connection Trigger */}
                    <button
                      id="btn-confirm-onboarding"
                      onClick={handleOnboardingEnter}
                      disabled={nameInput.trim() === ''}
                      className="w-full py-3 rounded-xl bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white font-bold transition flex items-center justify-center gap-2 text-xs shadow-lg shadow-cyan-600/20 active:scale-97 cursor-pointer"
                    >
                      <Sparkles className="h-4 w-4" /> Initialize Survivor Node
                    </button>
                  </>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* High-Fidelity Discord Authentication Simulated Popup */}
      <AnimatePresence>
        {showDiscordPopup && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          >
            <motion.div
              initial={{ scale: 0.9, y: 30 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.9, y: 30 }}
              className="w-full max-w-[440px] bg-[#36393f] rounded-2xl overflow-hidden border border-[#202225] text-white shadow-2xl flex flex-col font-sans"
            >
              {/* Simulated browser title bar */}
              <div className="bg-[#202225] py-2.5 px-4 flex items-center justify-between text-xs text-gray-400 select-none">
                <span className="flex items-center gap-1.5 font-semibold">
                  <span className="w-2.5 h-2.5 rounded-full bg-[#ef4444]" />
                  <span className="w-2.5 h-2.5 rounded-full bg-[#eab308]" />
                  <span className="w-2.5 h-2.5 rounded-full bg-[#22c55e]" />
                  <span className="ml-1 font-mono tracking-tight text-[10px]">discord.com/oauth2/authorize</span>
                </span>
                <button onClick={() => setShowDiscordPopup(false)} className="text-gray-400 hover:text-white transition font-bold text-sm cursor-pointer">✕</button>
              </div>

              {/* Discord Content body */}
              <div className="p-6 flex flex-col items-center">
                
                {/* Logo and App name */}
                <div className="flex items-center gap-4 bg-[#2f3136] px-5 py-3 rounded-xl border border-white/5 mb-6">
                  {/* Discord logo */}
                  <div className="h-10 w-10 rounded-full bg-[#5865F2] flex items-center justify-center">
                    <svg className="h-5.5 w-5.5 fill-white" viewBox="0 0 127.14 96.36">
                      <path d="M107.7,8.07A105.15,105.15,0,0,0,77.26,0a77.19,77.19,0,0,0-3.3,6.83A96.67,96.67,0,0,0,53.22,6.83,77.19,77.19,0,0,0,49.88,0,105.15,105.15,0,0,0,19.44,8.07C3.66,31.58-1.86,54.65,1,77.53A105.73,105.73,0,0,0,32,96.36a77.7,77.7,0,0,0,6.63-10.85,68.43,68.43,0,0,1-10.43-5c.87-.64,1.72-1.31,2.53-2a75.37,75.37,0,0,0,72.9,0c.81,.69,1.66,1.36,2.53,2a68.43,68.43,0,0,1-10.43,5,77.7,77.7,0,0,0,6.63,10.85,105.73,105.73,0,0,0,31-18.83C129.87,48.42,123.37,25.68,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53S36.18,40.36,42.45,40.36,54,46,53.92,53,48.81,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.24,60,73.24,53S78.41,40.36,84.69,40.36,96.22,46,96.14,53,91,65.69,84.69,65.69Z" />
                    </svg>
                  </div>
                  <span className="text-xl font-bold font-mono">⇋</span>
                  {/* Cool game icon placeholder */}
                  <div className="h-10 w-10 rounded-full bg-cyan-500/20 border border-cyan-400 flex items-center justify-center text-cyan-400 text-lg shadow-sm">
                    🏔️
                  </div>
                </div>

                <div className="text-center mb-5">
                  <h3 className="text-md font-bold">Authorize Whiteout Survival?</h3>
                  <p className="text-xs text-gray-400 mt-1">The application wants to link your survivor identity tags.</p>
                </div>

                {/* Scopes listing */}
                <div className="w-full bg-[#2f3136] rounded-xl p-4 text-xs space-y-3 border border-[#202225] select-none text-left">
                  <span className="block text-[9px] font-bold text-gray-400 uppercase tracking-widest border-b border-white/5 pb-1.5">WHITE OUT SURVIVAL IS REQUESTING ACCESS TO:</span>
                  <div className="flex items-start gap-2.5">
                    <span className="text-green-400 font-bold">✔</span>
                    <div>
                      <span className="font-semibold block text-gray-100 text-xs">Access your username and avatar</span>
                      <span className="text-gray-400 text-[10.5px]">Will match you to Discord ID <code className="bg-[#202225] text-cyan-400 px-1 py-0.5 rounded">@{selectedDiscordMockUser.name.toLowerCase()}</code></span>
                    </div>
                  </div>
                  <div className="flex items-start gap-2.5">
                    <span className="text-green-400 font-bold">✔</span>
                    <div>
                      <span className="font-semibold block text-gray-100 text-xs">Synchronize presence on the survivor log</span>
                      <span className="text-gray-400 text-[10.5px]">Allows active transmissions inside channels</span>
                    </div>
                  </div>
                </div>

                {isDiscordAuthorizing ? (
                  <div className="mt-6 flex flex-col items-center gap-3 py-2">
                    <div className="h-7 w-7 border-3 border-[#5865F2] border-t-transparent rounded-full animate-spin" />
                    <span className="text-xs text-[#5865F2] font-semibold animate-pulse">Exchanging OAuth tokens with Discord APIs...</span>
                  </div>
                ) : (
                  <div className="w-full mt-6 flex gap-3 text-xs font-bold">
                    <button
                      onClick={() => setShowDiscordPopup(false)}
                      className="flex-1 py-2.5 hover:bg-white/5 bg-transparent border border-gray-500 rounded-lg transition active:scale-95 text-gray-300 cursor-pointer"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={() => {
                        setIsDiscordAuthorizing(true);
                        setTimeout(() => {
                          setIsDiscordAuthorizing(false);
                          handleDiscordConfirm(selectedDiscordMockUser.name, selectedDiscordMockUser.avatar);
                        }, 1200);
                      }}
                      className="flex-1 py-2.5 bg-[#5865F2] hover:bg-[#4752C4] text-white rounded-lg transition active:scale-95 flex items-center justify-center gap-1.5 cursor-pointer"
                    >
                      Authorize
                    </button>
                  </div>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Main Full-Scale Frame Layout */}
      {!isOnboarding && currentUser && (
        <div className="flex h-full w-full bg-white dark:bg-gray-950">
          {isMobile ? (
            <div className="h-full w-full flex flex-col">
              {activeMobileView === 'sidebar' && (
                <Sidebar
                  currentUser={currentUser}
                  activeSessions={availableSessions}
                  activeUsers={activeUsers}
                  currentSessionId={currentSessionId}
                  onSelectSession={(id) => {
                    setCurrentSessionId(id);
                    setActiveMobileView('chat');
                  }}
                  onResetProfile={() => setShowSettingsModal(true)}
                  isBlizzardActive={isBlizzardActive}
                  onToggleBlizzard={handleToggleBlizzard}
                  onClearLogs={handleClearLogs}
                  onBroadcastAnnouncement={handleBroadcastAnnouncement}
                  onClaimAdmin={handleClaimAdmin}
                />
              )}
              {activeMobileView === 'chat' && (
                <ChatArea
                  currentUser={currentUser}
                  session={selectedSession}
                  messages={currentChatMessages}
                  activeUsers={activeUsers}
                  isOtherUserTyping={isOtherUserCurrentlyTyping}
                  typingUser={currentlyTypingUserNames}
                  onSendMessage={handleSendMessage}
                  onSendReaction={handleSendReaction}
                  onSendTyping={handleSendTyping}
                  onInitiateCall={handleInitiateCall}
                  isBlizzardActive={isBlizzardActive}
                  onDeleteMessage={handleDeleteMessage}
                  onTranslateMessage={handleTranslateMessage}
                  onShowUserProfile={(userId) => {
                    setSelectedProfileUserId(userId);
                    setActiveMobileView('profile');
                  }}
                  isRightSidebarOpen={false}
                  onToggleRightSidebar={() => setActiveMobileView('profile')}
                  chatBg={chatBg}
                  onBack={() => setActiveMobileView('sidebar')}
                />
              )}
              {activeMobileView === 'profile' && (
                <RightProfileSidebar
                  activeUsers={activeUsers}
                  currentUser={currentUser}
                  selectedUserId={selectedProfileUserId}
                  onSelectUser={setSelectedProfileUserId}
                  onCloseProfile={() => setActiveMobileView('chat')}
                  onStartPrivateChat={startPrivateChat}
                  mutedUserIds={mutedUserIds}
                  onToggleMute={handleToggleMute}
                  friendUserIds={friendUserIds}
                  onToggleFriend={handleToggleFriend}
                />
              )}
            </div>
          ) : (
            <PanelGroup orientation="horizontal" className="h-full w-full">
              {/* Left panel Sidebar Folder */}
              <Panel defaultSize={22} minSize={15} className="h-full">
                <Sidebar
                  currentUser={currentUser}
                  activeSessions={availableSessions}
                  activeUsers={activeUsers}
                  currentSessionId={currentSessionId}
                  onSelectSession={(id) => setCurrentSessionId(id)}
                  onResetProfile={() => setShowSettingsModal(true)}
                  isBlizzardActive={isBlizzardActive}
                  onToggleBlizzard={handleToggleBlizzard}
                  onClearLogs={handleClearLogs}
                  onBroadcastAnnouncement={handleBroadcastAnnouncement}
                  onClaimAdmin={handleClaimAdmin}
                />
              </Panel>

              <PanelResizeHandle className="w-4 mx-[-6px] hover:bg-cyan-500/20 active:bg-cyan-500/30 transition-colors cursor-col-resize z-50 relative flex items-center justify-center group shrink-0 outline-none">
                <div className="h-full w-px bg-gray-200 dark:bg-gray-800 group-hover:bg-cyan-500 group-active:bg-cyan-500 group-hover:w-1 transition-all duration-200 shadow-sm rounded-full" />
              </PanelResizeHandle>

              {/* Middle Workspace messaging details */}
              <Panel className="h-full min-w-0 flex flex-col items-stretch relative">
                <ChatArea
                  currentUser={currentUser}
                  session={selectedSession}
                  messages={currentChatMessages}
                  activeUsers={activeUsers}
                  isOtherUserTyping={isOtherUserCurrentlyTyping}
                  typingUser={currentlyTypingUserNames}
                  onSendMessage={handleSendMessage}
                  onSendReaction={handleSendReaction}
                  onSendTyping={handleSendTyping}
                  onInitiateCall={handleInitiateCall}
                  isBlizzardActive={isBlizzardActive}
                  onDeleteMessage={handleDeleteMessage}
                  onTranslateMessage={handleTranslateMessage}
                  onShowUserProfile={(userId) => {
                    setSelectedProfileUserId(userId);
                    setShowRightSidebar(true);
                  }}
                  isRightSidebarOpen={showRightSidebar}
                  onToggleRightSidebar={() => setShowRightSidebar(!showRightSidebar)}
                  chatBg={chatBg}
                />
              </Panel>

              {/* Right side Panel */}
              {showRightSidebar && (
                <>
                  <PanelResizeHandle className="w-4 mx-[-6px] hover:bg-cyan-500/20 active:bg-cyan-500/30 transition-colors cursor-col-resize z-50 relative flex items-center justify-center group shrink-0 outline-none">
                    <div className="h-full w-px bg-gray-200 dark:bg-gray-800 group-hover:bg-cyan-500 group-active:bg-cyan-500 group-hover:w-1 transition-all duration-200 shadow-sm rounded-full" />
                  </PanelResizeHandle>
                  <Panel defaultSize={20} minSize={15} className="h-full relative z-20">
                    <RightProfileSidebar
                      activeUsers={activeUsers}
                      currentUser={currentUser}
                      selectedUserId={selectedProfileUserId}
                      onSelectUser={setSelectedProfileUserId}
                      onCloseProfile={() => setShowRightSidebar(false)}
                      onStartPrivateChat={startPrivateChat}
                      mutedUserIds={mutedUserIds}
                      onToggleMute={handleToggleMute}
                      friendUserIds={friendUserIds}
                      onToggleFriend={handleToggleFriend}
                    />
                  </Panel>
                </>
              )}
            </PanelGroup>
          )}

          {/* Dynamic Popup full-screen Calls panels */}
          <CallOverlay
            currentCall={currentCall}
            onAccept={handleAcceptCall}
            onDecline={handleDeclineCall}
            onHangup={handleHangupCall}
          />

          {/* Tactical User Settings & Background Customizer Modal */}
          <AnimatePresence>
            {showSettingsModal && currentUser && (
              <motion.div
                id="wos-settings-modal"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/50 p-4 backdrop-blur-md"
              >
                <motion.div
                  initial={{ scale: 0.95, y: 20 }}
                  animate={{ scale: 1, y: 0 }}
                  exit={{ scale: 0.95, y: 20 }}
                  transition={{ type: 'spring', stiffness: 450, damping: 30 }}
                  className="w-full max-w-2xl rounded-3xl bg-white dark:bg-gray-900 border border-gray-150 dark:border-gray-800 shadow-2xl overflow-hidden flex flex-col md:flex-row h-[560px]"
                >
                  {/* Left Tab Options Panel */}
                  <div className="w-full md:w-56 bg-gray-50/50 dark:bg-gray-900/40 p-5 border-b md:border-b-0 md:border-r border-gray-150 dark:border-gray-800 flex flex-row md:flex-col justify-start gap-1 flex-shrink-0 select-none">
                    <div className="hidden md:flex items-center gap-2 px-2.5 pb-4 mb-2 border-b border-gray-150 dark:border-gray-800">
                      <div className="h-7 w-7 rounded bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 flex items-center justify-center">
                        <Sliders className="h-4 w-4" />
                      </div>
                      <span className="text-xs font-bold uppercase tracking-wider text-gray-700 dark:text-gray-300">Survivor Deck</span>
                    </div>

                    <button
                      type="button"
                      onClick={() => setSettingsTab('profile')}
                      className={`flex-1 md:flex-none flex items-center justify-center md:justify-start gap-2.5 px-3.5 py-2.5 rounded-xl text-xs font-bold transition-all ${
                        settingsTab === 'profile'
                          ? 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/10'
                          : 'text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200'
                      }`}
                    >
                      <UserIcon className="h-4 w-4" />
                      <span className="hidden md:inline">Survivor Profile</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => setSettingsTab('background')}
                      className={`flex-1 md:flex-none flex items-center justify-center md:justify-start gap-2.5 px-3.5 py-2.5 rounded-xl text-xs font-bold transition-all ${
                        settingsTab === 'background'
                          ? 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/10'
                          : 'text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200'
                      }`}
                    >
                      <Palette className="h-4 w-4" />
                      <span className="hidden md:inline">Chat Background</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => setSettingsTab('tactical')}
                      className={`flex-1 md:flex-none flex items-center justify-center md:justify-start gap-2.5 px-3.5 py-2.5 rounded-xl text-xs font-bold transition-all ${
                        settingsTab === 'tactical'
                          ? 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/10'
                          : 'text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200'
                      }`}
                    >
                      <Settings className="h-4 w-4" />
                      <span className="hidden md:inline">Tactical Options</span>
                    </button>
                  </div>

                  {/* Right Contents Area */}
                  <div className="flex-1 flex flex-col h-full overflow-hidden bg-white dark:bg-gray-900">
                    {/* Header Bar */}
                    <div className="px-6 py-4 border-b border-gray-150 dark:border-gray-800 flex items-center justify-between select-none">
                      <h3 className="text-sm font-extrabold text-gray-900 dark:text-gray-100 uppercase tracking-wider font-display">
                        {settingsTab === 'profile' && 'Survivor Profile Setup'}
                        {settingsTab === 'background' && 'Custom Chat Background'}
                        {settingsTab === 'tactical' && 'Tactical Comfort Settings'}
                      </h3>
                      <button
                        type="button"
                        onClick={() => setShowSettingsModal(false)}
                        className="p-1 px-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 transition"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>

                    {/* Content Body Scrollable panel */}
                    <div className="flex-1 overflow-y-auto p-6 space-y-5">
                      {settingsTab === 'profile' && (
                        <div className="space-y-4">
                          <div>
                            <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-550 block mb-1.5 select-none text-left">
                              Survivor ID Reference Code
                            </label>
                            <input
                              type="text"
                              disabled
                              value={currentUser.id}
                              className="w-full px-4 py-2 border border-gray-150 dark:border-gray-800/80 rounded-xl bg-gray-50/50 dark:bg-gray-950/40 text-xs font-semibold text-gray-500 font-mono tracking-tight cursor-not-allowed select-all"
                            />
                          </div>

                          <div>
                            <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-550 block mb-1.5 select-none text-left">
                              Survivor Username Nickname
                            </label>
                            <input
                              type="text"
                              value={editName}
                              onChange={(e) => setEditName(e.target.value)}
                              maxLength={24}
                              placeholder="e.g. Chief Hunter"
                              className="w-full px-4 py-2.5 border border-gray-200 dark:border-gray-800 rounded-xl bg-gray-50/50 dark:bg-gray-950/40 focus:outline-none focus:ring-2 focus:ring-cyan-500/30 text-xs font-semibold text-gray-900 dark:text-gray-100"
                            />
                          </div>

                          <div>
                            <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-550 block mb-1.5 select-none text-left">
                              Tactical Status / Patrol Duty
                            </label>
                            <input
                              type="text"
                              value={customStatus}
                              onChange={(e) => setCustomStatus(e.target.value)}
                              maxLength={24}
                              placeholder="e.g. Patrolling Glacier Walls"
                              className="w-full px-4 py-2.5 border border-gray-200 dark:border-gray-800 rounded-xl bg-gray-50/50 dark:bg-gray-950/40 focus:outline-none focus:ring-2 focus:ring-cyan-500/30 text-xs font-semibold text-gray-900 dark:text-gray-100"
                            />
                          </div>

                          {/* Avatar Selector */}
                          <div>
                            <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-550 block mb-2 select-none text-left">
                              Change Avatar Frame
                            </label>
                            <div className="flex gap-2 mb-3">
                              {PRESET_AVATARS.map((url) => (
                                <button
                                  key={url}
                                  type="button"
                                  onClick={() => setEditAvatar(url)}
                                  className={`h-11 w-11 rounded-full overflow-hidden border-2 transition active:scale-95 flex-shrink-0 ${
                                    editAvatar === url ? 'border-cyan-500 scale-105 ring-4 ring-cyan-500/20' : 'border-gray-200 dark:border-gray-800 hover:border-cyan-400'
                                  }`}
                                >
                                  <img src={url} alt="preset avatar" className="h-full w-full object-cover" />
                                </button>
                              ))}
                            </div>

                            <div className="space-y-1">
                              <span className="text-[10px] text-gray-400 block text-left">Or supply a custom external graphic web URL:</span>
                              <input
                                type="text"
                                placeholder="https://unsplash.com/... or raw image link"
                                value={editAvatar}
                                onChange={(e) => setEditAvatar(e.target.value)}
                                className="w-full px-4 py-2 border border-gray-200 dark:border-gray-800 rounded-xl bg-gray-50/50 dark:bg-gray-950/40 focus:outline-none focus:ring-2 focus:ring-cyan-500/30 text-xs text-gray-900 dark:text-gray-150 font-mono"
                              />
                            </div>
                          </div>
                        </div>
                      )}

                      {settingsTab === 'background' && (
                        <div className="space-y-4">
                          <div>
                            <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-550 block mb-2 select-none text-left">
                              Select Frozen Preset Arctic Walls
                            </label>
                            <div className="grid grid-cols-2 gap-3">
                              {/* Aurora Gradient */}
                              <button
                                type="button"
                                onClick={() => setChatBg({ type: 'gradient', value: 'linear-gradient(to bottom right, #020617, #0f172a)' })}
                                className={`p-4 rounded-2xl flex flex-col items-center justify-center gap-1 bg-gradient-to-br from-[#020617] to-[#0f172a] border transition text-white ${
                                  chatBg.type === 'gradient' && chatBg.value.includes('#020617') ? 'border-cyan-500 ring-2 ring-cyan-500/20' : 'border-slate-800 hover:opacity-90'
                                }`}
                              >
                                <span className="text-xs font-bold font-display">Frosted Glacier</span>
                                <span className="text-[9px] opacity-70">Slated Deep Blue</span>
                              </button>

                              {/* Aurora Borealis Green Gradient */}
                              <button
                                type="button"
                                onClick={() => setChatBg({ type: 'gradient', value: 'linear-gradient(to bottom right, #011c27, #022c22, #000000)' })}
                                className={`p-4 rounded-2xl flex flex-col items-center justify-center gap-1 bg-gradient-to-br from-[#011c27] via-[#022c22] to-black border transition text-emerald-450 ${
                                  chatBg.type === 'gradient' && chatBg.value.includes('#022c22') ? 'border-cyan-500 ring-2 ring-cyan-500/20' : 'border-slate-800 hover:opacity-90'
                                }`}
                              >
                                <span className="text-xs font-bold font-display">Arctic Aurora</span>
                                <span className="text-[9px] opacity-70">Emerald Green Light</span>
                              </button>

                              {/* Crimson Ember Hot Zone */}
                              <button
                                type="button"
                                onClick={() => setChatBg({ type: 'gradient', value: 'linear-gradient(to bottom right, #0f0505, #220303, #020000)' })}
                                className={`p-4 rounded-2xl flex flex-col items-center justify-center gap-1 bg-gradient-to-br from-[#0f0505] via-[#220303] to-black border transition text-red-500 ${
                                  chatBg.type === 'gradient' && chatBg.value.includes('#220303') ? 'border-cyan-500 ring-2 ring-cyan-500/20' : 'border-slate-800 hover:opacity-90'
                                }`}
                              >
                                <span className="text-xs font-bold font-display">Ember Generator</span>
                                <span className="text-[9px] opacity-70">Volcanic Red Heater</span>
                              </button>

                              {/* Cosmic Blackout */}
                              <button
                                type="button"
                                onClick={() => setChatBg({ type: 'color', value: '#030712' })}
                                className={`p-4 rounded-2xl flex flex-col items-center justify-center gap-1 bg-[#030712] border transition text-gray-300 ${
                                  chatBg.type === 'color' && chatBg.value === '#030712' ? 'border-cyan-500 ring-2 ring-cyan-500/20' : 'border-slate-800 hover:opacity-90'
                                }`}
                              >
                                <span className="text-xs font-bold font-display">Deep Abyss</span>
                                <span className="text-[9px] opacity-70">Solid Space Gray</span>
                              </button>
                            </div>
                          </div>

                          <div className="pt-2 border-t border-gray-150 dark:border-gray-800">
                            <label className="text-[10px] font-bold uppercase tracking-wider text-gray-400 dark:text-gray-550 block mb-1.5 select-none text-left">
                              Or set a custom background Unsplash graphic Wallpaper URL:
                            </label>
                            <div className="flex gap-2">
                              <input
                                type="text"
                                placeholder="https://images.unsplash.com/... or raw image link"
                                value={customBgInput}
                                onChange={(e) => {
                                  setCustomBgInput(e.target.value);
                                  if (e.target.value.trim() !== '') {
                                    setChatBg({ type: 'image', value: e.target.value.trim() });
                                  }
                                }}
                                className="flex-1 px-4 py-2 border border-gray-200 dark:border-gray-800 rounded-xl bg-gray-50/50 dark:bg-gray-950/40 text-xs focus:outline-none"
                              />
                            </div>
                            <span className="text-[9px] text-gray-400 block text-left mt-1">Preview applies live to the core chat dashboard frames dynamically!</span>
                          </div>
                        </div>
                      )}

                      {settingsTab === 'tactical' && (
                        <div className="space-y-4">
                          {/* Mute alerts */}
                          <div className="flex items-center justify-between p-3.5 rounded-2xl bg-gray-50/50 dark:bg-gray-950/20 border border-gray-150 dark:border-gray-800">
                            <div className="flex items-center gap-3">
                              {soundMuted ? (
                                <VolumeX className="h-5 w-5 text-red-500" />
                              ) : (
                                <Volume2 className="h-5 w-5 text-cyan-600 dark:text-cyan-400" />
                              )}
                              <div className="flex flex-col text-left leading-tight">
                                <span className="text-xs font-bold text-gray-800 dark:text-gray-200">Survivor Alarm Sound</span>
                                <span className="text-[10px] text-gray-400">Play micro audio alarms on incoming transmissions</span>
                              </div>
                            </div>
                            <button
                              type="button"
                              onClick={() => setSoundMuted(!soundMuted)}
                              className={`p-1.5 rounded-full transition w-12 flex items-center ${
                                !soundMuted ? 'bg-cyan-600 text-white justify-end' : 'bg-gray-200 dark:bg-gray-800 text-gray-400 justify-start'
                              }`}
                            >
                              <span className="h-4 w-4 rounded-full bg-white shadow" />
                            </button>
                          </div>

                          {/* Compact visual density */}
                          <div className="flex items-center justify-between p-3.5 rounded-2xl bg-gray-50/50 dark:bg-gray-950/20 border border-gray-150 dark:border-gray-800">
                            <div className="flex items-center gap-3">
                              <Shield className="h-5 w-5 text-amber-500" />
                              <div className="flex flex-col text-left leading-tight">
                                <span className="text-xs font-bold text-gray-800 dark:text-gray-200">Compact Density</span>
                                <span className="text-[10px] text-gray-400">Reduces spacing inside dialogue grids for rapid overview</span>
                              </div>
                            </div>
                            <button
                              type="button"
                              onClick={() => setCompactMode(!compactMode)}
                              className={`p-1.5 rounded-full transition w-12 flex items-center ${
                                compactMode ? 'bg-cyan-600 text-white justify-end' : 'bg-gray-200 dark:bg-gray-800 text-gray-400 justify-start'
                              }`}
                            >
                              <span className="h-4 w-4 rounded-full bg-white shadow" />
                            </button>
                          </div>

                          {/* Logout Button */}
                          <div className="pt-4 mt-2 border-t border-gray-150 dark:border-gray-800">
                            <button
                              type="button"
                              onClick={handleLogout}
                              className="w-full py-3 rounded-xl bg-red-500/10 hover:bg-red-500/20 text-red-600 dark:text-red-400 font-bold transition flex items-center justify-center border border-red-500/20"
                            >
                              Disconnect Node & Logout
                            </button>
                          </div>
                        </div>
                      )}
                    </div>

                    {/* Footer Actions */}
                    <div className="px-6 py-4.5 border-t border-gray-150 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/60 flex items-center justify-end gap-3.5 select-none">
                      <button
                        type="button"
                        onClick={() => setShowSettingsModal(false)}
                        className="py-2.5 px-5 border border-gray-200 dark:border-gray-800 text-gray-700 dark:text-gray-300 rounded-xl text-xs font-bold hover:bg-gray-50 dark:hover:bg-gray-850 active:scale-95 transition"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={handleSaveSettings}
                        className="py-2.5 px-6 bg-cyan-600 hover:bg-cyan-500 text-white rounded-xl text-xs font-bold shadow-lg shadow-cyan-600/20 active:scale-95 transition cursor-pointer"
                      >
                        Save Configuration
                      </button>
                    </div>
                  </div>
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
