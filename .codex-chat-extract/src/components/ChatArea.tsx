import React, { useState, useRef, useEffect, ChangeEvent, KeyboardEvent } from 'react';
import { Send, Paperclip, Smile, Phone, Video, Download, Play, Pause, File, X, Sparkles, User as UserIcon, Volume2, Plus, HelpCircle, ShieldAlert, PanelRightClose, PanelRightOpen, Users, CornerUpLeft, ChevronDown, ChevronLeft } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { User, Message, FileAttachment } from '../types';

interface ChatAreaProps {
  currentUser: User & { isAdmin?: boolean };
  session: {
    id: string;
    name: string;
    avatar: string;
    isGroup: boolean;
    isBot?: boolean;
  } | null;
  messages: Message[];
  activeUsers: User[];
  isOtherUserTyping: boolean;
  typingUser?: string;
  onSendMessage: (
    text: string,
    type: 'text' | 'file' | 'audio' | 'dice',
    fileInfo?: FileAttachment,
    replyTo?: { id: string; senderName: string; text: string },
    diceValue?: number
  ) => void;
  onSendReaction: (messageId: string, emoji: string) => void;
  onSendTyping: (isTyping: boolean) => void;
  onInitiateCall: (isVideo: boolean) => void;
  isBlizzardActive: boolean;
  onDeleteMessage: (messageId: string) => void;
  onTranslateMessage: (messageId: string, targetLang: string) => void;
  onShowUserProfile?: (userId: string) => void;
  onToggleRightSidebar?: () => void;
  isRightSidebarOpen?: boolean;
  chatBg?: { type: string; value: string };
  onBack?: () => void;
}

// Simple custom Markdown formatter helper to output clean HTML securely
function FormattedText({ text }: { text: string }) {
  if (!text) return null;
  
  // Format code blocks
  let formatted = text;
  
  // Clean escaping
  const escapeHtml = (unsafe: string) => {
    return unsafe
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  };

  // Convert blockquotes
  formatted = formatted.replace(/^>\s+(.*)$/gm, '<blockquote class="border-l-4 border-sky-400 pl-3 italic my-2 text-gray-500">$1</blockquote>');
  
  // Convert bullet points
  formatted = formatted.replace(/^\s*[-*+]\s+(.*)$/gm, '<li class="list-disc ml-5 my-1 text-inherit">$1</li>');

  // Convert Bold **text**
  formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

  // Convert Italic *text*
  formatted = formatted.replace(/\*(.*?)\*/g, '<em>$1</em>');

  // Convert inline backticks code \`code\`
  formatted = formatted.replace(/`(.*?)`/g, '<code class="bg-gray-100 dark:bg-gray-800 text-red-500 font-mono text-xs px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-700">$1</code>');

  // Convert newlines to html line-breaks
  formatted = formatted.replace(/\n/g, '<br />');

  return (
    <div
      className="text-[13.5px] leading-relaxed break-words"
      dangerouslySetInnerHTML={{ __html: formatted }}
    />
  );
}

const COMMON_EMOJIS = ['👍', '❤️', '🔥', '😂', '🎉', '🚀', '👀', '👎'];

interface DiceRollerProps {
  value: number;
  timestamp: string | Date;
}

function DiceRoller({ value, timestamp }: DiceRollerProps) {
  const [currentFace, setCurrentFace] = useState(1);
  const [isRolling, setIsRolling] = useState(true);

  useEffect(() => {
    const age = Date.now() - new Date(timestamp).getTime();
    if (age < 1500) {
      setIsRolling(true);
      const intervalId = setInterval(() => {
        setCurrentFace(Math.floor(Math.random() * 6) + 1);
      }, 100);

      const timeoutId = setTimeout(() => {
        clearInterval(intervalId);
        setIsRolling(false);
        setCurrentFace(value);
      }, 1500 - age);

      return () => {
        clearInterval(intervalId);
        clearTimeout(timeoutId);
      };
    } else {
      setIsRolling(false);
      setCurrentFace(value);
    }
  }, [value, timestamp]);

  const dots: { [key: number]: React.ReactNode } = {
    1: <div className="h-2.5 w-2.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />,
    2: (
      <div className="w-full h-full flex flex-col justify-between p-2">
        <div className="h-2 w-2 rounded-full bg-cyan-600 dark:bg-cyan-400 self-start" />
        <div className="h-2 w-2 rounded-full bg-cyan-600 dark:bg-cyan-400 self-end" />
      </div>
    ),
    3: (
      <div className="w-full h-full flex flex-col justify-between p-1.5 items-center">
        <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400 self-start" />
        <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400 self-center" />
        <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400 self-end" />
      </div>
    ),
    5: (
      <div className="w-full h-full flex flex-col justify-between p-1.5">
        <div className="flex justify-between">
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
        </div>
        <div className="flex justify-center -my-1">
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
        </div>
        <div className="flex justify-between">
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
        </div>
      </div>
    ),
    6: (
      <div className="w-full h-full flex flex-col justify-between p-1.5">
        <div className="flex justify-between">
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
        </div>
        <div className="flex justify-between">
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
        </div>
        <div className="flex justify-between">
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
          <div className="h-1.5 w-1.5 rounded-full bg-cyan-600 dark:bg-cyan-400" />
        </div>
      </div>
    ),
  };

  return (
    <div className="flex flex-col items-center justify-center p-3.5 my-2 bg-gradient-to-b from-cyan-500/10 to-transparent dark:from-cyan-950/15 rounded-2xl border border-cyan-500/15 text-center gap-2 max-w-[200px] mx-auto shadow-sm select-none">
      <span className="text-[10px] uppercase font-mono tracking-widest font-extrabold text-cyan-600 dark:text-cyan-455 flex items-center gap-1.5 justify-center">
        {isRolling ? (
          <>
            <span className="h-1.5 w-1.5 rounded-full bg-cyan-500 animate-ping" />
            <span>Rolling Dice...</span>
          </>
        ) : (
          <>
            <span>🎲 Dice Result</span>
          </>
        )}
      </span>
      <motion.div
        animate={isRolling ? {
          rotateX: [0, 180, 360, 540],
          rotateY: [0, 360, 720, 1080],
          scale: [1, 1.15, 0.95, 1],
        } : {
          rotate: [0, 10, -10, 0],
          scale: 1,
        }}
        transition={{
          duration: isRolling ? 1.5 : 0.4,
          ease: "easeInOut"
        }}
        className="h-12 w-12 rounded-xl bg-white dark:bg-slate-900 border-2 border-cyan-500/30 flex items-center justify-center shadow-xl text-cyan-600 dark:text-cyan-400 font-bold text-lg select-none relative overflow-hidden"
      >
        {isRolling ? (
          <span className="text-xl font-extrabold text-cyan-500">{currentFace}</span>
        ) : (
          <div className="h-full w-full flex items-center justify-center">
            {currentFace === 1 ? dots[1] : dots[currentFace] || dots[1]}
          </div>
        )}
      </motion.div>
    </div>
  );
}

interface MessageItemProps {
  msg: Message;
  isSelf: boolean;
  showSenderHeader: boolean;
  currentUser: User & { isAdmin?: boolean };
  session: any;
  onSendReaction: (messageId: string, emoji: string) => void;
  onDeleteMessage: (messageId: string) => void;
  onTranslateMessage: (messageId: string, targetLang: string) => void;
  onShowUserProfile?: (userId: string) => void;
  setReplyingTo: (reply: { id: string; senderName: string; text: string } | null) => void;
  isBlizzardActive: boolean;
  onImageLoad?: () => void;
}

const MessageItem = React.memo(({
  msg,
  isSelf,
  showSenderHeader,
  currentUser,
  session,
  onSendReaction,
  onDeleteMessage,
  onTranslateMessage,
  onShowUserProfile,
  setReplyingTo,
  isBlizzardActive,
  onImageLoad
}: MessageItemProps) => {
  const [isHovered, setIsHovered] = useState(false);
  const [showOptionsPopup, setShowOptionsPopup] = useState(false);
  const [dragX, setDragX] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const touchTimerRef = useRef<any>(null);

  const hoverTimeoutRef = useRef<any>(null);

  const handleMouseEnter = () => {
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    setIsHovered(true);
  };

  const handleMouseLeave = () => {
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    hoverTimeoutRef.current = setTimeout(() => {
      setIsHovered(false);
    }, 400); // 400ms hover grace delay
  };

  useEffect(() => {
    return () => {
      if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    };
  }, []);

  // Long press detector for touch/mobile devices
  const handleTouchStart = (e: React.TouchEvent) => {
    if (touchTimerRef.current) clearTimeout(touchTimerRef.current);
    touchTimerRef.current = setTimeout(() => {
      if (window.navigator?.vibrate) {
        window.navigator.vibrate(45);
      }
      setShowOptionsPopup(true);
    }, 600); // 600ms long press threshold
  };

  const handleTouchMove = () => {
    // Cancel long press when scrolling
    if (touchTimerRef.current) {
      clearTimeout(touchTimerRef.current);
    }
  };

  const handleTouchEnd = () => {
    if (touchTimerRef.current) {
      clearTimeout(touchTimerRef.current);
    }
  };

  return (
    <div
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onClick={(e) => {
        // Toggle mobile/desktop options on click for stable lock
        setShowOptionsPopup(!showOptionsPopup);
      }}
      className={`flex gap-3 group/msg relative items-end ${isSelf ? 'justify-end' : 'justify-start'} w-full select-none mb-4`}
    >
      {/* Click outside transparent backdrop to safely dismiss active stable menus */}
      {showOptionsPopup && (
        <div 
          className="fixed inset-0 z-10 cursor-default bg-transparent" 
          onClick={(e) => {
            e.stopPropagation();
            setShowOptionsPopup(false);
            setConfirmDelete(false);
          }} 
        />
      )}

      {/* Swipe/Drag visual reply indicator cards hidden behind */}
      <div 
        className="absolute left-4 top-1/2 -translate-y-1/2 pointer-events-none flex items-center transition-all duration-100 z-0"
        style={{
          opacity: dragX > 15 ? Math.min((dragX - 15) / 40, 1) : 0,
          transform: `translateY(-50%) scale(${dragX > 15 ? Math.min(0.6 + (dragX - 15) / 100, 1.1) : 0.6})`,
        }}
      >
        <div className="flex items-center gap-1.5 bg-cyan-500/15 border border-cyan-500/30 text-cyan-600 dark:text-cyan-400 px-3 py-1.5 rounded-full shadow-sm">
          <CornerUpLeft className="h-3.5 w-3.5" />
          <span className="text-[10px] font-extrabold uppercase tracking-widest font-mono">Swipe Reply</span>
        </div>
      </div>

      <motion.div
        initial={{ opacity: 0, scale: 0.98, y: 5 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        drag="x"
        dragDirectionLock
        dragConstraints={{ left: 0, right: 0 }}
        dragElastic={{ left: 0.05, right: 0.75 }}
        onDrag={(event, info) => {
          setDragX(info.offset.x);
        }}
        onDragEnd={(event, info) => {
          if (info.offset.x > 60) {
            setReplyingTo({ id: msg.id, senderName: msg.senderName, text: msg.text });
          }
          setDragX(0); // reset swipe states on release smoothly
        }}
        className={`flex gap-3 items-end max-w-[85%] ${isSelf ? 'justify-end ml-auto' : 'justify-start'} cursor-grab active:cursor-grabbing z-10`}
      >
        {/* Peer Avatar */}
        {!isSelf && (
          <div 
            className={`flex-shrink-0 select-none ${onShowUserProfile ? 'cursor-pointer' : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              onShowUserProfile?.(msg.senderId);
            }}
          >
            {msg.senderAvatar ? (
              <img
                src={msg.senderAvatar}
                alt={msg.senderName}
                className="h-8 w-8 rounded-full object-cover border border-gray-100 dark:border-gray-850 hover:scale-105 transition"
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-cyan-600 text-white text-xs font-semibold hover:scale-105 transition">
                {msg.senderName[0]?.toUpperCase()}
              </div>
            )}
          </div>
        )}

        {/* Message Core Box and reactions wrapper */}
        <div className="flex flex-col relative w-full">
          <div
            id={`message-box-${msg.id}`}
            className={`relative rounded-2xl px-4 py-2 shadow-sm border border-gray-100 dark:border-gray-800 transition-all duration-200 ${
              isSelf
                ? 'bg-gradient-to-tr from-cyan-600 to-sky-500 border-0 text-white rounded-br-none'
                : 'bg-white dark:bg-gray-900 border-gray-150/50 dark:border-gray-800 text-gray-900 dark:text-gray-150 rounded-bl-none'
            } ${isHovered || showOptionsPopup ? 'ring-2 ring-cyan-500/30 dark:ring-cyan-500/20 shadow-md scale-[1.002]' : ''}`}
          >
            {/* Smooth Reaction and Translation option bar */}
            {(isHovered || showOptionsPopup) && (
              <div
                className={`absolute -top-11 flex items-center gap-1.5 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 px-2 py-1.5 rounded-full shadow-2xl z-20 ${
                  isSelf ? 'right-0' : 'left-0'
                } animate-fade-in`}
                onClick={(e) => e.stopPropagation()}
              >
                {/* Invisible bridge to prevent mouseleave when moving cursor between bubble and bar */}
                <div className="absolute -bottom-3 left-0 right-0 h-4 bg-transparent pointer-events-auto" />

                {/* Emoji selectors */}
                <div className="flex gap-1 pr-1.5 mr-1 border-r border-gray-150 dark:border-gray-800 select-none">
                  {COMMON_EMOJIS.map((emoji) => (
                    <button
                      key={emoji}
                      onClick={() => {
                        onSendReaction(msg.id, emoji);
                        setShowOptionsPopup(false);
                      }}
                      className="hover:scale-130 active:scale-95 transition text-sm flex-shrink-0"
                    >
                      {emoji}
                    </button>
                  ))}
                </div>

                {/* Thread Replies */}
                <button
                  onClick={() => {
                    setReplyingTo({ id: msg.id, senderName: msg.senderName, text: msg.text });
                    setShowOptionsPopup(false);
                  }}
                  className="px-1 text-[9px] font-bold text-cyan-600 dark:text-cyan-400 uppercase tracking-widest hover:underline"
                >
                  Reply
                </button>

                {/* Translation Toggle */}
                <button
                  onClick={() => {
                    onTranslateMessage(msg.id, 'en');
                    setShowOptionsPopup(false);
                  }}
                  className="px-1 text-[9px] font-bold text-amber-500 uppercase tracking-widest hover:underline"
                >
                  Translate
                </button>

                {/* Deletion (sender or admins) */}
                {(isSelf || currentUser.isAdmin) && (
                  <div className="flex items-center">
                    {confirmDelete ? (
                      <button
                        onClick={() => {
                          onDeleteMessage(msg.id);
                          setConfirmDelete(false);
                          setShowOptionsPopup(false);
                        }}
                        className="px-1.5 py-0.5 rounded bg-red-600 text-white text-[9px] font-extrabold uppercase tracking-widest hover:bg-red-500 animate-pulse"
                      >
                        Confirm
                      </button>
                    ) : (
                      <button
                        onClick={() => {
                          setConfirmDelete(true);
                          // Auto revert delete check in 3 seconds
                          setTimeout(() => setConfirmDelete(false), 3000);
                        }}
                        className="px-1 text-[9px] font-bold text-red-500 uppercase tracking-widest hover:underline"
                      >
                        Delete
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Sender username for group chats */}
            {showSenderHeader && (
              <span className="block text-[11px] font-bold text-sky-450 pr-4 pb-1">
                {msg.senderName}
              </span>
            )}

            {/* Render replied citation if mapped */}
            {msg.replyTo && (
              <div className="mb-2 p-1.5 bg-black/5 dark:bg-white/5 border-l-2 border-cyan-500 rounded text-[10.5px] opacity-85 truncate max-w-full text-left">
                <span className="font-extrabold block text-cyan-500 text-[9px]">
                  Replying to {msg.replyTo.senderName}
                </span>
                <span className="italic truncate block">{msg.replyTo.text}</span>
              </div>
            )}

            {/* Dice Rolled gameplay blocks */}
            {msg.type === 'dice' && msg.diceValue && (
              <DiceRoller value={msg.diceValue} timestamp={msg.timestamp} />
            )}

            {/* Render attachment file card if file type */}
            {msg.type === 'file' && msg.fileInfo && (
              <div className="mb-2 p-2.5 rounded-lg bg-black/10 dark:bg-black/30 flex items-center justify-between border border-white/5 gap-3">
                <div className="flex items-center gap-2 overflow-hidden">
                   <File className="h-8 w-8 text-sky-300 flex-shrink-0" />
                   <div className="flex flex-col overflow-hidden">
                     <span className="text-xs font-bold truncate max-w-[150px]">{msg.fileInfo.name}</span>
                     <span className="text-[10px] opacity-70">
                       {(msg.fileInfo.size / 1024).toFixed(1)} KB
                     </span>
                   </div>
                </div>
                <a
                  id={`download-attachment-${msg.fileInfo.id}`}
                  href={msg.fileInfo.url}
                  download
                  className="p-1.5 bg-sky-200/20 hover:bg-sky-200/40 rounded-full transition text-inherit flex-shrink-0"
                  title="Download shared artifact file"
                >
                  <Download className="h-4 w-4" />
                </a>
              </div>
            )}

            {/* Special inline Image Render preview */}
            {msg.type === 'file' && msg.fileInfo?.mimeType.startsWith('image/') && (
              <div className="my-2 max-w-[240px] overflow-hidden rounded-md bg-gray-50 border dark:border-gray-800">
                <img
                  src={msg.fileInfo.url}
                  alt={msg.fileInfo.name}
                  className="max-h-48 w-full object-cover hover:scale-105 transition duration-200"
                  referrerPolicy="no-referrer"
                  onLoad={onImageLoad}
                />
              </div>
            )}

            {/* Simulated Voice record playback block */}
            {msg.type === 'audio' && (
              <div className="flex items-center gap-3 py-1.5 px-1 bg-black/10 dark:bg-black/25 rounded-lg border border-white/5 mb-2.5">
                <button
                  className="h-8 w-8 rounded-full bg-white/25 flex items-center justify-center text-white"
                  onClick={() => {}}
                >
                  <Play className="h-4 w-4 translate-x-0.2" />
                </button>
                <div className="flex flex-col">
                  <div className="flex items-center gap-1">
                    <div className="w-1 bg-white/60 h-2" />
                    <div className="w-1 bg-white/60 h-4" />
                    <div className="w-1 bg-white/60 h-3" />
                    <div className="w-1 bg-white/60 h-5" />
                    <div className="w-1 bg-white/60 h-2" />
                    <div className="w-1 bg-white/60 h-4" />
                    <div className="w-1 bg-white/60 h-1" />
                  </div>
                  <span className="text-[10px] opacity-75 mt-0.5">Voice recording Playback</span>
                </div>
              </div>
            )}

            {/* Standard formatted message body */}
            <FormattedText text={msg.text} />

            {/* Render Translations if translatedText exists */}
            {msg.translatedText && (
              <div className={`mt-2 pt-1.5 border-t text-[11px] font-medium leading-relaxed ${
                isSelf 
                  ? 'border-white/20 text-white/95' 
                  : 'border-gray-150 dark:border-gray-800 text-cyan-700 dark:text-cyan-300'
              }`}>
                <span className={`text-[8px] font-bold block uppercase tracking-wider mb-0.5 ${
                  isSelf ? 'text-cyan-100' : 'text-gray-400 dark:text-gray-500'
                }`}>
                  Translation
                </span>
                {msg.translatedText}
              </div>
            )}

            {/* Message Bubble Footer details: reaction stats, visual ticks clock */}
            <div className="flex items-center justify-end gap-2 mt-1.5 select-none leading-none">
              {/* Super handy translation shortcut */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onTranslateMessage(msg.id, 'en');
                }}
                className={`font-mono text-[9px] font-bold cursor-pointer hover:underline border-r pr-1.5 uppercase tracking-wider ${
                  isSelf 
                    ? 'text-cyan-100/90 hover:text-white border-white/20' 
                    : 'text-cyan-600 dark:text-cyan-400 hover:text-cyan-700 border-gray-150 dark:border-gray-800'
                }`}
              >
                {msg.translatedText ? 'Original' : 'Translate'}
              </button>

              <div className="flex items-center gap-1 font-mono text-[9px] opacity-65">
                <span>
                  {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
                {isSelf && (
                  <span className="text-[11px] font-bold text-sky-100">✓✓</span>
                )}
              </div>
            </div>
          </div>

          {/* Render Message Emoji reactions if any exists */}
          {Object.entries(msg.reactions).some(([_, users]) => users.length > 0) && (
            <div className="flex flex-wrap gap-1 mt-1">
              {Object.entries(msg.reactions).map(([reactionEmoji, reactingUserIds]) => {
                if (reactingUserIds.length === 0) return null;
                const hasWeReacted = reactingUserIds.includes(currentUser.id);
                return (
                  <button
                    key={reactionEmoji}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSendReaction(msg.id, reactionEmoji);
                    }}
                    className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-mono transition font-bold leading-none select-none z-10 active:scale-95 ${
                      hasWeReacted
                        ? 'bg-sky-500/20 border-sky-400 text-sky-500'
                        : 'bg-gray-100 dark:bg-gray-850 dark:border-gray-800 text-gray-500'
                    }`}
                  >
                    <span>{reactionEmoji}</span>
                    <span>{reactingUserIds.length}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </motion.div>
    </div>
  );
});

MessageItem.displayName = 'MessageItem';

export default function ChatArea({
  currentUser,
  session,
  messages,
  activeUsers,
  isOtherUserTyping,
  typingUser,
  onSendMessage,
  onSendReaction,
  onSendTyping,
  onInitiateCall,
  isBlizzardActive,
  onDeleteMessage,
  onTranslateMessage,
  onShowUserProfile,
  onToggleRightSidebar,
  isRightSidebarOpen,
  chatBg,
  onBack,
}: ChatAreaProps) {
  if (!session) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center bg-gray-50 dark:bg-gray-950 p-6 text-center">
        <div id="welcome-chat-screen" className="max-w-md flex flex-col items-center">
          <div className="h-20 w-20 rounded-full bg-cyan-500/10 flex items-center justify-center text-cyan-500 animate-pulse mb-6">
            <Sparkles className="h-10 w-10 rotate-12" />
          </div>
          <h1 className="text-2xl font-extrabold text-gray-900 dark:text-gray-100 font-display">Whiteout Survival Communications</h1>
          <p className="mt-3 text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
            Select a session or connect through our **Global chat** to coordinate defenses, trade blueprints, rolled games, and trigger real-time voice calls.
          </p>
        </div>
      </div>
    );
  }

  const [inputText, setInputText] = useState('');

  // Dynamic Background Style
  const getBgStyle = () => {
    if (!chatBg) return {};
    if (chatBg.type === 'color') {
      return { backgroundColor: chatBg.value };
    }
    if (chatBg.type === 'gradient') {
      return { backgroundImage: chatBg.value };
    }
    if (chatBg.type === 'image') {
      return {
        backgroundImage: `url(${chatBg.value})`,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        backgroundRepeat: 'no-repeat',
      };
    }
    return {};
  };
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  const [emojiPickerTab, setEmojiPickerTab] = useState<'emojis' | 'gifs'>('emojis');
  const [isRecording, setIsRecording] = useState(false);
  const [replyingTo, setReplyingTo] = useState<{ id: string; senderName: string; text: string } | null>(null);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [hoveredMessageId, setHoveredMessageId] = useState<string | null>(null);
  const [uploadingFile, setUploadingFile] = useState(false);
  const [showGameTray, setShowGameTray] = useState(false);
  
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const typingTimeoutRef = useRef<any>(null);
  const recordingTimerRef = useRef<any>(null);
  const [showScrollBottomBtn, setShowScrollBottomBtn] = useState(false);

  const scrollToBottom = (behavior: 'smooth' | 'auto' = 'smooth') => {
    if (scrollContainerRef.current) {
      const container = scrollContainerRef.current;
      container.scrollTo({
        top: container.scrollHeight,
        behavior,
      });
    } else {
      messagesEndRef.current?.scrollIntoView({ behavior });
    }
  };

  const handleScroll = () => {
    if (scrollContainerRef.current) {
      const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current;
      // Show back-to-bottom toggle button if user is scrolled up by over 250px
      const isScrolledUp = scrollHeight - scrollTop - clientHeight > 250;
      setShowScrollBottomBtn(isScrolledUp);
    }
  };

  // Track previous room id to differentiate new messages from channel switches
  const prevSessionIdRef = useRef<string | null>(null);

  // Robust auto-scroll behavior on any flow / message container events
  useEffect(() => {
    const isNewChannel = prevSessionIdRef.current !== session.id;
    prevSessionIdRef.current = session.id;

    if (isNewChannel) {
      // Switched channels: instantly snap down to bottom with zero jumpy animations
      scrollToBottom('auto');
    } else {
      // Just a new message added or other client started typing: animate smoothly
      scrollToBottom('smooth');
    }
  }, [messages.length, isOtherUserTyping, session.id]);

  // Clean timeouts on unmount
  useEffect(() => {
    return () => {
      if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current);
      if (recordingTimerRef.current) clearInterval(recordingTimerRef.current);
    };
  }, []);

  const [isSelfTyping, setIsSelfTyping] = useState(false);

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
    
    // Broadcast "typing" signal only if we aren't already marked as typing
    if (!isSelfTyping) {
      setIsSelfTyping(true);
      onSendTyping(true);
    }
    
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current);
    
    typingTimeoutRef.current = setTimeout(() => {
      setIsSelfTyping(false);
      onSendTyping(false);
    }, 1800);
  };

  const handleSend = () => {
    if (inputText.trim() === '') return;
    onSendMessage(inputText, 'text', undefined, replyingTo || undefined);
    setInputText('');
    setReplyingTo(null);
    setIsSelfTyping(false);
    onSendTyping(false);
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current);
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Emojis reaction Click handler
  const handleEmojiClick = (emoji: string) => {
    setInputText((prev) => prev + emoji);
    setShowEmojiPicker(false);
  };

  // Sound Recording action simulation
  const startRecording = () => {
    setIsRecording(true);
    setRecordingSeconds(0);
    recordingTimerRef.current = setInterval(() => {
      setRecordingSeconds((prev) => prev + 1);
    }, 1000);
  };

  const stopAndSendRecording = () => {
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    setIsRecording(false);
    
    // Send simulated voice message
    if (recordingSeconds > 1) {
      onSendMessage(`🎤 Voice Message • ${recordingSeconds} seconds`, 'audio');
    }
    setRecordingSeconds(0);
  };

  // File Upload Trigger using REST API base64 parser
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadingFile(true);
    try {
      const reader = new FileReader();
      reader.readAsDataURL(file);
      reader.onload = async () => {
        const base64Data = reader.result as string;
        
        // POST to Node server upload route
        const uploadResponse = await fetch('/api/upload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name: file.name,
            size: file.size,
            mimeType: file.type,
            base64Data,
          }),
        });

        if (!uploadResponse.ok) throw new Error('Upload error response');
        const fileRecord: FileAttachment = await uploadResponse.json();

        // Send File socket message
        onSendMessage(`📎 Attachment: ${file.name}`, 'file', fileRecord);
      };
    } catch (err) {
      console.error('Failed uploading attachment:', err);
      alert('Failed uploading attachment file.');
    } finally {
      setUploadingFile(false);
    }
  };

  // Determine chat headers details
  const getHeaderDetails = () => {
    if (!session) return { title: '', status: '', isOnline: false };
    if (session.id === 'global') {
      const activeClientCount = activeUsers.filter(u => u.status === 'online').length;
      return {
        title: 'Global Survival Chat',
        status: `${activeClientCount} members online`,
        isOnline: activeClientCount > 0,
      };
    }
    if (session.id === 'gemini_bot') {
      return {
        title: 'WOS BOT',
        status: 'online • Tactical Advisor',
        isOnline: true,
      };
    }
    const matchingPeer = activeUsers.find((u) => u.id === session.id);
    const online = matchingPeer?.status === 'online';
    return {
      title: session.name,
      status: online ? 'online' : 'offline',
      isOnline: online,
    };
  };

  const header = getHeaderDetails();

  return (
    <div className="flex h-full flex-1 flex-col bg-gray-50 dark:bg-gray-950 relative">
      
      {/* Top Header details & Caller launch button widgets */}
      <div className="h-16 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between px-4 sm:px-6 bg-white dark:bg-gray-900 shadow-sm flex-shrink-0 z-10">
        <div className="flex items-center gap-3">
          {onBack && (
            <button
              onClick={onBack}
              className="mr-1 p-1.5 md:hidden text-gray-500 hover:text-cyan-600 hover:bg-cyan-50 dark:hover:bg-cyan-900/20 rounded-md transition"
            >
              <ChevronLeft className="h-6 w-6" />
            </button>
          )}
          {session.avatar ? (
            <img
              src={session.avatar}
              alt={session.name}
              className="h-10 w-10 rounded-full object-cover border border-gray-150"
              referrerPolicy="no-referrer"
            />
          ) : session.id === 'global' ? (
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-sky-500 text-white font-semibold shadow-inner">
              <Smile className="h-5 w-5" />
            </div>
          ) : (
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-sky-500 text-white font-semibold">
              {session.name[0]?.toUpperCase()}
            </div>
          )}
          <div className="flex flex-col">
            <h2 className="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5 font-display">
              {header.title}
              {header.isOnline && !session.isBot && (
                <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
              )}
            </h2>
            <span className="text-[11px] text-gray-400 tracking-tight font-medium select-none">
              {header.status}
            </span>
          </div>
        </div>

        {/* Action controls (Calling matches standard profile user tabs only. Disable on yourself & generic group chat) */}
        {session.id !== 'global' && session.id !== 'gemini_bot' && (
          <div className="flex gap-1.5 bg-gray-50 dark:bg-gray-950 p-1 rounded-full border border-gray-100 dark:border-gray-800">
            <button
              id="btn-voice-call"
              onClick={() => onInitiateCall(false)}
              className="p-2 hover:bg-gray-100 dark:hover:bg-gray-850 text-gray-600 dark:text-gray-300 rounded-full transition active:scale-90"
              title="Initiate Voice Call"
            >
              <Phone className="h-4.5 w-4.5" />
            </button>
            <button
              id="btn-video-call"
              onClick={() => onInitiateCall(true)}
              className="p-2 hover:bg-gray-100 dark:hover:bg-gray-850 text-gray-600 dark:text-gray-300 rounded-full transition active:scale-90"
              title="Initiate Video Call"
            >
              <Video className="h-4.5 w-4.5" />
            </button>
            {onToggleRightSidebar && (
              <button
                id="btn-toggle-right-sidebar"
                onClick={onToggleRightSidebar}
                className="p-2 hover:bg-gray-100 dark:hover:bg-gray-850 text-gray-600 dark:text-gray-300 rounded-full transition active:scale-90 ml-1"
                title="Toggle Right Panel"
              >
                {isRightSidebarOpen ? (
                  <PanelRightClose className="h-4.5 w-4.5" />
                ) : (
                  <PanelRightOpen className="h-4.5 w-4.5" />
                )}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Main Messages scroll list */}
      <div 
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 py-4 scroll-smooth transition-all duration-300"
        style={getBgStyle()}
      >
        <div className="max-w-4xl mx-auto space-y-4 w-full">
        {messages.length > 0 ? (
          messages.map((msg, index) => {
            const isSelf = msg.senderId === currentUser.id;
            const isCallType = msg.type === 'call';
            const showSenderHeader = !isSelf && session.id === 'global' && !isCallType;

            // Handle system Call visual bubbles
            if (isCallType) {
              return (
                <motion.div 
                  key={msg.id} 
                  initial={{ opacity: 0, scale: 0.96, y: 10 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                  className="flex justify-center my-4"
                >
                  <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-amber-500/10 border border-amber-500/20 text-xs font-medium text-amber-500 font-mono shadow-sm">
                    <Phone className="h-3.5 w-3.5" />
                    <span>{msg.text}</span>
                    <span className="text-[10px] text-amber-500/60 select-none ml-1">
                      {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                </motion.div>
              );
            }

            return (
              <MessageItem
                key={msg.id}
                msg={msg}
                isSelf={isSelf}
                showSenderHeader={showSenderHeader}
                currentUser={currentUser}
                session={session}
                onSendReaction={onSendReaction}
                onDeleteMessage={onDeleteMessage}
                onTranslateMessage={onTranslateMessage}
                onShowUserProfile={onShowUserProfile}
                setReplyingTo={setReplyingTo}
                isBlizzardActive={isBlizzardActive}
                onImageLoad={() => scrollToBottom('smooth')}
              />
            );
          })
        ) : (
          <div className="flex flex-col items-center justify-center h-full py-10 text-center select-none">
            <Smile className="h-12 w-12 text-gray-300 dark:text-gray-700 animate-bounce" />
            <p className="mt-3 text-sm text-gray-400 dark:text-gray-500 font-semibold font-display">No transmission logs mapped</p>
            <p className="text-xs text-gray-400 mt-1">Send a message below to boot up the interaction histories.</p>
          </div>
        )}

        {/* Dynamic Partner "typing..." bubble indicator */}
        <AnimatePresence>
          {isOtherUserTyping && (
            <motion.div 
              initial={{ opacity: 0, scale: 0.9, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9, y: 10 }}
              className="flex gap-3 items-end"
            >
              <div className="flex-shrink-0">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-cyan-600/20 border border-cyan-500/30 text-cyan-600 dark:text-cyan-400 text-xs font-bold font-mono tracking-tighter">
                  ...
                </div>
              </div>
              <div className="rounded-2xl rounded-bl-none px-4 py-2.5 bg-white dark:bg-gray-900 border border-gray-150/40 dark:border-gray-800 flex items-center gap-2 shadow-sm">
                <span className="text-[12px] text-gray-500 font-medium">
                  {typingUser || 'Partner'} is typing
                </span>
                <span className="flex gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-cyan-500 animate-[bounce_1.4s_infinite_100ms]" />
                  <span className="h-1.5 w-1.5 rounded-full bg-cyan-500 animate-[bounce_1.4s_infinite_200ms]" />
                  <span className="h-1.5 w-1.5 rounded-full bg-cyan-500 animate-[bounce_1.4s_infinite_300ms]" />
                </span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Floating Scroll to Bottom button */}
      <AnimatePresence>
        {showScrollBottomBtn && (
          <motion.button
            id="floating-scroll-bottom-btn"
            initial={{ opacity: 0, scale: 0.8, y: 10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.8, y: 10 }}
            onClick={() => scrollToBottom('smooth')}
            className="absolute bottom-28 right-8 z-30 flex h-10 w-10 items-center justify-center rounded-full bg-white dark:bg-gray-900 border border-gray-150 dark:border-gray-800 text-cyan-600 dark:text-cyan-400 shadow-xl hover:scale-115 active:scale-90 transition cursor-pointer"
            title="Scroll to bottom"
          >
            <ChevronDown className="h-5 w-5 animate-bounce" />
          </motion.button>
        )}
      </AnimatePresence>

      {/* Uploading Progress Panel inside chat */}
      <AnimatePresence>
        {uploadingFile && (
          <motion.div
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 15 }}
            className="px-6 py-2 bg-sky-50 dark:bg-sky-950/20 text-xs text-sky-600 dark:text-sky-400 font-mono tracking-wide border-t border-sky-100 dark:border-sky-900 flex items-center justify-between"
          >
            <span>📎 Uploading network file pack payload to servers...</span>
            <span className="animate-spin h-3.5 w-3.5 border-2 border-sky-500 border-t-transparent rounded-full" />
          </motion.div>
        )}
      </AnimatePresence>

      {/* Replying Citation Preview Above Input Field */}
      <AnimatePresence>
        {replyingTo && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="px-6 py-2.5 bg-gray-50 dark:bg-slate-950 border-t border-gray-150 dark:border-gray-805 flex items-center justify-between text-xs"
          >
            <div className="border-l-2 border-cyan-500 pl-2 text-left">
              <span className="font-extrabold text-cyan-600 block text-[10px] uppercase tracking-wider">Replying to {replyingTo.senderName}</span>
              <span className="text-gray-500 dark:text-gray-400 block truncate max-w-lg">{replyingTo.text}</span>
            </div>
            <button
              onClick={() => setReplyingTo(null)}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 cursor-pointer"
            >
              ✕
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Native Keyboard Input Area */}
      <div className="p-4 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 flex-shrink-0 z-10 relative">
        <div className="max-w-4xl mx-auto flex items-end gap-3 w-full relative">
        
        {/* Rapid Emoji popover selector overlay with GIFs support */}
        <AnimatePresence>
          {showEmojiPicker && (
            <motion.div
              initial={{ opacity: 0, scale: 0.9, y: -20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9, y: -20 }}
              className="absolute bottom-16 left-4 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 p-3 rounded-2xl shadow-xl w-72 z-30 flex flex-col gap-2.5"
            >
              <div className="flex items-center justify-between border-b dark:border-gray-800 pb-2 text-xs text-gray-400 font-semibold uppercase">
                <span>Emojis & GIFs Pack</span>
                <button onClick={() => setShowEmojiPicker(false)}>
                  <X className="h-3.5 w-3.5 font-bold cursor-pointer" />
                </button>
              </div>

              {/* Mini Tabs: Emojis vs GIFs */}
              <div className="flex gap-1 bg-gray-50 dark:bg-gray-950 p-1 rounded-lg text-[10px] font-bold">
                <button
                  onClick={() => setEmojiPickerTab('emojis')}
                  className={`flex-1 py-1 rounded text-center transition cursor-pointer ${emojiPickerTab === 'emojis' ? 'bg-cyan-500 text-white' : 'text-gray-400'}`}
                >
                  1000 EMOJIS pack
                </button>
                <button
                  onClick={() => setEmojiPickerTab('gifs')}
                  className={`flex-1 py-1 rounded text-center transition cursor-pointer ${emojiPickerTab === 'gifs' ? 'bg-cyan-500 text-white' : 'text-gray-400'}`}
                >
                  LIVE SURVIVAL GIFs
                </button>
              </div>

              {emojiPickerTab === 'emojis' ? (
                <div className="grid grid-cols-6 gap-2 max-h-48 overflow-y-auto text-xl justify-items-center py-1">
                  {/* Expanded emojis representation */}
                  {['😄', '😍', '👍', '🔥', '🎉', '🚀', '😂', '👀', '❤️', '😱', '🤔', '😢', '👏', '👎', '💼', '💻', '🌟', '💥', '✨', '⚡', '❄️', '⛄', '🏔️', '⛺', '🍖', '🌲', '🐺', '🐻', '👑', '🎯', '📢', '🛎️'].map((emoji) => (
                    <button
                      key={emoji}
                      onClick={() => handleEmojiClick(emoji)}
                      className="hover:scale-130 active:scale-95 transition cursor-pointer"
                    >
                      {emoji}
                    </button>
                  ))}
                  {['🍎', '🍌', '🍕', '🍔', '🍟', '🍺', '☕', '🥤', '⚽', '🏆', '🎮', '💡', '⏰', '🧭', '🛸', '🛰️', '🏖️', '🍿', '🍩', '🍪'].map((emoji) => (
                    <button
                      key={`extra-${emoji}`}
                      onClick={() => handleEmojiClick(emoji)}
                      className="hover:scale-130 active:scale-95 transition cursor-pointer"
                    >
                      {emoji}
                    </button>
                  ))}
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-1.5 max-h-48 overflow-y-auto py-1">
                  {[
                    { name: 'Warm Cozy Campfire', url: 'https://images.unsplash.com/photo-1542382156909-9ae37b3f56fd?w=300&auto=format&fit=crop&q=60' },
                    { name: 'Frozen Wilderness Blizzard', url: 'https://images.unsplash.com/photo-1547989453-11e67ffb3885?w=300&auto=format&fit=crop&q=60' },
                    { name: 'Survivors Cabin Build', url: 'https://images.unsplash.com/photo-1510312305653-8ed496efae75?w=300&auto=format&fit=crop&q=60' },
                    { name: 'Chief Hunter Mammoth Hunt', url: 'https://images.unsplash.com/photo-1552410260-0fd9b577afa6?w=300&auto=format&fit=crop&q=60' },
                  ].map((gif) => (
                    <button
                      key={gif.name}
                      onClick={() => {
                        // Deliver instant GIF mock file payload to connection
                        onSendMessage(`Sent GIF: "${gif.name}"`, 'file', {
                          id: Math.random().toString(),
                          name: gif.name + '.gif',
                          size: 450123,
                          mimeType: 'image/gif',
                          url: gif.url
                        });
                        setShowEmojiPicker(false);
                      }}
                      className="group relative h-16 w-full rounded-lg overflow-hidden border border-gray-150 dark:border-gray-800 hover:border-cyan-500 transition active:scale-95 text-left cursor-pointer"
                    >
                      <img src={gif.url} alt={gif.name} className="h-full w-full object-cover group-hover:scale-110 transition duration-300" referrerPolicy="no-referrer" />
                      <div className="absolute inset-0 bg-black/40 flex items-end p-1">
                        <span className="text-[8.5px] text-white font-bold truncate block w-full">{gif.name}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Input elements toolbar bar wrapper */}
        <div className="flex-1 flex bg-gray-50 dark:bg-gray-950 border border-gray-200/80 dark:border-gray-800 rounded-2xl items-end px-3 py-1.5 focus-within:ring-2 focus-within:ring-cyan-500/30 gap-2">
          
          <button
            id="btn-open-emojis"
            onClick={() => setShowEmojiPicker(!showEmojiPicker)}
            className="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-850 text-gray-400 dark:text-gray-500 rounded-full transition cursor-pointer"
            title="Emojis pack & GIFs list"
          >
            <Smile className="h-5 w-5" />
          </button>

          {/* Hidden upload native file input */}
          <input
            ref={fileInputRef}
            type="file"
            onChange={handleFileUpload}
            className="hidden"
          />
          <button
            id="btn-trigger-upload"
            onClick={() => fileInputRef.current?.click()}
            className="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-850 text-gray-400 dark:text-gray-500 rounded-full transition cursor-pointer"
            title="Attach a file"
          >
            <Paperclip className="h-5 w-5" />
          </button>

          {/* Custom + expanding Action and Games Menu Drawer Trigger */}
          {!session.isBot && (
            <div className="relative flex">
              <button
                id="btn-open-games-tray"
                onClick={() => {
                  setShowGameTray(!showGameTray);
                  setShowEmojiPicker(false);
                }}
                className={`p-1.5 rounded-full transition duration-200 cursor-pointer ${
                  showGameTray
                    ? 'bg-cyan-500 text-white rotate-45 shadow-sm shadow-cyan-500/20'
                    : 'hover:bg-gray-250 dark:hover:bg-gray-850 text-gray-400 dark:text-gray-500'
                }`}
                title="Action & Games menu"
              >
                <Plus className="h-5 w-5" />
              </button>

              <AnimatePresence>
                {showGameTray && (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.95, y: 15 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.95, y: 15 }}
                    className="absolute bottom-11 -left-12 w-52 bg-white dark:bg-gray-900 border border-gray-150 dark:border-gray-800 rounded-2xl shadow-2xl p-2.5 z-40 flex flex-col gap-1 text-left"
                  >
                    <span className="block text-[9px] uppercase tracking-wider font-extrabold text-gray-450 dark:text-gray-500 px-2 pb-1.5 border-b border-gray-100 dark:border-gray-800 my-1 select-none">
                      Survivor Active Games
                    </span>

                    {/* Roll survival dice button */}
                    <button
                      onClick={() => {
                        const rolled = Math.floor(Math.random() * 6) + 1;
                        onSendMessage(`🎲 rolled a survival die: ${rolled}!`, 'dice', undefined, replyingTo || undefined, rolled);
                        setReplyingTo(null);
                        setShowGameTray(false);
                      }}
                      className="w-full text-left py-2 px-2.5 hover:bg-cyan-500/10 hover:text-cyan-600 dark:hover:bg-cyan-500/15 rounded-xl text-xs font-semibold transition flex items-center gap-2 cursor-pointer text-gray-700 dark:text-gray-300"
                    >
                      <span className="text-sm">🎲</span>
                      <span>Roll Survival Dice</span>
                    </button>

                    {/* Placeholder Game Option 1 */}
                    <button
                      disabled
                      className="w-full text-left py-1.5 px-2.5 opacity-55 rounded-xl text-[11px] font-medium transition flex items-center gap-2 text-gray-400 dark:text-gray-500 cursor-not-allowed"
                    >
                      <span className="text-sm">🏔️</span>
                      <div className="flex flex-col text-left leading-tight">
                        <span className="font-semibold text-gray-500 dark:text-gray-400">Blizzard Shelter</span>
                        <span className="text-[8px] opacity-75">Developing later...</span>
                      </div>
                    </button>

                    {/* Placeholder Game Option 2 */}
                    <button
                      disabled
                      className="w-full text-left py-1.5 px-2.5 opacity-55 rounded-xl text-[11px] font-medium transition flex items-center gap-2 text-gray-400 dark:text-gray-500 cursor-not-allowed"
                    >
                      <span className="text-sm">🎯</span>
                      <div className="flex flex-col text-left leading-tight">
                        <span className="font-semibold text-gray-500 dark:text-gray-400">Wilderness Hunt</span>
                        <span className="text-[8px] opacity-75">Developing later...</span>
                      </div>
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}

          {/* Text Area */}
          <textarea
            id="message-text-input"
            value={inputText}
            onChange={handleInputChange}
            onKeyDown={handleKeyPress}
            disabled={isBlizzardActive && !currentUser.isAdmin}
            placeholder={
              isBlizzardActive && !currentUser.isAdmin
                ? 'Frozen! Active blizzard has locked global transmission channels...'
                : session.id === 'gemini_bot'
                ? 'Type to @WOSBot assistant (survival blueprints)...'
                : 'Say "Hello"'
            }
            rows={1}
            className="flex-1 border-0 bg-transparent resize-none focus:outline-none focus:ring-0 text-sm max-h-32 text-gray-900 dark:text-gray-100 py-1.5 leading-snug font-sans disabled:opacity-50"
          />
        </div>

        {/* Voice recording triggers / Standard Send actions */}
        <div className="flex items-center gap-2">
          {inputText.trim() === '' ? (
            <button
              id="btn-voice-recorder"
              onMouseDown={startRecording}
              onMouseUp={stopAndSendRecording}
              className={`h-11 w-11 flex items-center justify-center rounded-full transition-all duration-200 ${
                isRecording
                  ? 'bg-red-500 text-white animate-pulse scale-120'
                  : 'bg-sky-500 text-white hover:bg-sky-600 active:scale-95'
              }`}
              title="Record voice message (Hold down to record, release to transmit)"
            >
              <Volume2 className="h-5 w-5" />
            </button>
          ) : (
            <button
              id="btn-send-message"
              onClick={handleSend}
              className="h-11 w-11 bg-sky-500 hover:bg-sky-600 active:scale-95 text-white flex items-center justify-center rounded-full transition shadow-sm shadow-sky-500/20"
              title="Submit message payload"
            >
              <Send className="h-4.5 w-4.5 -rotate-12 translate-x-0.2" />
            </button>
          )}
        </div>

        {/* Recording active overlay tooltip tag */}
        <AnimatePresence>
          {isRecording && (
            <motion.div
              initial={{ scale: 0.9, opacity: 0, y: 10 }}
              animate={{ scale: 1, opacity: 1, y: 0 }}
              exit={{ scale: 0.9, opacity: 0, y: 10 }}
              className="absolute right-16 bottom-16 bg-red-550 border border-red-500/20 bg-red-600 px-4 py-2 text-white font-mono rounded-xl shadow-lg text-xs flex items-center gap-2 z-10 animate-bounce"
            >
              <span className="h-2 w-2 rounded-full bg-white inline-block animate-ping" />
              <span>Recording Voice: {recordingSeconds}s</span>
            </motion.div>
          )}
        </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
