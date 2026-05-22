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
    replyClear: roomRoot.querySelector("[data-room-reply-clear]")
  };

  const DISCORD_CLIENT_ID = "1399025185046134866";
  const STORAGE_KEYS = {
    guestId: "wos_global_chat_guest_id",
    guestName: "wos_global_chat_guest_name",
    lastSeen: "wos_global_chat_last_seen_at"
  };
  const emojis = ["😀", "😂", "🔥", "❄️", "🎁", "⚔️", "🛡️", "🏆", "💎", "✅", "👀", "🤝", "🙏", "🚀", "💬", "❤️"];
  const quickReactions = ["👍", "❤️", "😂", "🔥", "❄️", "🎁"];
  let currentUser = null;
  let pendingAttachments = [];
  let messagesCache = [];
  let replyTo = null;
  let pollTimer = null;
  let presenceTimer = null;
  let mediaRecorder = null;
  let voiceChunks = [];
  let tinodeInstance = null;
  let tinodeTopic = null;
  let tinodeConfig = { enabled: false };

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
  const setGuestName = (name) => {
    const cleaned = (name || "").trim().slice(0, 32);
    if (cleaned) localStorage.setItem(STORAGE_KEYS.guestName, cleaned);
    return cleaned;
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
      el.identity.textContent = `Discord: ${currentUser.global_name || currentUser.username}`;
      el.login.classList.add("is-hidden");
      return;
    }
    if (guestName) {
      el.identity.textContent = `Guest: ${guestName}`;
      el.login.classList.add("is-hidden");
      return;
    }
    el.identity.textContent = "Guest access";
    el.login.classList.remove("is-hidden");
  };

  const resolveDiscordIdentity = async () => {
    if (!getToken()) {
      updateIdentityView();
      initTinode();
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
      initTinode();
    }
  };

  const initTinode = async () => {
    try {
      const res = await fetch("/api/chat/config");
      if (!res.ok) throw new Error("Failed to load chat config");
      tinodeConfig = await res.json();
    } catch (e) {
      console.warn("Failed to fetch Tinode config, using fallback polling.", e);
      startFallbackPolling();
      return;
    }

    if (!tinodeConfig.enabled || typeof Tinode === "undefined") {
      console.log("Tinode is disabled or library not loaded, using fallback polling.");
      startFallbackPolling();
      return;
    }

    console.log("Tinode is enabled, connecting to:", tinodeConfig.server_url);
    connectTinode();
  };

  const connectTinode = async () => {
    if (tinodeInstance) {
      try {
        tinodeInstance.disconnect();
      } catch (e) {}
    }

    try {
      tinodeInstance = new Tinode({
        appName: "WhiteoutSurvivalChat",
        host: tinodeConfig.server_url,
        apiKey: tinodeConfig.api_key,
        transport: "ws",
        secure: tinodeConfig.secure
      });
      tinodeInstance.enableLogging(true);

      tinodeInstance.onDisconnect = (err) => {
        console.warn("Tinode disconnected:", err);
        setStatus("Real-time disconnected. Retrying...", true);
        setTimeout(connectTinode, 5000);
      };

      await tinodeInstance.connect();

      const guestName = getGuestName();
      const currentUserName = currentUser ? (currentUser.global_name || currentUser.username) : guestName;

      if (!currentUser && !guestName) {
        setStatus("Waiting for user to choose name...", false);
        return;
      }

      const uid = currentUser ? `discord_${currentUser.id}` : `guest_${getGuestId()}`;
      const password = uid + "_secret123";

      let ctrl;
      try {
        ctrl = await tinodeInstance.loginBasic(uid, password);
      } catch (err) {
        console.log("Login failed, attempting account registration for:", uid);
        ctrl = await tinodeInstance.createAccountBasic(uid, password, {
          public: { fn: currentUserName },
          login: true
        });
      }

      console.log("Tinode logged in as:", tinodeInstance.getCurrentUserId());

      tinodeTopic = tinodeInstance.getTopic(tinodeConfig.topic);

      tinodeTopic.onData = (msg) => {
        handleIncomingTinodeMessage(msg);
      };

      tinodeTopic.onMeta = (meta) => {
        if (meta.sub) {
          const onlineCount = meta.sub.length;
          el.online.textContent = String(onlineCount);
        }
      };

      await tinodeTopic.subscribe({
        get: {
          desc: {},
          sub: {},
          data: { limit: 80 }
        }
      });

      setStatus("Live global room (Real-time)");
    } catch (error) {
      console.error("Tinode connection/subscription failed:", error);
      setStatus("Real-time offline. Falling back...", true);
      startFallbackPolling();
    }
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

  const parseTinodeMessage = (msg) => {
    let parsed = null;
    try {
      parsed = JSON.parse(msg.content);
    } catch (e) {}

    if (parsed && parsed.ver === "wos-1.0") {
      return {
        id: String(msg.seq),
        seq: msg.seq,
        content: parsed.content || parsed.text || "",
        author: parsed.author || { name: msg.from || "User", kind: "guest" },
        attachments: parsed.attachments || [],
        reply_to: parsed.reply_to || null,
        reactions: parsed.reactions || [],
        created_at: parsed.client_time || msg.ts || new Date().toISOString(),
        timezone: parsed.timezone || "UTC",
        type: parsed.type || "message",
        rawMsg: msg
      };
    } else {
      return {
        id: String(msg.seq),
        seq: msg.seq,
        content: typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content),
        author: {
          name: msg.from || "User",
          avatar_url: null,
          kind: "guest"
        },
        attachments: [],
        reply_to: null,
        reactions: [],
        created_at: msg.ts || new Date().toISOString(),
        timezone: "UTC",
        type: "message",
        rawMsg: msg
      };
    }
  };

  const handleIncomingTinodeMessage = (msg) => {
    const parsed = parseTinodeMessage(msg);
    if (parsed.type === "reaction") {
      handleTinodeReaction(parsed);
      return;
    }

    const existingIndex = messagesCache.findIndex(m => m.seq === parsed.seq);
    if (existingIndex > -1) {
      messagesCache[existingIndex] = parsed;
    } else {
      messagesCache.push(parsed);
    }

    messagesCache.sort((a, b) => a.seq - b.seq);

    if (messagesCache.length > 150) {
      messagesCache = messagesCache.slice(messagesCache.length - 150);
    }

    renderMessages();
    
    if (messagesCache.length) {
      localStorage.setItem(STORAGE_KEYS.lastSeen, messagesCache[messagesCache.length - 1].created_at);
    }
  };

  const handleTinodeReaction = (parsedReaction) => {
    let parsedContent;
    try {
      parsedContent = JSON.parse(parsedReaction.rawMsg.content);
    } catch (e) {
      return;
    }
    const targetSeq = parsedContent.target_seq;
    const emoji = parsedContent.emoji;
    const userId = parsedContent.author ? parsedContent.author.id : null;
    if (!targetSeq || !emoji || !userId) return;

    const targetMsg = messagesCache.find(m => m.seq === targetSeq);
    if (targetMsg) {
      if (!targetMsg.reactions) targetMsg.reactions = [];
      let group = targetMsg.reactions.find(r => r.emoji === emoji);
      if (!group) {
        group = { emoji: emoji, count: 0, users: [] };
        targetMsg.reactions.push(group);
      }
      if (!group.users) group.users = [];
      
      const idx = group.users.indexOf(userId);
      if (idx > -1) {
        group.users.splice(idx, 1);
        group.count = Math.max(0, group.count - 1);
      } else {
        group.users.push(userId);
        group.count++;
      }
      
      targetMsg.reactions = targetMsg.reactions.filter(r => r.count > 0);
      renderMessages();
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

  const setReply = (message) => {
    replyTo = message;
    el.reply.hidden = false;
    el.replyName.textContent = (message.author || {}).name || "Player";
    el.replyContent.textContent = message.content || "Attachment";
    el.input.focus();
  };

  const clearReply = () => {
    replyTo = null;
    el.reply.hidden = true;
    el.replyName.textContent = "";
    el.replyContent.textContent = "";
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
    bubble.appendChild(actions);

    article.append(avatar, bubble);
    return article;
  };

  const renderMessages = () => {
    el.messages.replaceChildren();
    messagesCache.forEach((message) => el.messages.appendChild(renderMessage(message)));
    el.messages.scrollTop = el.messages.scrollHeight;
  };

  const refreshMessages = async () => {
    el.loading.hidden = false;
    try {
      const response = await fetch("/api/chat/messages?limit=100", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Chat unavailable");
      const payload = await response.json();
      messagesCache = payload.messages || [];
      el.online.textContent = String(payload.online_count || 0);
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

    if (tinodeConfig.enabled && tinodeTopic && tinodeTopic.isSubscribed) {
      try {
        const payload = {
          ver: "wos-1.0",
          type: "message",
          content: content,
          author: {
            name: currentUser ? (currentUser.global_name || currentUser.username) : guestName,
            avatar_url: currentUser ? currentUser.avatar_url : null,
            kind: currentUser ? "discord" : "guest",
            id: currentUser ? currentUser.id : getGuestId()
          },
          attachments: pendingAttachments,
          reply_to: replyTo ? {
            id: String(replyTo.seq || replyTo.id),
            author_name: replyTo.author.name,
            content: replyTo.content
          } : null,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
          client_time: new Date().toISOString()
        };

        tinodeTopic.publish(JSON.stringify(payload));
        
        el.input.value = "";
        el.input.style.height = "";
        pendingAttachments = [];
        renderPendingAttachments();
        clearReply();
      } catch (error) {
        console.error("Failed to publish Tinode message:", error);
        setStatus("Message failed to send", true);
      }
    } else {
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
            attachments: pendingAttachments
          })
        });
        if (!response.ok) throw new Error("Message failed");
        el.input.value = "";
        el.input.style.height = "";
        pendingAttachments = [];
        renderPendingAttachments();
        clearReply();
        await refreshMessages();
      } catch (error) {
        setStatus("Message was not sent", true);
      }
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
    const isTinodeMsg = typeof messageId === "number" || /^\d+$/.test(messageId);
    
    if (tinodeConfig.enabled && tinodeTopic && tinodeTopic.isSubscribed && isTinodeMsg) {
      try {
        const reactionPayload = {
          ver: "wos-1.0",
          type: "reaction",
          target_seq: parseInt(messageId, 10),
          emoji: emoji,
          author: {
            id: currentUser ? currentUser.id : getGuestId(),
            name: currentUser ? (currentUser.global_name || currentUser.username) : getGuestName()
          }
        };
        tinodeTopic.publish(JSON.stringify(reactionPayload));
      } catch (error) {
        console.error("Failed to react via Tinode:", error);
        setStatus("Reaction failed", true);
      }
    } else {
      try {
        const response = await fetch(`/api/chat/messages/${encodeURIComponent(messageId)}/react`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders() },
          body: JSON.stringify({ emoji, display_name: getGuestName() || el.name.value, guest_id: getGuestId() })
        });
        if (!response.ok) throw new Error("Reaction failed");
        await refreshMessages();
      } catch (error) {
        setStatus("Reaction failed", true);
      }
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
      const response = await fetch(`/api/chat/tenor?q=${encodeURIComponent(q)}&limit=18`, { headers: authHeaders() });
      if (!response.ok) throw new Error("GIF search unavailable");
      const data = await response.json();
      el.tenorResults.replaceChildren();
      (data.results || []).forEach((gif) => {
        const button = document.createElement("button");
        button.type = "button";
        const image = document.createElement("img");
        image.src = gif.preview_url || gif.url;
        image.alt = gif.title || "Tenor GIF";
        button.appendChild(image);
        button.addEventListener("click", () => {
          pendingAttachments.push({ name: gif.title || "Tenor GIF", url: gif.url, type: "image/gif", size: 0 });
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

  emojis.forEach((emoji) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = emoji;
    button.title = emoji;
    button.addEventListener("click", () => insertEmoji(emoji));
    el.emojiPanel.appendChild(button);
  });

  el.discord.href = buildDiscordAuthUrl();
  el.name.value = getGuestName();
  updateIdentityView();
  resolveDiscordIdentity();

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

    if (tinodeConfig.enabled && typeof Tinode !== "undefined") {
      connectTinode();
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
  });
})();
