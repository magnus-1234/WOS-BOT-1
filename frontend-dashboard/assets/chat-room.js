(function () {
  const roomRoot = document.querySelector("[data-chat-room]");
  if (!roomRoot) return;

  const el = {
    online: roomRoot.querySelector("[data-room-online]"),
    login: roomRoot.querySelector("[data-room-login]"),
    name: roomRoot.querySelector("[data-room-name]"),
    guest: roomRoot.querySelector("[data-room-guest]"),
    discord: roomRoot.querySelector("[data-room-discord]"),
    identity: roomRoot.querySelector("[data-room-identity]"),
    status: roomRoot.querySelector("[data-room-status]"),
    loading: roomRoot.querySelector("[data-room-loading]"),
    refresh: roomRoot.querySelector("[data-room-refresh]"),
    messages: roomRoot.querySelector("[data-room-messages]"),
    form: roomRoot.querySelector("[data-room-form]"),
    input: roomRoot.querySelector("[data-room-input]"),
    file: roomRoot.querySelector("[data-room-file]"),
    attachments: roomRoot.querySelector("[data-room-attachments]"),
    emoji: roomRoot.querySelector("[data-room-emoji]"),
    emojiPanel: roomRoot.querySelector("[data-room-emoji-panel]"),
    tenor: roomRoot.querySelector("[data-room-tenor]"),
    tenorPanel: roomRoot.querySelector("[data-room-tenor-panel]"),
    tenorSearch: roomRoot.querySelector("[data-room-tenor-search]"),
    tenorGo: roomRoot.querySelector("[data-room-tenor-go]"),
    tenorResults: roomRoot.querySelector("[data-room-tenor-results]"),
    voice: roomRoot.querySelector("[data-room-voice]"),
    reply: roomRoot.querySelector("[data-room-reply]"),
    replyName: roomRoot.querySelector("[data-room-reply-name]"),
    replyContent: roomRoot.querySelector("[data-room-reply-content]"),
    replyClear: roomRoot.querySelector("[data-room-reply-clear]"),
    title: roomRoot.querySelector("[data-room-title]"),
    subtitle: roomRoot.querySelector("[data-room-subtitle]"),
    activeAvatar: roomRoot.querySelector("[data-room-active-avatar]"),
    globalSession: roomRoot.querySelector("[data-room-global-session]"),
    sessionList: roomRoot.querySelector("[data-room-session-list]"),
    sessionCount: roomRoot.querySelector("[data-room-session-count]"),
    whisperClear: roomRoot.querySelector("[data-room-whisper-clear]"),
    memberList: roomRoot.querySelector("[data-room-member-list]"),
    memberSearch: roomRoot.querySelector("[data-room-member-search]"),
    memberCount: roomRoot.querySelector("[data-room-member-count]"),
    profile: roomRoot.querySelector("[data-room-profile]"),
    profileClose: roomRoot.querySelector("[data-room-profile-close]"),
    profileAvatar: roomRoot.querySelector("[data-room-profile-avatar]"),
    profileName: roomRoot.querySelector("[data-room-profile-name]"),
    profileKind: roomRoot.querySelector("[data-room-profile-kind]"),
    profileWhisper: roomRoot.querySelector("[data-room-profile-whisper]"),
    profileCopy: roomRoot.querySelector("[data-room-profile-copy]")
  };

  const DISCORD_CLIENT_ID = "1399025185046134866";
  const STORAGE_KEYS = {
    guestId: "wos_global_chat_guest_id",
    guestName: "wos_global_chat_guest_name",
    guestAvatar: "wos_global_chat_guest_avatar",
    lastSeen: "wos_global_chat_last_seen_at"
  };
  
  const emojiCategories = {
    "Smileys": ["😀","😂","🤣","😊","🥰","😍","😎","🤔","🙄","😴","😷","🤢","🥵","🥶"],
    "Gestures": ["👍","👎","👌","✌️","🤞","🤝","🙏","💪","👋","👏","🙌","🫶"],
    "Objects": ["🎁","🏆","💎","📱","💻","💣","💡","🔥","💧","❄️","💥","✨"],
    "Symbols": ["✅","❌","❓","❗","💯","💢","💬","❤️","💔","💕","☢️","☣️"]
  };
  const quickReactions = ["👍", "❤️", "😂", "🔥", "❄️", "🎁"];
  
  let currentUser = null;
  let pendingAttachments = [];
  let messagesCache = [];
  let replyTo = null;
  let replyToUser = null; // target_user_id for private messages
  let activeGifSource = 'tenor'; // tenor or giphy
  let pollTimer = null;
  let presenceTimer = null;
  let mediaRecorder = null;
  let voiceChunks = [];
  let ws = null;
  let wsReconnectTimer = null;
  let wsConnected = false;
  let soundEnabled = true;
  let searchQuery = "";
  let onlineUsers = [];
  let typingTimer = null;
  let selectedProfileUser = null;
  let memberSearchQuery = "";

  const getToken = () => localStorage.getItem("discord_access_token");
  const authHeaders = () => {
    const token = getToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  };
  const getGuestId = () => {
    let guestId = localStorage.getItem(STORAGE_KEYS.guestId);
    if (!guestId) {
      guestId = `guest-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      localStorage.setItem(STORAGE_KEYS.guestId, guestId);
    }
    return guestId;
  };
  const getGuestName = () => localStorage.getItem(STORAGE_KEYS.guestName) || "";
  const getGuestAvatar = () => localStorage.getItem(STORAGE_KEYS.guestAvatar) || null;
  const setGuestName = (name) => {
    const cleaned = (name || "").trim().slice(0, 32);
    if (cleaned) localStorage.setItem(STORAGE_KEYS.guestName, cleaned);
    return cleaned;
  };
  const setGuestAvatar = (url) => {
    if (url) localStorage.setItem(STORAGE_KEYS.guestAvatar, url);
  };
  const setStatus = (message, isError = false) => {
    el.status.textContent = message;
    el.status.style.color = isError ? "#ffb4a8" : "#71f7a8";
  };
  const buildDiscordAuthUrl = () => {
    const base = "https://discord.com/api/oauth2/authorize";
    const redirect = `${window.location.origin}/oauth-callback.html`;
    const scope = encodeURIComponent("identify guilds");
    return `${base}?client_id=${DISCORD_CLIENT_ID}&redirect_uri=${encodeURIComponent(redirect)}&response_type=code&scope=${scope}&prompt=consent`;
  };
  const initials = (name) => {
    const parts = String(name || "Guest").trim().split(/\s+/).slice(0, 2);
    return parts.map((part) => part[0] || "").join("").toUpperCase() || "G";
  };
  const getMyId = () => currentUser ? String(currentUser.id) : getGuestId();
  const normalizeUserId = (value) => String(value || "").trim();
  const userDisplayName = (user) => user ? (user.name || user.global_name || user.username || "Guest Player") : "Guest Player";
  const getUserById = (userId) => onlineUsers.find((user) => normalizeUserId(user.id || user.name) === normalizeUserId(userId));
  const isCurrentUser = (user) => normalizeUserId(user && (user.id || user.name)) === normalizeUserId(getMyId());
  const otherPartyId = (message) => {
    const targetId = normalizeUserId(message.target_user_id);
    if (!targetId) return "";
    const authorId = normalizeUserId((message.author || {}).id || (message.author || {}).name);
    return targetId === normalizeUserId(getMyId()) ? authorId : targetId;
  };
  const formatTime = (iso) => {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "now";
    const local = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const utc = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" });
    return `${local} local / ${utc} UTC`;
  };
  const isImageAttachment = (attachment) => {
    const type = attachment.type || "";
    return type.startsWith("image/") || /\.(png|jpe?g|gif|webp|apng)$/i.test(attachment.url || "");
  };
  const isAudioAttachment = (attachment) => {
    const type = attachment.type || "";
    return type.startsWith("audio/") || /\.(webm|ogg|mp3|wav|m4a|aac)$/i.test(attachment.url || "");
  };

  const updateIdentityView = () => {
    const guestName = getGuestName();
    if (currentUser) {
      el.identity.innerHTML = `Discord: ${currentUser.global_name || currentUser.username} <button type="button" data-edit-profile style="background:none;border:none;cursor:pointer;color:var(--primary);margin-left:8px;font-size:0.8em;" title="Edit Profile">✏️</button>`;
      el.login.classList.add("is-hidden");
    } else if (guestName) {
      el.identity.innerHTML = `Guest: ${guestName} <button type="button" data-edit-profile style="background:none;border:none;cursor:pointer;color:var(--primary);margin-left:8px;font-size:0.8em;" title="Edit Profile">✏️</button>`;
      el.login.classList.add("is-hidden");
    } else {
      el.identity.textContent = "Guest access";
      el.login.classList.remove("is-hidden");
    }
    
    const editBtn = roomRoot.querySelector("[data-edit-profile]");
    if (editBtn) editBtn.addEventListener("click", openProfileModal);
  };

  const resolveDiscordIdentity = async () => {
    if (!getToken()) {
      updateIdentityView();
      initWebSocket();
      return;
    }
    try {
      const response = await fetch("/api/auth/me", { headers: authHeaders() });
      if (!response.ok) throw new Error("Discord login expired");
      currentUser = await response.json();
    } catch (error) {
      currentUser = null;
    } finally {
      updateIdentityView();
      initWebSocket();
    }
  };

  // ── Profile Modal ────────────────────────────────────────────────────────
  const openProfileModal = () => {
    let modal = document.getElementById('chat-profile-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'chat-profile-modal';
      modal.className = 'chat-profile-modal';
      modal.innerHTML = `
        <div class="chat-profile-content">
          <h2>Edit Profile</h2>
          <img class="chat-profile-avatar-preview" src="${currentUser ? currentUser.avatar_url : (getGuestAvatar() || 'https://cdn.discordapp.com/embed/avatars/0.png')}" alt="Avatar">
          <input type="file" id="profile-avatar-upload" accept="image/*" style="display:none;">
          <button type="button" class="btn secondary" onclick="document.getElementById('profile-avatar-upload').click()">Upload New Picture</button>
          <input type="text" id="profile-name-input" class="chat-profile-input" placeholder="Display Name" value="${currentUser ? (currentUser.global_name || currentUser.username) : getGuestName()}" maxlength="32">
          <div style="display:flex;gap:8px;margin-top:8px;">
            <button type="button" class="btn secondary" style="flex:1;" id="profile-cancel">Cancel</button>
            <button type="button" class="btn" style="flex:1;" id="profile-save">Save</button>
          </div>
        </div>
      `;
      document.body.appendChild(modal);

      document.getElementById('profile-cancel').addEventListener('click', () => modal.classList.remove('open'));
      document.getElementById('profile-save').addEventListener('click', () => {
        const newName = document.getElementById('profile-name-input').value.trim();
        if (newName) setGuestName(newName);
        
        if (wsConnected && ws) {
           const userInfo = currentUser
            ? { id: currentUser.id, name: newName, avatar_url: currentUser.avatar_url, kind: "discord" }
            : { id: getGuestId(), name: newName, avatar_url: getGuestAvatar(), kind: "guest" };
           ws.send(JSON.stringify({ type: "register", user: userInfo }));
        }
        updateIdentityView();
        modal.classList.remove('open');
      });

      document.getElementById('profile-avatar-upload').addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        try {
          const btn = document.querySelector('#profile-avatar-upload').nextElementSibling;
          btn.textContent = "Uploading...";
          const formData = new FormData();
          formData.append("file", file, file.name);
          const response = await fetch("/api/chat/upload", { method: "POST", headers: authHeaders(), body: formData });
          if (!response.ok) throw new Error("Upload failed");
          const { attachment } = await response.json();
          document.querySelector('.chat-profile-avatar-preview').src = attachment.url;
          setGuestAvatar(attachment.url);
          btn.textContent = "Upload New Picture";
        } catch (error) {
          alert("Failed to upload avatar");
          document.querySelector('#profile-avatar-upload').nextElementSibling.textContent = "Upload New Picture";
        }
      });
    }
    
    // Update fields before opening
    document.querySelector('.chat-profile-avatar-preview').src = currentUser ? (currentUser.avatar_url || 'https://cdn.discordapp.com/embed/avatars/0.png') : (getGuestAvatar() || 'https://cdn.discordapp.com/embed/avatars/0.png');
    document.getElementById('profile-name-input').value = currentUser ? (currentUser.global_name || currentUser.username) : getGuestName();
    
    setTimeout(() => modal.classList.add('open'), 10);
  };

  // ── Web Audio API chime synthesizer ─────────────────────────────────────
  const playChime = (type = "message") => {
    if (!soundEnabled) return;
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const gain = ctx.createGain();
      gain.connect(ctx.destination);
      gain.gain.setValueAtTime(0.18, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.6);

      const freqs = type === "connect" ? [523.25, 659.25] : [659.25, 783.99];
      freqs.forEach((freq, i) => {
        const osc = ctx.createOscillator();
        osc.type = i === 0 ? "sine" : "triangle";
        osc.frequency.setValueAtTime(freq, ctx.currentTime + i * 0.1);
        osc.connect(gain);
        osc.start(ctx.currentTime + i * 0.1);
        osc.stop(ctx.currentTime + 0.6);
      });
    } catch (e) {
      // Audio not supported
    }
  };

  // ── Native WebSocket engine ──────────────────────────────────────────────
  const initWebSocket = () => {
    addSoundToggleButton();
    addSearchInput();
    addTypingContainer();
    startFallbackPolling();   // also polls on first load
    connectWebSocket();
  };

  const connectWebSocket = () => {
    if (ws) {
      try { ws.close(); } catch (e) {}
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${proto}://${location.host}/api/chat/ws`;
    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      console.warn("WebSocket construction failed:", e);
      return;
    }

    ws.addEventListener("open", () => {
      wsConnected = true;
      setStatus("🟢 Live global room");
      playChime("connect");
      // Register the current user with the server
      const userInfo = currentUser
        ? { id: currentUser.id, name: currentUser.global_name || currentUser.username, avatar_url: currentUser.avatar_url, kind: "discord" }
        : { id: getGuestId(), name: getGuestName() || "Guest Player", avatar_url: getGuestAvatar(), kind: "guest" };
      ws.send(JSON.stringify({ type: "register", user: userInfo }));
      // Stop polling - WebSocket takes over
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    });

    ws.addEventListener("message", (event) => {
      try { handleWsEvent(JSON.parse(event.data)); } catch (e) {}
    });

    ws.addEventListener("close", () => {
      wsConnected = false;
      setStatus("⚠️ Reconnecting...", true);
      startFallbackPolling();
      if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
      wsReconnectTimer = setTimeout(connectWebSocket, 5000);
    });

    ws.addEventListener("error", () => {
      wsConnected = false;
    });
  };

  const handleWsEvent = (data) => {
    switch (data.type) {
      case "message": {
        const msg = data.message;
        const existingIndex = messagesCache.findIndex(m => m.id === msg.id);
        if (existingIndex > -1) {
          messagesCache[existingIndex] = msg;
        } else {
          messagesCache.push(msg);
          playChime("message");
        }
        if (messagesCache.length > 200) messagesCache = messagesCache.slice(-200);
        renderSessions();
        renderMessages();
        if (messagesCache.length) localStorage.setItem(STORAGE_KEYS.lastSeen, messagesCache[messagesCache.length - 1].created_at);
        break;
      }
      case "reaction": {
        const target = messagesCache.find(m => m.id === data.message_id);
        if (target) {
          target.reactions = data.reactions;
          renderMessages();
        }
        break;
      }
      case "delete": {
        messagesCache = messagesCache.filter(m => m.id !== data.message_id);
        renderSessions();
        const elMsg = el.messages.querySelector(`[data-message-id="${data.message_id}"]`);
        if (elMsg) {
          elMsg.classList.add("deleting");
          setTimeout(() => elMsg.remove(), 300);
        }
        break;
      }
      case "presence": {
        el.online.textContent = String(data.online_count || 0);
        onlineUsers = data.users || [];
        renderActiveRoom();
        renderSessions();
        renderMembers();
        break;
      }
      case "typing": {
        renderTypingIndicator(data.users || []);
        break;
      }
      case "pong":
        break;
    }
  };

  // ── Typing indicator ─────────────────────────────────────────────────────
  const sendTypingStatus = (isTyping) => {
    if (!wsConnected || !ws) return;
    try { ws.send(JSON.stringify({ type: "typing", is_typing: isTyping })); } catch (e) {}
  };

  const handleUserTyping = () => {
    sendTypingStatus(true);
    if (typingTimer) clearTimeout(typingTimer);
    typingTimer = setTimeout(() => sendTypingStatus(false), 3000);
  };

  const addTypingContainer = () => {
    if (roomRoot.querySelector("[data-room-typing]")) return;
    const typingDiv = document.createElement("div");
    typingDiv.dataset.roomTyping = "";
    typingDiv.className = "chat-typing-indicator";
    typingDiv.hidden = true;
    el.messages && el.messages.after(typingDiv);
  };

  const renderTypingIndicator = (users) => {
    const typingDiv = roomRoot.querySelector("[data-room-typing]");
    if (!typingDiv) return;
    if (!users || users.length === 0) {
      typingDiv.hidden = true;
      typingDiv.textContent = "";
      return;
    }
    const myId = currentUser ? currentUser.id : getGuestId();
    const others = users.filter(u => u.id !== myId);
    if (others.length === 0) { typingDiv.hidden = true; return; }
    const names = others.slice(0, 3).map(u => u.name || "Someone").join(", ");
    typingDiv.textContent = `${names} ${others.length === 1 ? "is" : "are"} typing...`;
    typingDiv.hidden = false;
  };

  // ── Sound toggle button ──────────────────────────────────────────────────
  const addSoundToggleButton = () => {
    const topbar = roomRoot.querySelector(".chat-room-topbar");
    if (!topbar || topbar.querySelector("[data-sound-toggle]")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.soundToggle = "";
    btn.className = "chat-icon-button";
    btn.title = "Toggle sound notifications";
    btn.setAttribute("aria-label", "Toggle sound");
    btn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>`;
    btn.addEventListener("click", () => {
      soundEnabled = !soundEnabled;
      btn.style.opacity = soundEnabled ? "1" : "0.4";
      btn.title = soundEnabled ? "Sound on" : "Sound off";
    });
    topbar.appendChild(btn);
  };

  // ── Message search bar ───────────────────────────────────────────────────
  const addSearchInput = () => {
    const topbar = roomRoot.querySelector(".chat-room-topbar");
    if (!topbar || topbar.querySelector("[data-msg-search]")) return;
    const wrap = document.createElement("div");
    wrap.className = "chat-search-wrap";
    const input = document.createElement("input");
    input.type = "search";
    input.placeholder = "Search messages...";
    input.dataset.msgSearch = "";
    input.className = "chat-search-input";
    input.setAttribute("aria-label", "Search chat messages");
    input.addEventListener("input", () => {
      searchQuery = input.value.trim().toLowerCase();
      renderMessages();
    });
    wrap.appendChild(input);
    topbar.appendChild(wrap);
  };

  // ── Online users popover ─────────────────────────────────────────────────
  const showOnlineUsersPopover = (anchor) => {
    let popover = document.getElementById("online-users-popover");
    if (popover) { popover.remove(); return; }
    popover = document.createElement("div");
    popover.id = "online-users-popover";
    popover.className = "online-users-popover";
    if (!onlineUsers.length) {
      popover.innerHTML = "<p>No one online right now.</p>";
    } else {
      const ul = document.createElement("ul");
      onlineUsers.slice(0, 20).forEach(user => {
        const li = document.createElement("li");
        const dot = document.createElement("span");
        dot.className = "online-dot";
        const name = document.createElement("span");
        name.textContent = user.name || "Guest";
        li.append(dot, name);
        ul.appendChild(li);
      });
      popover.appendChild(ul);
    }
    anchor.after(popover);
    const dismiss = (e) => { if (!popover.contains(e.target) && e.target !== anchor) { popover.remove(); document.removeEventListener("click", dismiss); } };
    setTimeout(() => document.addEventListener("click", dismiss), 10);
  };

  const renderActiveRoom = () => {
    if (!el.title || !el.subtitle || !el.activeAvatar) return;
    const target = replyToUser ? getUserById(replyToUser) : null;
    if (!replyToUser) {
      el.title.textContent = "Community Room";
      el.subtitle.textContent = "Public room with replies, reactions, files, voice, GIFs, and translation";
      el.activeAvatar.textContent = "#";
      el.activeAvatar.style.backgroundImage = "";
      if (el.whisperClear) el.whisperClear.hidden = true;
      if (el.globalSession) el.globalSession.classList.add("active");
      return;
    }

    const name = userDisplayName(target) || "Private player";
    el.title.textContent = `Whisper: ${name}`;
    el.subtitle.textContent = "Private messages are visible only to you and this player";
    el.activeAvatar.textContent = initials(name);
    el.activeAvatar.style.backgroundImage = target && target.avatar_url ? `url("${target.avatar_url}")` : "";
    if (el.whisperClear) el.whisperClear.hidden = false;
    if (el.globalSession) el.globalSession.classList.remove("active");
  };

  const switchToGlobalRoom = () => {
    replyToUser = null;
    selectedProfileUser = null;
    if (el.profile) el.profile.hidden = true;
    renderActiveRoom();
    renderSessions();
    renderMembers();
    renderMessages();
    el.input.focus();
  };

  const startWhisper = (userId, keepReply = false) => {
    const normalized = normalizeUserId(userId);
    if (!normalized || normalized === normalizeUserId(getMyId())) return;
    replyToUser = normalized;
    if (!keepReply) replyTo = null;
    if (!keepReply && el.reply) el.reply.hidden = true;
    renderActiveRoom();
    renderSessions();
    renderMembers();
    renderMessages();
    el.input.focus();
  };

  const conversationPartners = () => {
    const ids = new Set();
    messagesCache.forEach((message) => {
      const partner = otherPartyId(message);
      if (partner) ids.add(partner);
    });
    onlineUsers.forEach((user) => {
      const id = normalizeUserId(user.id || user.name);
      if (id && !isCurrentUser(user)) ids.add(id);
    });
    return Array.from(ids);
  };

  const privateMessagesWith = (userId) => {
    const normalized = normalizeUserId(userId);
    return messagesCache.filter((message) => otherPartyId(message) === normalized);
  };

  const renderSessions = () => {
    if (!el.sessionList) return;
    const partners = conversationPartners();
    el.sessionList.replaceChildren();
    partners.forEach((userId) => {
      const user = getUserById(userId);
      const privateMessages = privateMessagesWith(userId);
      const last = privateMessages[privateMessages.length - 1];
      const name = userDisplayName(user) || (last && userDisplayName(last.author)) || "Private player";
      const button = document.createElement("button");
      button.type = "button";
      button.className = `chat-session-item${normalizeUserId(replyToUser) === normalizeUserId(userId) ? " active" : ""}`;
      const avatar = document.createElement("span");
      avatar.className = "chat-session-icon";
      if (user && user.avatar_url) {
        avatar.style.backgroundImage = `url("${user.avatar_url}")`;
      } else {
        avatar.textContent = initials(name);
      }
      const body = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = name;
      const small = document.createElement("small");
      small.textContent = last ? (last.content || "Attachment") : "Start a private whisper";
      body.append(strong, small);
      button.append(avatar, body);
      button.addEventListener("click", () => startWhisper(userId));
      el.sessionList.appendChild(button);
    });
    if (el.sessionCount) el.sessionCount.textContent = String(partners.length + 1);
    if (el.globalSession) el.globalSession.classList.toggle("active", !replyToUser);
  };

  const openProfile = (user) => {
    if (!user || !el.profile) return;
    selectedProfileUser = user;
    const name = userDisplayName(user);
    el.profile.hidden = false;
    if (el.profileAvatar) {
      el.profileAvatar.textContent = initials(name);
      el.profileAvatar.style.backgroundImage = user.avatar_url ? `url("${user.avatar_url}")` : "";
    }
    if (el.profileName) el.profileName.textContent = name;
    if (el.profileKind) el.profileKind.textContent = user.kind === "discord" ? "Verified Discord player" : "Guest account";
    if (el.profileWhisper) el.profileWhisper.disabled = isCurrentUser(user);
  };

  const renderMembers = () => {
    if (!el.memberList) return;
    const query = memberSearchQuery.trim().toLowerCase();
    const members = onlineUsers
      .filter((user) => !query || userDisplayName(user).toLowerCase().includes(query))
      .sort((a, b) => Number(isCurrentUser(b)) - Number(isCurrentUser(a)) || userDisplayName(a).localeCompare(userDisplayName(b)));

    el.memberList.replaceChildren();
    members.forEach((user) => {
      const userId = normalizeUserId(user.id || user.name);
      const name = userDisplayName(user);
      const button = document.createElement("button");
      button.type = "button";
      button.className = `chat-member-item${normalizeUserId(replyToUser) === userId ? " active" : ""}`;
      const avatar = document.createElement("span");
      avatar.className = "chat-member-avatar";
      if (user.avatar_url) {
        avatar.style.backgroundImage = `url("${user.avatar_url}")`;
      } else {
        avatar.textContent = initials(name);
      }
      const body = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = `${name}${isCurrentUser(user) ? " (you)" : ""}`;
      const small = document.createElement("small");
      small.textContent = user.kind === "discord" ? "Discord player" : "Guest player";
      body.append(strong, small);
      button.append(avatar, body);
      button.addEventListener("click", () => openProfile(user));
      el.memberList.appendChild(button);
    });

    if (!members.length) {
      const empty = document.createElement("p");
      empty.className = "chat-member-empty";
      empty.textContent = "No matching players online.";
      el.memberList.appendChild(empty);
    }
    if (el.memberCount) el.memberCount.textContent = `${onlineUsers.length} online`;
  };

  const startFallbackPolling = () => {
    if (!pollTimer) {
      refreshMessages();
      pollTimer = window.setInterval(refreshMessages, 5000);
    }
    if (!presenceTimer) {
      sendPresence();
      presenceTimer = window.setInterval(sendPresence, 25000);
    }
  };



  const renderPendingAttachments = () => {
    el.attachments.hidden = pendingAttachments.length === 0;
    el.attachments.replaceChildren();
    pendingAttachments.forEach((attachment, index) => {
      const chip = document.createElement("div");
      chip.className = "chat-attachment-chip";
      const label = document.createElement("span");
      label.textContent = attachment.name || "file";
      const remove = document.createElement("button");
      remove.className = "chat-attachment-remove";
      remove.type = "button";
      remove.textContent = "x";
      remove.title = "Remove file";
      remove.addEventListener("click", () => {
        pendingAttachments.splice(index, 1);
        renderPendingAttachments();
      });
      chip.append(label, remove);
      el.attachments.appendChild(chip);
    });
  };

  const setReply = (message, isPrivate = false) => {
    replyTo = message;
    if (isPrivate) {
      startWhisper((message.author || {}).id || (message.author || {}).name, true);
    }
    el.reply.hidden = false;
    el.replyName.textContent = (message.author || {}).name || "Player";
    el.replyContent.textContent = isPrivate ? "(Private Message) " + (message.content || "Attachment") : (message.content || "Attachment");
    el.input.focus();
  };

  const clearReply = () => {
    replyTo = null;
    if (!el.whisperClear) replyToUser = null;
    el.reply.hidden = true;
    el.replyName.textContent = "";
    el.replyContent.textContent = "";
    renderActiveRoom();
    renderSessions();
  };

  const uploadBlob = async (blob, filename) => {
    const formData = new FormData();
    formData.append("file", blob, filename);
    const response = await fetch("/api/chat/upload", {
      method: "POST",
      headers: authHeaders(),
      body: formData
    });
    if (!response.ok) throw new Error("Upload failed");
    return (await response.json()).attachment;
  };

  const uploadFiles = async (files) => {
    const selected = Array.from(files).slice(0, Math.max(0, 4 - pendingAttachments.length));
    for (const file of selected) {
      if (file.size > 8 * 1024 * 1024) {
        setStatus(`${file.name} is larger than 8 MB`, true);
        continue;
      }
      try {
        setStatus(`Uploading ${file.name}...`);
        pendingAttachments.push(await uploadBlob(file, file.name));
        renderPendingAttachments();
        setStatus("File ready");
      } catch (error) {
        setStatus(`Could not upload ${file.name}`, true);
      }
    }
    el.file.value = "";
  };

  const renderAttachment = (attachment, list) => {
    if (isImageAttachment(attachment)) {
      const link = document.createElement("a");
      link.href = attachment.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const image = document.createElement("img");
      image.src = attachment.preview_url || attachment.url;
      image.alt = attachment.name || "Chat attachment";
      link.appendChild(image);
      list.appendChild(link);
      return;
    }
    if (isAudioAttachment(attachment)) {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.src = attachment.url;
      list.appendChild(audio);
      return;
    }
    const link = document.createElement("a");
    link.className = "chat-file-link";
    link.href = attachment.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = attachment.name || "Download file";
    list.appendChild(link);
  };

  const renderMessage = (message) => {
    const article = document.createElement("article");
    article.className = "chat-message";
    article.dataset.messageId = message.id;

    const avatar = document.createElement("div");
    avatar.className = "chat-avatar";
    const author = message.author || {};
    if (normalizeUserId(author.id || author.name) === normalizeUserId(getMyId())) {
      article.classList.add("is-self");
    }
    if (author.avatar_url) {
      const image = document.createElement("img");
      image.src = author.avatar_url;
      image.alt = "";
      avatar.appendChild(image);
    } else {
      avatar.textContent = initials(author.name);
    }

    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";

    const top = document.createElement("div");
    top.className = "chat-bubble-top";
    const name = document.createElement("div");
    name.className = "chat-author";
    name.textContent = author.name || "Guest Player";
    const time = document.createElement("time");
    time.className = "chat-meta";
    time.dateTime = message.created_at || "";
    time.textContent = formatTime(message.created_at);
    top.append(name, time);
    bubble.appendChild(top);

    if (message.reply_to) {
      const reply = document.createElement("div");
      reply.className = "chat-reply-context";
      const replyName = document.createElement("strong");
      replyName.textContent = message.reply_to.author_name || "Player";
      const replyText = document.createElement("span");
      replyText.textContent = message.reply_to.content || "Attachment";
      reply.append(replyName, replyText);
      bubble.appendChild(reply);
    }
    
    if (message.target_user_id) {
       bubble.classList.add("chat-bubble-private");
       const privInd = document.createElement("span");
       privInd.className = "private-indicator";
       privInd.textContent = "Private Whisper";
       top.appendChild(privInd);
    }

    if (message.content) {
      const content = document.createElement("p");
      content.className = "chat-content";
      content.textContent = message.content;
      bubble.appendChild(content);
    }

    if (Array.isArray(message.attachments) && message.attachments.length) {
      const list = document.createElement("div");
      list.className = "chat-message-attachments";
      message.attachments.forEach((attachment) => renderAttachment(attachment, list));
      bubble.appendChild(list);
    }

    if (Array.isArray(message.reactions) && message.reactions.length) {
      const reactions = document.createElement("div");
      reactions.className = "chat-reactions";
      message.reactions.forEach((reaction) => {
        const pill = document.createElement("span");
        pill.className = "chat-reaction-pill";
        pill.textContent = `${reaction.emoji} ${reaction.count}`;
        reactions.appendChild(pill);
      });
      bubble.appendChild(reactions);
    }

    const actions = document.createElement("div");
    actions.className = "chat-message-actions";
    if (message.content) {
      const translate = document.createElement("button");
      translate.className = "chat-translate-button";
      translate.type = "button";
      translate.textContent = "EN";
      translate.title = "Translate to English";
      translate.addEventListener("click", () => translateMessage(message, bubble, translate));
      actions.appendChild(translate);
    }
    const replyButton = document.createElement("button");
    replyButton.className = "chat-reply-button";
    replyButton.type = "button";
    replyButton.textContent = "Reply";
    replyButton.addEventListener("click", () => setReply(message));
    actions.appendChild(replyButton);
    
    const dmButton = document.createElement("button");
    dmButton.className = "chat-reply-button";
    dmButton.type = "button";
    dmButton.textContent = "Whisper";
    dmButton.title = "Private Message";
    dmButton.addEventListener("click", () => setReply(message, true));
    actions.appendChild(dmButton);

    quickReactions.forEach((emoji) => {
      const button = document.createElement("button");
      button.className = "chat-react-button";
      button.type = "button";
      button.textContent = emoji;
      button.title = `React ${emoji}`;
      button.addEventListener("click", () => reactToMessage(message.id, emoji));
      actions.appendChild(button);
    });

    const report = document.createElement("button");
    report.className = "chat-report-button";
    report.type = "button";
    report.textContent = "Report";
    report.addEventListener("click", () => reportMessage(message));
    actions.appendChild(report);
    
    const myId = currentUser ? currentUser.id : getGuestId();
    if (author.id === myId) {
      const deleteBtn = document.createElement("button");
      deleteBtn.className = "chat-report-button";
      deleteBtn.type = "button";
      deleteBtn.textContent = "🗑️";
      deleteBtn.title = "Delete Message";
      deleteBtn.addEventListener("click", () => deleteMessage(message.id));
      actions.appendChild(deleteBtn);
    }
    
    bubble.appendChild(actions);

    article.append(avatar, bubble);
    return article;
  };

  const renderMessages = () => {
    const roomFiltered = replyToUser
      ? messagesCache.filter((message) => otherPartyId(message) === normalizeUserId(replyToUser))
      : messagesCache.filter((message) => !message.target_user_id);
    const filtered = searchQuery
      ? roomFiltered.filter(m => (m.content || "").toLowerCase().includes(searchQuery) || ((m.author || {}).name || "").toLowerCase().includes(searchQuery))
      : roomFiltered;
    el.messages.replaceChildren();
    filtered.forEach((message) => el.messages.appendChild(renderMessage(message)));
    if (!filtered.length) {
      const empty = document.createElement("div");
      empty.className = "chat-empty-state";
      empty.innerHTML = replyToUser
        ? "<strong>No whispers yet</strong><span>Send the first private message in this conversation.</span>"
        : "<strong>No community messages yet</strong><span>Start the room with a message, file, GIF, or voice note.</span>";
      el.messages.appendChild(empty);
    }
    el.messages.scrollTop = el.messages.scrollHeight;
  };

  const refreshMessages = async () => {
    el.loading.hidden = false;
    try {
      const guestId = getGuestId();
      const response = await fetch(`/api/chat/messages?limit=100&guest_id=${encodeURIComponent(guestId)}`, { headers: { Accept: "application/json", ...authHeaders() } });
      if (!response.ok) throw new Error("Chat unavailable");
      const payload = await response.json();
      messagesCache = payload.messages || [];
      el.online.textContent = String(payload.online_count || 0);
      renderActiveRoom();
      renderSessions();
      renderMembers();
      renderMessages();
      if (messagesCache.length) {
        localStorage.setItem(STORAGE_KEYS.lastSeen, messagesCache[messagesCache.length - 1].created_at);
      }
      setStatus(messagesCache.length ? "Live global room" : "No messages yet");
    } catch (error) {
      setStatus("Global chat is offline right now", true);
    } finally {
      el.loading.hidden = true;
    }
  };

  const sendPresence = async () => {
    try {
      const response = await fetch("/api/chat/presence", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          display_name: getGuestName() || el.name.value,
          guest_id: getGuestId(),
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
        })
      });
      if (!response.ok) return;
      const data = await response.json();
      el.online.textContent = String(data.online_count || 0);
    } catch (error) {
      // Presence is best-effort.
    }
  };

  const sendMessage = async () => {
    const guestName = getGuestName() || setGuestName(el.name.value);
    if (!currentUser && !guestName) {
      el.login.classList.remove("is-hidden");
      el.name.focus();
      setStatus("Add a player name or login with Discord", true);
      return;
    }

    const content = el.input.value.trim();
    if (!content && !pendingAttachments.length) return;

    // Stop typing indicator on send
    if (typingTimer) { clearTimeout(typingTimer); typingTimer = null; }
    sendTypingStatus(false);

    try {
      const response = await fetch("/api/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          content,
          display_name: guestName,
          guest_id: getGuestId(),
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
          client_time: new Date().toISOString(),
          reply_to_id: replyTo ? replyTo.id : null,
          target_user_id: replyToUser ? String(replyToUser) : null,
          attachments: pendingAttachments
        })
      });
      if (!response.ok) throw new Error("Message failed");
      el.input.value = "";
      el.input.style.height = "";
      pendingAttachments = [];
      renderPendingAttachments();
      clearReply();
      // If WebSocket is live, it will broadcast. Otherwise refresh.
      if (!wsConnected) await refreshMessages();
    } catch (error) {
      setStatus("Message was not sent", true);
    }
  };
  
  const deleteMessage = async (messageId) => {
    if (!confirm("Are you sure you want to delete this message?")) return;
    try {
      const response = await fetch(`/api/chat/messages/${encodeURIComponent(messageId)}?guest_id=${encodeURIComponent(getGuestId())}`, {
        method: "DELETE",
        headers: authHeaders()
      });
      if (!response.ok) throw new Error("Delete failed");
      // UI is removed via WebSocket or poll
    } catch (error) {
      setStatus("Delete failed", true);
    }
  };

  const translateMessage = async (message, bubble, button) => {
    const existing = bubble.querySelector(".chat-translation");
    if (existing) {
      existing.remove();
      button.textContent = "EN";
      return;
    }
    button.disabled = true;
    button.textContent = "...";
    try {
      const response = await fetch("/api/chat/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ text: message.content })
      });
      if (!response.ok) throw new Error("Translation unavailable");
      const data = await response.json();
      const translated = document.createElement("div");
      translated.className = "chat-translation";
      translated.textContent = data.translated_text || message.content;
      bubble.appendChild(translated);
      button.textContent = "Hide";
    } catch (error) {
      setStatus("Translation failed", true);
      button.textContent = "EN";
    } finally {
      button.disabled = false;
    }
  };

  const reactToMessage = async (messageId, emoji) => {
    try {
      const response = await fetch(`/api/chat/messages/${encodeURIComponent(messageId)}/react`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ emoji, display_name: getGuestName() || el.name.value, guest_id: getGuestId() })
      });
      if (!response.ok) throw new Error("Reaction failed");
      // WebSocket broadcast will update reactions; only poll as fallback
      if (!wsConnected) await refreshMessages();
    } catch (error) {
      setStatus("Reaction failed", true);
    }
  };

  const reportMessage = async (message) => {
    const reason = window.prompt("Report reason", "Spam or abusive message");
    if (!reason) return;
    try {
      const response = await fetch(`/api/chat/messages/${encodeURIComponent(message.id)}/report`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          reason,
          display_name: getGuestName() || el.name.value,
          guest_id: getGuestId(),
          reported_content: message.content,
          reported_author_name: message.author.name
        })
      });
      if (!response.ok) throw new Error("Report failed");
      setStatus("Report sent for review");
    } catch (error) {
      setStatus("Report failed", true);
    }
  };

  const insertEmoji = (emoji) => {
    const start = el.input.selectionStart || el.input.value.length;
    const end = el.input.selectionEnd || el.input.value.length;
    el.input.value = `${el.input.value.slice(0, start)}${emoji}${el.input.value.slice(end)}`;
    el.input.focus();
    el.input.selectionStart = el.input.selectionEnd = start + emoji.length;
    el.input.dispatchEvent(new Event("input"));
  };

  const searchTenor = async () => {
    const q = el.tenorSearch.value.trim() || "whiteout survival";
    el.tenorResults.innerHTML = '<span class="global-chat-status">Loading GIFs...</span>';
    try {
      const endpoint = activeGifSource === 'giphy' ? '/api/chat/giphy' : '/api/chat/tenor';
      const response = await fetch(`${endpoint}?q=${encodeURIComponent(q)}&limit=18`, { headers: authHeaders() });
      if (!response.ok) throw new Error("GIF search unavailable");
      const data = await response.json();
      el.tenorResults.replaceChildren();
      (data.results || []).forEach((gif) => {
        const button = document.createElement("button");
        button.type = "button";
        const image = document.createElement("img");
        image.src = gif.preview_url || gif.url;
        image.alt = gif.title || "GIF";
        button.appendChild(image);
        button.addEventListener("click", () => {
          pendingAttachments.push({ name: gif.title || "GIF", url: gif.url, type: "image/gif", size: 0 });
          renderPendingAttachments();
          el.tenorPanel.hidden = true;
          el.input.focus();
        });
        el.tenorResults.appendChild(button);
      });
      if (!el.tenorResults.children.length) el.tenorResults.textContent = "No GIFs found";
    } catch (error) {
      el.tenorResults.textContent = "GIF search unavailable";
    }
  };

  const toggleVoice = async () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("Voice recording is not supported in this browser", true);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      voiceChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.addEventListener("dataavailable", (event) => {
        if (event.data.size) voiceChunks.push(event.data);
      });
      mediaRecorder.addEventListener("stop", async () => {
        stream.getTracks().forEach((track) => track.stop());
        el.voice.classList.remove("is-recording");
        try {
          const blob = new Blob(voiceChunks, { type: mediaRecorder.mimeType || "audio/webm" });
          pendingAttachments.push(await uploadBlob(blob, `voice-${Date.now()}.webm`));
          renderPendingAttachments();
          setStatus("Voice message ready");
        } catch (error) {
          setStatus("Voice upload failed", true);
        }
      });
      mediaRecorder.start();
      el.voice.classList.add("is-recording");
      setStatus("Recording voice... tap mic again to stop");
    } catch (error) {
      setStatus("Microphone permission denied", true);
    }
  };

  // Setup Emoji grid
  const emojiWrap = document.createElement('div');
  emojiWrap.className = 'emoji-grid-container';
  Object.entries(emojiCategories).forEach(([category, emojis]) => {
    const title = document.createElement('div');
    title.className = 'emoji-category-title';
    title.textContent = category;
    emojiWrap.appendChild(title);
    
    emojis.forEach((emoji) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = emoji;
      button.title = emoji;
      button.addEventListener("click", () => insertEmoji(emoji));
      emojiWrap.appendChild(button);
    });
  });
  el.emojiPanel.appendChild(emojiWrap);
  
  // Setup GIF tabs
  const gifTabs = document.createElement('div');
  gifTabs.className = 'gif-source-tabs';
  gifTabs.innerHTML = `
    <button type="button" class="active" data-source="tenor">Tenor</button>
    <button type="button" data-source="giphy">Giphy</button>
  `;
  el.tenorSearch.parentElement.before(gifTabs);
  
  gifTabs.addEventListener('click', (e) => {
    if(e.target.tagName !== 'BUTTON') return;
    Array.from(gifTabs.children).forEach(b => b.classList.remove('active'));
    e.target.classList.add('active');
    activeGifSource = e.target.dataset.source;
    searchTenor();
  });

  el.discord.href = buildDiscordAuthUrl();
  el.name.value = getGuestName();
  updateIdentityView();
  resolveDiscordIdentity();

  if (el.online) {
    el.online.style.cursor = "pointer";
    el.online.title = "Click to see online users";
    el.online.addEventListener("click", () => showOnlineUsersPopover(el.online));
  }
  if (el.globalSession) el.globalSession.addEventListener("click", switchToGlobalRoom);
  if (el.whisperClear) el.whisperClear.addEventListener("click", switchToGlobalRoom);
  if (el.memberSearch) {
    el.memberSearch.addEventListener("input", () => {
      memberSearchQuery = el.memberSearch.value || "";
      renderMembers();
    });
  }
  if (el.profileClose) el.profileClose.addEventListener("click", () => {
    selectedProfileUser = null;
    if (el.profile) el.profile.hidden = true;
  });
  if (el.profileWhisper) el.profileWhisper.addEventListener("click", () => {
    if (selectedProfileUser) startWhisper(selectedProfileUser.id || selectedProfileUser.name);
  });
  if (el.profileCopy) el.profileCopy.addEventListener("click", async () => {
    if (!selectedProfileUser) return;
    const id = normalizeUserId(selectedProfileUser.id || selectedProfileUser.name);
    try {
      await navigator.clipboard.writeText(id);
      setStatus("Player ID copied");
    } catch (error) {
      setStatus(id);
    }
  });
  renderActiveRoom();
  renderSessions();
  renderMembers();

  el.guest.addEventListener("click", () => {
    const name = setGuestName(el.name.value);
    if (!name) {
      setStatus("Enter a player name first", true);
      el.name.focus();
      return;
    }
    currentUser = null;
    updateIdentityView();
    setStatus("Guest login ready");
    el.input.focus();
    // Re-register with new guest name over WebSocket
    if (wsConnected && ws) {
      ws.send(JSON.stringify({ type: "register", user: { id: getGuestId(), name, avatar_url: null, kind: "guest" } }));
    } else {
      sendPresence();
      refreshMessages();
    }
  });

  el.refresh.addEventListener("click", refreshMessages);
  el.file.addEventListener("change", (event) => uploadFiles(event.target.files || []));
  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });
  el.input.addEventListener("input", () => {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 140)}px`;
    handleUserTyping();
  });
  el.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  el.emoji.addEventListener("click", () => {
    el.emojiPanel.hidden = !el.emojiPanel.hidden;
    el.tenorPanel.hidden = true;
  });
  el.tenor.addEventListener("click", () => {
    el.tenorPanel.hidden = !el.tenorPanel.hidden;
    el.emojiPanel.hidden = true;
    if (!el.tenorPanel.hidden && !el.tenorResults.children.length) searchTenor();
  });
  el.tenorGo.addEventListener("click", searchTenor);
  el.tenorSearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      searchTenor();
    }
  });
  el.voice.addEventListener("click", toggleVoice);
  el.replyClear.addEventListener("click", clearReply);
  window.addEventListener("beforeunload", () => {
    if (pollTimer) window.clearInterval(pollTimer);
    if (presenceTimer) window.clearInterval(presenceTimer);
    if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
    if (ws) try { ws.close(); } catch (e) {}
  });
})();
