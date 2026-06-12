
    let savedTheme = localStorage.getItem('theme') || 'cyberpunk-cool';
    if (savedTheme === 'viper') {
      savedTheme = 'cyberpunk-cool';
      localStorage.setItem('theme', 'cyberpunk-cool');
    }
    if (savedTheme && savedTheme !== 'dark') {
      document.documentElement.setAttribute('data-theme', savedTheme);
    }
  

    const API_BASE = '';

    // --- TAB SWITCHING ---
    function switchTab(tabId) {
      document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      
      document.getElementById(tabId).classList.add('active');
      document.querySelector(`[onclick="switchTab('${tabId}')"]`).classList.add('active');
    }

    // --- CUSTOM MODAL LOGIC ---
    let modalConfirmCallback = null;
    function openModal(title, desc, confirmBtnText, callback) {
      document.getElementById('modal-title').textContent = title;
      document.getElementById('modal-desc').textContent = desc;
      const confirmBtn = document.getElementById('modal-confirm-btn');
      confirmBtn.innerHTML = confirmBtnText;
      
      modalConfirmCallback = callback;
      document.getElementById('custom-modal').classList.add('active');
    }
    
    function closeModal() {
      document.getElementById('custom-modal').classList.remove('active');
      modalConfirmCallback = null;
    }
    
    document.getElementById('modal-confirm-btn').addEventListener('click', () => {
      if (modalConfirmCallback) modalConfirmCallback();
      closeModal();
    });

    const token = localStorage.getItem('discord_access_token');
    let state = { servers: [], admins: [], pending: [], registrationLimits: [], stats: {}, settingsCatalog: [], chatAdmin: null };
    let selectedGuildId = null;

    function authHeaders(json = true) {
      const headers = { Authorization: `Bearer ${token}` };
      if (json) headers['Content-Type'] = 'application/json';
      return headers;
    }

    async function api(endpoint, options = {}) {
      if (!token) {
        window.location.href = 'login.html';
        return;
      }
      const response = await fetch(API_BASE + endpoint, {
        ...options,
        headers: { ...authHeaders(options.body !== undefined), ...(options.headers || {}) }
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
      return data;
    }

    function escapeHTML(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
      }[ch]));
    }

    function toast(message, error = false) {
      const el = document.getElementById('toast');
      el.textContent = message;
      el.className = `toast show${error ? ' error' : ''}`;
      setTimeout(() => el.classList.remove('show'), 3200);
    }

    function logout() {
      localStorage.removeItem('discord_access_token');
      window.location.href = 'index.html';
    }

    async function refreshAll() {
      try {
        const me = await api('/api/admin/me');
        if (!me.is_global_admin) {
          document.querySelector('.shell').innerHTML = '<section class="panel hero-main"><h1>Access restricted</h1><p>This panel is visible only to global administrators. Your Discord session is valid, but it does not have global admin rights.</p><div class="actions"><a class="btn" href="dashboard.html">Back to Dashboard</a></div></section>';
          return;
        }
        const overview = await api('/api/admin/overview');
        state = {
          servers: overview.servers || [],
          admins: overview.admins || [],
          pending: overview.pending || [],
          registrationLimits: overview.registration_user_limits || [],
          stats: overview.stats || {},
          settingsCatalog: overview.settings_catalog || []
        };
        selectedGuildId = selectedGuildId || (state.servers[0] && state.servers[0].id);
        renderStats();
        renderServers();
        renderSelected();
        renderPending();
        renderRegistrationLimits();
        renderAdmins();
        loadChatAdmin(false);
      } catch (err) {
        toast(err.message, true);
      } finally {
        lucide.createIcons();
      }
    }

    function renderStats() {
      const stats = [
        [state.stats.servers || 0, 'Servers'],
        [state.stats.members || 0, 'Members'],
        [state.stats.custom_limits || 0, 'Custom limits'],
        [state.stats.pending_registrations || 0, 'Pending reviews']
      ];
      document.getElementById('stats').innerHTML = stats.map(([num, label]) => `<div class="stat"><b>${Number(num).toLocaleString()}</b><span>${label}</span></div>`).join('');
    }

    function serverMatchesFilter(server) {
      const filter = document.getElementById('server-filter').value;
      const limits = server.limits || {};
      const lock = server.lock || {};
      if (filter === 'locked') return !!lock.locked;
      if (filter === 'feature') return !!lock.feature_locked;
      if (filter === 'limits') return Object.keys(limits).length > 0;
      if (filter === 'monitor') return !!limits.alliance_monitor_locked;
      return true;
    }

    function renderServers() {
      const q = document.getElementById('server-search').value.trim().toLowerCase();
      const list = document.getElementById('server-list');
      const servers = state.servers.filter(s => {
        const text = `${s.name} ${s.id}`.toLowerCase();
        return (!q || text.includes(q)) && serverMatchesFilter(s);
      });
      document.getElementById('server-count').textContent = `${servers.length} shown`;
      if (!servers.length) {
        list.innerHTML = '<div class="empty">No servers match the current filter.</div>';
        return;
      }
      list.innerHTML = servers.map(server => {
        const limits = server.limits || {};
        const lock = server.lock || {};
        const cap = limits.max_auto_redeem_members ?? 'default';
        const icon = server.icon_url || 'https://cdn.discordapp.com/embed/avatars/0.png';
        return `
          <div class="server-row" data-id="${escapeHTML(server.id)}">
            <img src="${escapeHTML(icon)}" alt="">
            <div>
              <div class="server-name">${escapeHTML(server.name)}</div>
              <div class="server-meta">
                <span class="pill">${Number(server.member_count || 0).toLocaleString()} members</span>
                <span class="pill">Cap: ${escapeHTML(cap)}</span>
                ${limits.alliance_monitor_locked ? '<span class="pill warn">Monitor locked</span>' : '<span class="pill ok">Monitor open</span>'}
                ${lock.locked ? '<span class="pill danger">Bot locked</span>' : ''}
                ${lock.feature_locked ? '<span class="pill warn">Feature locked</span>' : ''}
              </div>
            </div>
            <div class="row-actions">
              <button class="btn small ${selectedGuildId === server.id ? 'primary' : ''}" onclick="selectServer('${escapeHTML(server.id)}')">Select</button>
              <a class="btn small" href="manage.html?id=${encodeURIComponent(server.id)}">Manage</a>
            </div>
          </div>
        `;
      }).join('');
      lucide.createIcons();
    }

    function selectServer(guildId) {
      selectedGuildId = guildId;
      renderServers();
      renderSelected();
    }

    function selectedServer() {
      return state.servers.find(s => s.id === selectedGuildId) || null;
    }

    function renderSelected() {
      const server = selectedServer();
      const links = document.getElementById('feature-links');
      if (!server) {
        document.getElementById('selected-name').textContent = 'No server selected';
        links.innerHTML = '<div class="empty">Select a server to open feature editors.</div>';
        return;
      }
      document.getElementById('selected-name').textContent = server.name;
      const limits = server.limits || {};
      const lock = server.lock || {};
      document.getElementById('limit-max').value = limits.max_auto_redeem_members ?? -1;
      document.getElementById('limit-monitor').checked = !!limits.alliance_monitor_locked;
      document.getElementById('lock-bot').checked = !!lock.locked;
      document.getElementById('lock-feature').checked = !!lock.feature_locked;
      syncLockControls();
      const tabs = ['overview', 'welcome', 'alliance', 'translate', 'birthday', 'giftcodes', 'reminders'];
      links.innerHTML = tabs.map(tab => {
        const label = tab === 'overview' ? 'Overview' : tab.replace(/^\w/, c => c.toUpperCase());
        return `<a class="btn" href="manage.html?id=${encodeURIComponent(server.id)}&tab=${encodeURIComponent(tab)}">${escapeHTML(label)}</a>`;
      }).join('');
    }

    function syncLockControls() {
      const botLock = document.getElementById('lock-bot').checked;
      const feature = document.getElementById('lock-feature');
      feature.disabled = botLock;
      if (botLock) feature.checked = false;
    }

    function applyQuickLimit() {
      const value = document.getElementById('quick-limit').value;
      if (value !== '') document.getElementById('limit-max').value = value;
    }

    async function saveSelectedLimits() {
      if (!selectedGuildId) return;
      try {
        await api(`/api/admin/servers/${selectedGuildId}/limits`, {
          method: 'POST',
          body: JSON.stringify({
            max_auto_redeem_members: Number(document.getElementById('limit-max').value),
            alliance_monitor_locked: document.getElementById('limit-monitor').checked
          })
        });
        toast('Limits saved');
        await refreshAll();
      } catch (err) {
        toast(err.message, true);
      }
    }

    async function resetSelectedLimits() {
      if (!selectedGuildId) return;
      openModal('Reset Limits', 'Are you sure you want to reset custom limits for this server?', '<i data-lucide=\"rotate-ccw\"></i> Reset', async () => {
      try {
        await api(`/api/admin/servers/${selectedGuildId}/limits`, { method: 'DELETE' });
        toast('Limits reset');
        await refreshAll();
      } catch (err) {
        toast(err.message, true);
      }
      });
    }

    async function saveSelectedLock() {
      if (!selectedGuildId) return;
      try {
        await api(`/api/admin/servers/${selectedGuildId}/lock`, {
          method: 'POST',
          body: JSON.stringify({
            locked: document.getElementById('lock-bot').checked,
            feature_locked: document.getElementById('lock-feature').checked
          })
        });
        toast('Lock state saved');
        await refreshAll();
      } catch (err) {
        toast(err.message, true);
      }
    }

    function renderPending() {
      const list = document.getElementById('pending-list');
      document.getElementById('pending-count').textContent = `${state.pending.length} pending`;
      if (!state.pending.length) {
        list.innerHTML = '<div class="empty">No pending registrations.</div>';
        return;
      }
      list.innerHTML = state.pending.map(item => `
        <div class="table-row">
          <div>
            <strong>${escapeHTML(item.guild_name || item.guild_id)}</strong>
            <div class="muted">Alliance: ${escapeHTML(item.alliance_name)} (State: ${escapeHTML(item.state || 'N/A')}) | Requested by ${escapeHTML(item.discord_username)} (${escapeHTML(item.discord_user_id)})</div>
          </div>
          <div class="row-actions">
            <button class="btn small success" onclick="reviewRegistration('${escapeHTML(item.guild_id)}','approve')">Approve</button>
            <button class="btn small danger" onclick="reviewRegistration('${escapeHTML(item.guild_id)}','deny')">Deny</button>
          </div>
        </div>
      `).join('');
    }

    async function reviewRegistration(guildId, action) {
      try {
        await api(`/api/admin/registrations/${guildId}/review`, {
          method: 'POST',
          body: JSON.stringify({ action })
        });
        toast(`Registration ${action}d`);
        await refreshAll();
      } catch (err) {
        toast(err.message, true);
      }
    }

    function renderRegistrationLimits() {
      const list = document.getElementById('registration-limit-list');
      const items = state.registrationLimits || [];
      document.getElementById('registration-limit-count').textContent = `${items.length} custom`;
      if (!items.length) {
        list.innerHTML = '<div class="empty">No custom user registration limits.</div>';
        return;
      }
      list.innerHTML = items.map(item => `
        <div class="table-row">
          <div>
            <strong>${escapeHTML(item.discord_user_id)}</strong>
            <div class="muted">Allowed servers: ${escapeHTML(item.max_servers || 1)}</div>
          </div>
          <div class="row-actions">
            <button class="btn small" onclick="fillRegistrationLimit('${escapeHTML(item.discord_user_id)}', ${Number(item.max_servers || 1)})">Edit</button>
            <button class="btn small danger" onclick="resetRegistrationLimit('${escapeHTML(item.discord_user_id)}')">Reset</button>
          </div>
        </div>
      `).join('');
    }

    function fillRegistrationLimit(userId, maxServers) {
      document.getElementById('registration-limit-user-id').value = userId;
      document.getElementById('registration-limit-max').value = maxServers;
    }

    async function saveRegistrationLimit() {
      const userId = document.getElementById('registration-limit-user-id').value.trim();
      const maxServers = Number(document.getElementById('registration-limit-max').value);
      if (!/^\d{3,32}$/.test(userId)) {
        toast('Enter a valid Discord user ID', true);
        return;
      }
      if (!Number.isInteger(maxServers) || maxServers < 1) {
        toast('Allowed servers must be at least 1', true);
        return;
      }
      try {
        await api(`/api/admin/registration-limits/${userId}`, {
          method: 'POST',
          body: JSON.stringify({ max_servers: maxServers })
        });
        document.getElementById('registration-limit-user-id').value = '';
        document.getElementById('registration-limit-max').value = 1;
        toast('Registration limit saved');
        await refreshAll();
      } catch (err) {
        toast(err.message, true);
      }
    }

    async function resetRegistrationLimit(userId) {
      openModal('Reset Registration Limit', 'Reset this user back to the default 1 server limit?', '<i data-lucide=\"rotate-ccw\"></i> Reset', async () => {
      try {
        await api(`/api/admin/registration-limits/${userId}`, { method: 'DELETE' });
        toast('Registration limit reset');
        await refreshAll();
      } catch (err) {
        toast(err.message, true);
      }
      });
    }

    function formatChatTime(value) {
      if (!value) return '';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return '';
      return date.toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    }

    async function loadChatAdmin(showErrors = true) {
      const status = document.getElementById('chat-admin-status');
      try {
        status.textContent = 'Loading...';
        const data = await api('/api/chat/admin/state');
        state.chatAdmin = data;
        renderChatAdmin();
      } catch (err) {
        status.textContent = 'Unavailable';
        document.getElementById('chat-message-list').innerHTML = '<div class="empty">Community chat controls are unavailable for this session.</div>';
        if (showErrors) toast(err.message, true);
      } finally {
        lucide.createIcons();
      }
    }

    function renderChatAdmin() {
      const data = state.chatAdmin || {};
      const room = data.room_state || {};
      const messages = data.messages || [];
      const banned = room.banned_user_ids || [];
      const activeAnnouncement = room.announcement || '';
      document.getElementById('chat-admin-status').textContent = `${Number(data.online_count || 0).toLocaleString()} online`;
      document.getElementById('chat-announcement').value = activeAnnouncement;
      document.getElementById('chat-freeze-btn').classList.toggle('danger', !!room.is_blizzard_active);
      document.getElementById('chat-admin-stats').innerHTML = [
        [data.online_count || 0, 'Online users'],
        [banned.length || 0, 'Banned users'],
        [room.is_blizzard_active ? 'Paused' : 'Live', 'Message posting'],
        [activeAnnouncement ? 'Active' : 'None', 'Megaphone strip']
      ].map(([num, label]) => `<div class="stat"><b>${escapeHTML(num)}</b><span>${escapeHTML(label)}</span></div>`).join('');
      renderChatMessages(messages);
    }

    function renderChatMessages(messages) {
      const list = document.getElementById('chat-message-list');
      if (!messages.length) {
        list.innerHTML = '<div class="empty">No community chat messages found.</div>';
        return;
      }
      list.innerHTML = messages.slice().reverse().map(message => {
        const author = message.author || {};
        const authorId = author.id || '';
        const authorName = author.name || author.username || authorId || 'Player';
        const source = message.source === 'announcement' ? 'Announcement' : (author.kind || message.source || 'chat');
        const canBan = authorId && !['announcement', 'wos_bot'].includes(authorId);
        return `
          <div class="chat-message-row">
            <div>
              <div class="chat-message-meta">
                <span class="pill">${escapeHTML(authorName)}</span>
                <span class="pill">${escapeHTML(authorId || 'no id')}</span>
                <span class="pill">${escapeHTML(source)}</span>
                <span class="muted">${escapeHTML(formatChatTime(message.created_at))}</span>
              </div>
              <div class="chat-message-text">${escapeHTML(message.content || '[attachment]')}</div>
            </div>
            <div class="row-actions">
              ${canBan ? `<button class="btn small danger" onclick="banChatAuthor('${escapeHTML(authorId)}')"><i data-lucide="ban"></i> Ban</button>` : ''}
              <button class="btn small danger" onclick="deleteChatMessage('${escapeHTML(message.id)}')"><i data-lucide="trash-2"></i> Delete</button>
            </div>
          </div>
        `;
      }).join('');
    }

    async function saveChatAnnouncement() {
      const announcement = document.getElementById('chat-announcement').value.trim();
      if (!announcement) {
        toast('Announcement text is required', true);
        return;
      }
      try {
        await api('/api/chat/admin/announcement', {
          method: 'POST',
          body: JSON.stringify({ announcement })
        });
        toast('Announcement sent');
        await loadChatAdmin(false);
      } catch (err) {
        toast(err.message, true);
      }
    }

    async function clearChatAnnouncement() {
      try {
        await api('/api/chat/admin/announcement', {
          method: 'POST',
          body: JSON.stringify({ announcement: '' })
        });
        toast('Megaphone strip cleared');
        await loadChatAdmin(false);
      } catch (err) {
        toast(err.message, true);
      }
    }

    async function toggleChatFreeze() {
      const isFrozen = !!(state.chatAdmin && state.chatAdmin.room_state && state.chatAdmin.room_state.is_blizzard_active);
      try {
        await api('/api/chat/admin/blizzard', {
          method: 'POST',
          body: JSON.stringify({ is_frozen: !isFrozen })
        });
        toast(!isFrozen ? 'Community chat paused' : 'Community chat resumed');
        await loadChatAdmin(false);
      } catch (err) {
        toast(err.message, true);
      }
    }

    async function saveChatBan() {
      const userId = document.getElementById('chat-ban-user-id').value.trim();
      const action = document.getElementById('chat-ban-action').value;
      if (!userId) {
        toast('Enter a user ID', true);
        return;
      }
      try {
        await api(`/api/chat/admin/${action}`, {
          method: 'POST',
          body: JSON.stringify({ user_id: userId })
        });
        document.getElementById('chat-ban-user-id').value = '';
        toast(action === 'ban' ? 'User banned from community chat' : 'User unbanned');
        await loadChatAdmin(false);
      } catch (err) {
        toast(err.message, true);
      }
    }

    function banChatAuthor(userId) {
      document.getElementById('chat-ban-user-id').value = userId;
      document.getElementById('chat-ban-action').value = 'ban';
      saveChatBan();
    }

    async function deleteChatMessage(messageId) {
      openModal('Delete Message', 'Are you sure you want to delete this community chat message?', '<i data-lucide=\"trash-2\"></i> Delete', async () => {
      try {
        await api(`/api/chat/messages/${encodeURIComponent(messageId)}`, { method: 'DELETE' });
        toast('Message deleted');
        await loadChatAdmin(false);
      } catch (err) {
        toast(err.message, true);
      }
      });
    }

    async function clearChatMessages() {
      openModal('Clear Chat', 'Are you sure you want to clear all community chat messages? This action cannot be undone.', '<i data-lucide=\"trash-2\"></i> Clear All', async () => {
      try {
        await api('/api/chat/admin/clear', { method: 'POST' });
        toast('Community chat cleared');
        await loadChatAdmin(false);
      } catch (err) {
        toast(err.message, true);
      }
      });
    }

    function renderAdmins() {
      const list = document.getElementById('admin-list');
      document.getElementById('admin-count').textContent = `${state.admins.length} admins`;
      if (!state.admins.length) {
        list.innerHTML = '<div class="empty">No administrators found.</div>';
        return;
      }
      list.innerHTML = state.admins.map(admin => `
        <div class="table-row">
          <div>
            <strong>${escapeHTML(admin.id)}</strong>
            <div class="muted">${admin.is_global ? 'Global administrator' : 'Server admin'} | ${escapeHTML(admin.source)}</div>
          </div>
          <span class="pill ${admin.is_global ? 'ok' : ''}">${admin.is_global ? 'Global' : 'Server'}</span>
        </div>
      `).join('');
    }

    async function saveAdmin() {
      const userId = document.getElementById('admin-user-id').value.trim();
      if (!/^\d{3,32}$/.test(userId)) {
        toast('Enter a valid Discord user ID', true);
        return;
      }
      try {
        await api('/api/admin/admins', {
          method: 'POST',
          body: JSON.stringify({
            user_id: userId,
            is_global: document.getElementById('admin-global').value === 'true'
          })
        });
        document.getElementById('admin-user-id').value = '';
        toast('Administrator saved');
        await refreshAll();
      } catch (err) {
        toast(err.message, true);
      }
    }

    window.addEventListener('DOMContentLoaded', () => {
      refreshAll();
      lucide.createIcons();

      // Theme toggle logic
      const themeToggle = document.getElementById("theme-toggle");
      if (themeToggle) {
        const themes = ['cartoon', 'dark', 'light', 'high-contrast', 'hacker', 'aurora', 'cyberpunk-cool'];
        const themeLabels = { cartoon: 'Cartoon Theme', dark: 'Dark Theme', light: 'Light Theme', 'high-contrast': 'High Contrast Theme', hacker: 'Hacker Theme', aurora: 'Aurora Theme', 'cyberpunk-cool': 'Main Theme' };
        const setThemeLabel = (theme) => {
          const label = themeLabels[theme] || themeLabels['cyberpunk-cool'];
          themeToggle.setAttribute('aria-label', `Current theme: ${label}. Change theme`);
          themeToggle.title = label;
        };
        setThemeLabel(localStorage.getItem("theme") || "cyberpunk-cool");
        themeToggle.addEventListener("click", () => {
          const currentTheme = localStorage.getItem("theme") || "cyberpunk-cool";
          const currentIndex = themes.indexOf(currentTheme);
          const nextIndex = ((currentIndex === -1 ? 0 : currentIndex) + 1) % themes.length;
          const nextTheme = themes[nextIndex];

          localStorage.setItem("theme", nextTheme);
          if (nextTheme === "dark") {
            document.documentElement.removeAttribute("data-theme");
          } else {
            document.documentElement.setAttribute("data-theme", nextTheme);
          }
          setThemeLabel(nextTheme);
        });
      }
    });
  
