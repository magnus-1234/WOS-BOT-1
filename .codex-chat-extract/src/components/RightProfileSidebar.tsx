import React, { useState } from 'react';
import { User } from '../types';
import { X, MessageSquare, ShieldAlert, Sparkles, User as UserIcon, UserMinus, UserPlus, VolumeX, Volume2 } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';

interface RightProfileSidebarProps {
  activeUsers: User[];
  currentUser: User;
  onStartPrivateChat: (userId: string) => void;
  selectedUserId?: string | null;
  onCloseProfile?: () => void;
  onSelectUser: (userId: string | null) => void;
  mutedUserIds?: string[];
  onToggleMute?: (userId: string) => void;
  friendUserIds?: string[];
  onToggleFriend?: (userId: string) => void;
}

export default function RightProfileSidebar({
  activeUsers,
  currentUser,
  onStartPrivateChat,
  selectedUserId,
  onCloseProfile,
  onSelectUser,
  mutedUserIds = [],
  onToggleMute,
  friendUserIds = [],
  onToggleFriend
}: RightProfileSidebarProps) {
  const [search, setSearch] = useState('');

  const displayedUser = selectedUserId 
    ? activeUsers.find(u => u.id === selectedUserId) 
    : null;

  return (
    <div className="h-full bg-white dark:bg-gray-950 flex flex-col overflow-hidden">
      <AnimatePresence mode="wait">
        {displayedUser ? (
          <motion.div
            key="profile-view"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            className="flex-1 flex flex-col"
          >
            <div className="flex-shrink-0 p-4 border-b border-gray-150 dark:border-gray-800 flex items-center justify-between">
              <h3 className="font-bold text-sm tracking-wide text-gray-800 dark:text-gray-200 uppercase">
                Identity Profile
              </h3>
              <button
                onClick={() => onSelectUser(null)}
                className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-850 rounded-full transition text-gray-500 cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            
            <div className="flex-1 overflow-y-auto p-6 flex flex-col items-center">
              <div className="relative">
                {displayedUser.avatar ? (
                  <img src={displayedUser.avatar} className="h-24 w-24 rounded-full object-cover border-4 border-white dark:border-gray-900 shadow-xl" alt={displayedUser.name} />
                ) : (
                  <div className="h-24 w-24 rounded-full bg-gradient-to-tr from-cyan-500 to-blue-500 flex items-center justify-center text-white text-3xl font-bold shadow-xl border-4 border-white dark:border-gray-900">
                    {displayedUser.name[0]?.toUpperCase()}
                  </div>
                )}
                {displayedUser.status === 'online' && (
                  <div className="absolute bottom-1 right-1 h-5 w-5 rounded-full bg-green-500 border-2 border-white dark:border-gray-950" />
                )}
              </div>
              
              <h2 className="mt-4 font-bold text-xl font-display text-gray-900 dark:text-white flex items-center gap-2">
                {displayedUser.name}
                {displayedUser.isAdmin && <ShieldAlert className="h-4 w-4 text-rose-500" />}
              </h2>
              <div className="text-[10px] uppercase font-mono tracking-widest text-cyan-500 bg-cyan-50 dark:bg-cyan-900/10 px-2 py-0.5 rounded-full mt-2 font-bold mb-6">
                ID: {displayedUser.id.substring(0, 8)}...
              </div>

              {/* Action Buttons */}
              <div className="w-full space-y-2.5">
                {displayedUser.id !== currentUser.id && (
                  <>
                    <button
                      onClick={() => onStartPrivateChat(displayedUser.id)}
                      className="w-full py-3 bg-cyan-600 hover:bg-cyan-500 text-white rounded-xl font-bold text-sm shadow-lg shadow-cyan-500/20 active:scale-95 transition flex items-center justify-center gap-2 cursor-pointer"
                    >
                      <MessageSquare className="h-4 w-4" />
                      Private Chat
                    </button>
                    <div className="grid grid-cols-2 gap-2 mt-2">
                      <button
                        onClick={() => onToggleFriend?.(displayedUser.id)}
                        className={`w-full py-2.5 rounded-xl font-bold text-xs active:scale-95 transition flex items-center justify-center gap-2 cursor-pointer border ${
                          friendUserIds.includes(displayedUser.id)
                            ? 'bg-rose-50 border-rose-200 text-rose-600 hover:bg-rose-100 dark:bg-rose-500/10 dark:border-rose-500/20 dark:text-rose-400 dark:hover:bg-rose-500/20'
                            : 'bg-white border-gray-200 text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-750'
                        }`}
                      >
                        {friendUserIds.includes(displayedUser.id) ? (
                          <><UserMinus className="h-4 w-4" /> Unfriend</>
                        ) : (
                          <><UserPlus className="h-4 w-4" /> Add Friend</>
                        )}
                      </button>
                      <button
                        onClick={() => onToggleMute?.(displayedUser.id)}
                        className={`w-full py-2.5 rounded-xl font-bold text-xs active:scale-95 transition flex items-center justify-center gap-2 cursor-pointer border ${
                          mutedUserIds.includes(displayedUser.id)
                            ? 'bg-emerald-50 border-emerald-200 text-emerald-600 hover:bg-emerald-100 dark:bg-emerald-500/10 dark:border-emerald-500/20 dark:text-emerald-400 dark:hover:bg-emerald-500/20'
                            : 'bg-white border-gray-200 text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-750'
                        }`}
                      >
                        {mutedUserIds.includes(displayedUser.id) ? (
                          <><Volume2 className="h-4 w-4" /> Unmute</>
                        ) : (
                          <><VolumeX className="h-4 w-4" /> Mute User</>
                        )}
                      </button>
                    </div>
                  </>
                )}
                
                {displayedUser.isDiscord && (
                  <div className="w-full flex items-center justify-center p-3 rounded-xl border border-[#5865F2]/20 bg-[#5865F2]/5 mt-2">
                    <span className="text-xs font-semibold text-[#5865F2] flex items-center gap-2">
                      <svg className="h-4 w-4 fill-current pt-0.5" viewBox="0 0 127.14 96.36">
                        <path d="M107.7,8.07A105.15,105.15,0,0,0,77.26,0a77.19,77.19,0,0,0-3.3,6.83A96.67,96.67,0,0,0,53.22,6.83,77.19,77.19,0,0,0,49.88,0,105.15,105.15,0,0,0,19.44,8.07C3.66,31.58-1.86,54.65,1,77.53A105.73,105.73,0,0,0,32,96.36a77.7,77.7,0,0,0,6.63-10.85,68.43,68.43,0,0,1-10.43-5c.87-.64,1.72-1.31,2.53-2a75.37,75.37,0,0,0,72.9,0c.81,.69,1.66,1.36,2.53,2a68.43,68.43,0,0,1-10.43,5,77.7,77.7,0,0,0,6.63,10.85,105.73,105.73,0,0,0,31-18.83C129.87,48.42,123.37,25.68,107.7,8.07ZM42.45,65.69C36.18,65.69,31,60,31,53S36.18,40.36,42.45,40.36,54,46,53.92,53,48.81,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.24,60,73.24,53S78.41,40.36,84.69,40.36,96.22,46,96.14,53,91,65.69,84.69,65.69Z" />
                      </svg>
                      Verified Discord Origin
                    </span>
                  </div>
                )}
              </div>
              
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="list-view"
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 20 }}
            className="flex-1 flex flex-col"
          >
            <div className="flex-shrink-0 p-4 border-b border-gray-150 dark:border-gray-800">
              <h3 className="font-bold text-sm tracking-wide text-gray-800 dark:text-gray-200 uppercase flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <span>Members</span>
                  <span className="text-[10px] bg-emerald-500/10 text-emerald-500 px-2 py-0.5 rounded-full">{activeUsers.length} ONLINE</span>
                </div>
                {onCloseProfile && (
                  <button
                    onClick={onCloseProfile}
                    className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-850 rounded-full transition text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 cursor-pointer"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </h3>
              <div className="mt-4 relative">
                <input
                  type="text"
                  placeholder="Search network..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full pl-9 pr-4 py-2 border border-gray-200 dark:border-gray-800 rounded-xl bg-gray-50 dark:bg-gray-900 text-xs focus:ring-2 focus:ring-cyan-500/30 font-medium"
                />
                <UserIcon className="absolute left-3.5 top-2 h-4 w-4 text-gray-400" />
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-2">
              <ul className="space-y-1">
                {activeUsers
                 .filter(u => u.name.toLowerCase().includes(search.toLowerCase()))
                 .map((u) => {
                   const isMe = u.id === currentUser.id;
                   const isFriend = friendUserIds.includes(u.id);
                   const isBot = u.isBot;

                   return (
                  <li key={u.id} className="group relative">
                    <button
                      onClick={() => onSelectUser(u.id)}
                      className="w-full flex items-center gap-3 p-3 hover:bg-gray-50 dark:hover:bg-gray-850/60 rounded-xl transition cursor-pointer text-left"
                    >
                      <div className="relative">
                         {u.avatar ? (
                          <img src={u.avatar} className="h-10 w-10 rounded-full object-cover border border-gray-100 dark:border-gray-800 shrink-0" alt="" />
                         ) : (
                           <div className="h-10 w-10 flex border border-gray-100 dark:border-gray-800 items-center justify-center bg-cyan-600 rounded-full text-white text-xs font-bold">
                             {u.name[0]?.toUpperCase()}
                           </div>
                         )}
                         <span className={`absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border border-white dark:border-gray-950 ${u.status === 'online' ? 'bg-green-500' : 'bg-gray-400'}`} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 font-bold text-sm text-gray-900 dark:text-gray-100 truncate">
                          {u.name}
                          {u.isAdmin && <ShieldAlert className="h-3 w-3 text-rose-500 inline-block" />}
                          {u.id === currentUser.id && <span className="text-[9px] bg-cyan-500/10 text-cyan-600 px-1 py-0.5 rounded font-mono ml-auto mr-1">YOU</span>}
                        </div>
                        <div className="truncate text-[10px] text-gray-400 font-medium">
                           {u.isDiscord ? 'Discord Integration' : (u.isBot ? 'System Bot' : 'Guest Account')}
                        </div>
                      </div>
                    </button>
                    {!isMe && !isBot && (
                      <div className="absolute right-3 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1.5 focus-within:opacity-100 bg-white/60 dark:bg-gray-950/60 p-1 rounded-lg backdrop-blur-md mx-2">
                        {!isFriend && (
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); onToggleFriend?.(u.id); }}
                            className="p-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-200 rounded-md cursor-pointer text-xs shadow-sm hover:scale-105 transition"
                            title="Add Friend"
                          >
                            <UserPlus className="h-3.5 w-3.5" />
                          </button>
                        )}
                        <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); onStartPrivateChat(u.id); }}
                            className="p-1.5 bg-cyan-100 hover:bg-cyan-200 text-cyan-700 dark:bg-cyan-500/20 dark:hover:bg-cyan-500/30 dark:text-cyan-400 rounded-md cursor-pointer text-xs shadow-sm hover:scale-105 transition"
                            title="Private Chat"
                          >
                            <MessageSquare className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )}
                  </li>
                )})}
              </ul>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
