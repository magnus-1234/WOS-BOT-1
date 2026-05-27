import { useState, useMemo } from 'react';
import { Search, Globe, Bot, User as UserIcon, MessageSquare, LogOut, Settings2, Sparkles, Shield, AlertTriangle, Trash, VolumeX } from 'lucide-react';
import { User, ChatSession, Message } from '../types';

interface SidebarProps {
  currentUser: User & { isAdmin?: boolean };
  activeSessions: ChatSession[];
  activeUsers: User[];
  currentSessionId: string;
  onSelectSession: (id: string) => void;
  onResetProfile: () => void;
  isBlizzardActive: boolean;
  onToggleBlizzard: (isFrozen: boolean) => void;
  onClearLogs: () => void;
  onBroadcastAnnouncement: (text: string) => void;
  onClaimAdmin: (code: string) => boolean;
}

type TabType = 'global' | 'all' | 'group' | 'private';

export default function Sidebar({
  currentUser,
  activeSessions,
  activeUsers,
  currentSessionId,
  onSelectSession,
  onResetProfile,
  isBlizzardActive,
  onToggleBlizzard,
  onClearLogs,
  onBroadcastAnnouncement,
  onClaimAdmin,
}: SidebarProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState<TabType>('all');

  // Internal states for claimed admin powers input
  const [showClaimForm, setShowClaimForm] = useState(false);
  const [secretCode, setSecretCode] = useState('');
  const [claimError, setClaimError] = useState(false);
  const [announcementText, setAnnouncementText] = useState('');

  // Filter list based on selected Tab and text Search Query
  const filteredSessions = useMemo(() => {
    const list = activeSessions.filter((session) => {
      // 1. Tab fit
      if (activeTab === 'global' && session.id !== 'global') return false;
      if (activeTab === 'group' && (!session.isGroup || session.id === 'global')) return false;
      if (activeTab === 'private' && (session.isGroup || session.isBot)) return false;

      // 2. Search query fit
      if (searchQuery.trim() === '') return true;
      return (
        session.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (session.lastMessage?.text || '').toLowerCase().includes(searchQuery.toLowerCase())
      );
    });

    // Make sure Global Chat category is ALWAYS placed first, followed by remaining lists
    return [...list].sort((a, b) => {
      if (a.id === 'global') return -1;
      if (b.id === 'global') return 1;
      return 0;
    });
  }, [activeSessions, activeTab, searchQuery]);

  // Format message previews
  const renderLastMessagePreview = (msg?: Message) => {
    if (!msg) return <span className="text-gray-400 italic">No messages yet</span>;
    
    const senderStr = msg.senderId === currentUser.id ? 'You: ' : `${msg.senderName}: `;
    
    if (msg.type === 'file') {
      return (
        <span className="flex items-center gap-1 text-sky-400 text-xs truncate">
          <span>{senderStr}</span>
          <span>📎 File: {msg.fileInfo?.name}</span>
        </span>
      );
    }
    if (msg.type === 'audio') {
      return (
        <span className="flex items-center gap-1 text-sky-400 text-xs truncate">
          <span>{senderStr}</span>
          <span>🎤 Voice message</span>
        </span>
      );
    }
    if (msg.type === 'call') {
      return (
        <span className="text-amber-400 text-xs truncate">
          📞 Call Ended {msg.callInfo?.duration ? `(${Math.floor(msg.callInfo.duration)}s)` : ''}
        </span>
      );
    }
    return <span className="text-gray-400 text-xs truncate">{senderStr}{msg.text}</span>;
  };

  // Format timestamp nicely
  const formatTime = (isoString?: string) => {
    if (!isoString) return '';
    const date = new Date(isoString);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className="flex h-full w-full flex-col bg-white border-r border-gray-200 dark:bg-gray-900 dark:border-gray-800">
      
      {/* Top Main Owner Profile Header */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between bg-gray-50/55 dark:bg-gray-900/50">
        <div className="flex items-center gap-3">
          {currentUser.avatar ? (
            <img
              src={currentUser.avatar}
              alt={currentUser.name}
              className="h-10 w-10 rounded-full object-cover border border-sky-400/20 ring-2 ring-sky-400/30"
              referrerPolicy="no-referrer"
            />
          ) : (
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-tr from-sky-400 to-indigo-500 text-white font-semibold">
              {currentUser.name[0]?.toUpperCase()}
            </div>
          )}
          <div className="flex flex-col">
            <span className="font-semibold text-sm text-gray-900 dark:text-gray-100 flex items-center gap-1.5">
              {currentUser.name}
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
            <span className="text-[10px] text-gray-500 font-mono">You (Online)</span>
          </div>
        </div>
        
        {/* Profile/System Controls */}
        <div className="flex items-center gap-1 text-gray-500 dark:text-gray-400">
          <button
            id="btn-edit-profile"
            onClick={onResetProfile}
            className="p-2 hover:bg-gray-100 dark:hover:bg-gray-850 rounded-full transition"
            title="Edit Profile Nickname"
          >
            <Settings2 className="h-4.5 w-4.5 text-gray-600 dark:text-gray-300" />
          </button>
        </div>
      </div>

      {/* Leadership command or Claim deck center */}
      {currentUser.isAdmin ? (
        <div className="mx-4 mt-3 bg-gradient-to-br from-indigo-950 to-slate-900 border border-indigo-500/30 rounded-2xl p-4 text-white hover:border-indigo-500/50 shadow-lg relative overflow-hidden transition-all duration-300">
          <div className="flex items-center justify-between pb-2 border-b border-indigo-500/20 mb-3">
            <span className="text-xs font-bold tracking-wider uppercase text-indigo-300 flex items-center gap-1.5 font-mono select-none">
              👑 COMMAND DECK
            </span>
            <span className="text-[9px] bg-red-500 px-2 py-0.5 rounded-full font-bold select-none uppercase animate-pulse">
              Active
            </span>
          </div>
          
          <div className="space-y-3 text-xs text-slate-300">
            <div className="flex items-center justify-between">
              <span className="font-semibold flex items-center gap-1">
                ❄️ Blizzard Freeze Action
              </span>
              <button
                id="switch-blizzard-toggle"
                onClick={() => onToggleBlizzard(!isBlizzardActive)}
                className={`w-11 h-6 rounded-full transition-colors relative flex items-center px-1 cursor-pointer ${
                  isBlizzardActive ? "bg-cyan-500" : "bg-slate-700"
                }`}
              >
                <span className={`h-4 w-4 rounded-full bg-white shadow transition-transform ${
                  isBlizzardActive ? "translate-x-5" : "translate-x-0"
                }`} />
              </button>
            </div>

            <div className="flex flex-col gap-1">
              <span className="text-[9px] uppercase font-mono text-indigo-200">Alert Broadcast banner</span>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Order content..."
                  value={announcementText}
                  onChange={(e) => setAnnouncementText(e.target.value)}
                  className="flex-1 min-w-0 px-2.5 py-1 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && announcementText.trim()) {
                      onBroadcastAnnouncement(announcementText.trim());
                      setAnnouncementText("");
                    }
                  }}
                />
                <button
                  onClick={() => {
                    if (announcementText.trim()) {
                      onBroadcastAnnouncement(announcementText.trim());
                      setAnnouncementText("");
                    }
                  }}
                  className="px-2 bg-indigo-500 hover:bg-indigo-600 rounded-lg text-white font-bold text-[10px] uppercase"
                >
                  Send
                </button>
              </div>
            </div>

            <button
              onClick={() => {
                if (confirm("Are you sure you want to permanently wipe the global survival log terminals?")) {
                  onClearLogs();
                }
              }}
              className="w-full py-1.5 bg-red-600/20 hover:bg-red-600/40 transition border border-red-500/30 rounded-lg text-[9px] font-bold uppercase tracking-wider text-red-300 flex items-center justify-center gap-1 cursor-pointer"
            >
              <Trash className="h-3 w-3" /> Purge Global Logs
            </button>
          </div>
        </div>
      ) : (
        <div className="mx-4 mt-3">
          {!showClaimForm ? (
            <button
              id="btn-claim-deck-trigger"
              onClick={() => setShowClaimForm(true)}
              className="w-full py-2 bg-slate-50 hover:bg-slate-100 dark:bg-gray-850 dark:hover:bg-gray-800 transition border border-gray-150 dark:border-gray-800 rounded-xl text-center text-xs font-semibold text-gray-500 dark:text-gray-400 flex items-center justify-center gap-1.5 cursor-pointer"
            >
              <Shield className="h-3.5 w-3.5 text-indigo-400" /> Enter Administrator Code
            </button>
          ) : (
            <div className="p-3 bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-xl space-y-2">
              <span className="block text-[9px] font-bold uppercase tracking-wider text-gray-400">Unlock Command Center</span>
              <div className="flex gap-1.5">
                <input
                  id="claim-admin-pass-input"
                  type="password"
                  placeholder="Passcode..."
                  value={secretCode}
                  onChange={(e) => {
                    setSecretCode(e.target.value);
                    setClaimError(false);
                  }}
                  className="flex-1 min-w-0 px-2.5 py-1 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 text-xs rounded-lg text-gray-900 dark:text-white"
                />
                <button
                  id="btn-submit-claim-admin"
                  onClick={() => {
                    const ok = onClaimAdmin(secretCode);
                    if (ok) {
                      setShowClaimForm(false);
                      setSecretCode("");
                    } else {
                      setClaimError(true);
                    }
                  }}
                  className="px-2.5 bg-indigo-500 hover:bg-indigo-600 rounded-lg text-white font-bold text-xs"
                >
                  Auth
                </button>
              </div>
              {claimError && (
                <span className="block text-[8px] text-red-500 font-bold">❌ Invalid authentication calibrator</span>
              )}
              <button
                onClick={() => {
                  setShowClaimForm(false);
                  setSecretCode("");
                  setClaimError(false);
                }}
                className="text-[9px] text-gray-400 dark:text-gray-500 underline"
              >
                Dismiss
              </button>
            </div>
          )}
        </div>
      )}

      {/* Modern Search Field */}
      <div className="px-4 py-3">
        <div className="relative flex items-center">
          <Search className="absolute left-3 h-4 w-4 text-gray-400 pointer-events-none" />
          <input
            id="sidebar-search-conversations"
            type="text"
            placeholder="Search chats, messages..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-4 py-2 border border-gray-200 dark:border-gray-800 rounded-lg bg-gray-50 dark:bg-gray-950 text-sm placeholder-gray-400 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500"
          />
        </div>
      </div>

      {/* Scrollable Conversation List Area */}
      <div className="flex-1 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-800/50">
        
        {filteredSessions.length > 0 ? (
          filteredSessions.map((session) => {
            const isSelected = currentSessionId === session.id;
            return (
              <div
                key={session.id}
                id={`chat-session-${session.id}`}
                onClick={() => onSelectSession(session.id)}
                className={`flex items-center justify-between p-4 cursor-pointer transition-all duration-200 group relative overflow-hidden ${
                  isSelected
                    ? 'bg-cyan-500/10 border-l-4 border-cyan-500 shadow-sm'
                    : 'hover:bg-gray-50/50 dark:hover:bg-gray-800/40 border-l-4 border-transparent'
                }`}
              >
                <div className="flex items-center gap-3 overflow-hidden min-w-0">
                  
                  {/* Status Avatars */}
                  <div className="relative flex-shrink-0">
                    {session.avatar ? (
                      <img
                        src={session.avatar}
                        alt={session.name}
                        className="h-11 w-11 rounded-full object-cover border border-gray-100 dark:border-gray-800"
                        referrerPolicy="no-referrer"
                      />
                    ) : session.isGroup ? (
                      <div className="flex h-11 w-11 items-center justify-center rounded-full bg-indigo-500 text-white font-semibold shadow-inner">
                        <Globe className="h-5 w-5" />
                      </div>
                    ) : (
                      <div className="flex h-11 w-11 items-center justify-center rounded-full bg-sky-500 text-white font-semibold shadow-inner">
                        <UserIcon className="h-5 w-5" />
                      </div>
                    )}
                    
                    {/* Bot/Online Visual Indicators */}
                    {session.isBot ? (
                      <span className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-indigo-600 text-[9px] text-white font-bold border border-white dark:border-gray-900">
                        🤖
                      </span>
                    ) : !session.isGroup ? (
                      <span
                        className={`absolute -bottom-[2px] -right-[2px] h-3.5 w-3.5 rounded-full border-[2.5px] border-white dark:border-gray-900 ${
                          activeUsers.some((u) => u.id === session.id && u.status === 'online')
                            ? 'bg-emerald-500'
                            : 'bg-gray-300 dark:bg-gray-700'
                        }`}
                      />
                    ) : null}
                  </div>

                  {/* Body textual content */}
                  <div className="flex flex-col min-w-0">
                    <span className="font-semibold text-sm text-gray-900 dark:text-gray-100 truncate flex items-center gap-1 pb-0.5">
                      {session.name}
                      {session.isBot && (
                        <span className="inline-flex items-center gap-0.5 px-1 py-0.2 rounded bg-indigo-500/20 text-indigo-600 dark:text-sky-300 text-[10px] uppercase font-bold tracking-wider font-mono scale-90">
                          <Sparkles className="h-2 w-2" /> BOT
                        </span>
                      )}
                    </span>
                    <span className="text-xs truncate text-gray-500">
                      {renderLastMessagePreview(session.lastMessage)}
                    </span>
                  </div>
                </div>

                {/* Right metadata panel (time, unreads) */}
                <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
                  <span className="text-[10px] text-gray-400 font-medium">
                    {formatTime(session.lastMessage?.timestamp)}
                  </span>
                  {session.unreadCount > 0 ? (
                    <span className="flex h-5 min-w-[20px] items-center justify-center rounded-full bg-sky-500 px-1 text-[11px] font-bold text-white shadow-sm ring-1 ring-white/10">
                      {session.unreadCount}
                    </span>
                  ) : null}
                </div>
              </div>
            );
          })
        ) : (
          <div className="flex flex-col items-center justify-center py-10 px-4 text-center">
            <MessageSquare className="h-10 w-10 text-gray-300 dark:text-gray-700 animate-pulse" />
            <p className="mt-3 text-sm text-gray-400 dark:text-gray-500">No active conversations found</p>
          </div>
        )}
      </div>

      {/* Bottom Categories / Navigation Tabs */}
      <div className="px-2 py-2 flex items-center justify-between gap-1 border-t border-gray-150 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 text-xs font-bold overflow-x-auto scrollbar-none shrink-0 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)]">
        {(['global', 'all', 'group', 'private'] as TabType[]).map((tab) => (
          <button
            key={tab}
            id={`tab-filter-${tab}`}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 py-2 px-1.5 rounded-xl transition-all duration-200 capitalize whitespace-nowrap text-center cursor-pointer select-none tracking-wide ${
              activeTab === tab
                ? 'bg-cyan-500 text-white shadow-lg shadow-cyan-500/20 active:scale-95'
                : 'text-gray-500 dark:text-gray-400 hover:bg-gray-200/50 dark:hover:bg-gray-800/80 active:bg-gray-300 dark:active:bg-gray-700'
            }`}
          >
            {tab === 'global' && 'GLOBAL'}
            {tab === 'all' && 'ALL'}
            {tab === 'group' && 'GROUPS'}
            {tab === 'private' && 'PRIVATE'}
          </button>
        ))}
      </div>

      {/* Online Network Stats footer panel */}
      <div className="p-3 bg-gray-50 dark:bg-gray-950 border-t border-gray-150 dark:border-gray-800 text-[11px] text-gray-400 dark:text-gray-500 font-mono tracking-tight flex items-center justify-between shrink-0">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-emerald-400 inline-block animate-pulse" />
          <span>Online Members: {activeUsers.length}</span>
        </span>
        <span>Secure HTTP+WS</span>
      </div>
    </div>
  );
}
