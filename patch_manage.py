import re

with open(r"f:\Whiteout Survival Bot\frontend-dashboard\manage.html", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add filter dropdown to the preset header
filter_html = """                             <div style="position:relative; min-width:260px;">
                                 <select id="preset-filter" onchange="renderReminderPresets()" style="width:100%; padding:10px 14px; border-radius:8px; border:1px solid rgba(255,255,255,0.1); background:rgba(255,255,255,0.03); color:#fff; font-size:0.9rem; margin-bottom:10px; appearance:none;">
                                     <option value="all" style="background:#111">All Presets</option>
                                     <option value="mine" style="background:#111">My Presets</option>
                                     <option value="bookmarks" style="background:#111">Bookmarks</option>
                                 </select>"""
content = content.replace("""                             <div style="position:relative; min-width:260px;">""", filter_html, 1)

# 2. Update loadCommunityPresets to fetch bookmarks
old_load = """                    const res = await fetchData(`/api/reminders/presets${params}`); 
                    REMINDER_PRESETS = res.presets || []; 
                    renderReminderPresets();"""
new_load = """                    const [res, bkRes] = await Promise.all([
                        fetchData(`/api/reminders/presets${params}`),
                        fetchData(`/api/reminders/bookmarks`)
                    ]);
                    REMINDER_PRESETS = res.presets || []; 
                    BOOKMARKED_PRESETS = new Set(bkRes.bookmarks || []);
                    renderReminderPresets();"""

content = content.replace(old_load.replace('\r', ''), new_load)
content = re.sub(
    r"const res = await fetchData\(`/api/reminders/presets\$\{params\}`\);\s*REMINDER_PRESETS = res\.presets \|\| \[\];\s*renderReminderPresets\(\);",
    new_load,
    content
)

# 3. Update renderReminderPresets
new_render = """            if (grid) { 
                const filterVal = document.getElementById('preset-filter') ? document.getElementById('preset-filter').value : 'all';
                let displayedPresets = REMINDER_PRESETS;
                if (filterVal === 'mine') {
                    displayedPresets = REMINDER_PRESETS.filter(p => p.created_by_id === _discordUserId);
                } else if (filterVal === 'bookmarks') {
                    displayedPresets = REMINDER_PRESETS.filter(p => BOOKMARKED_PRESETS.has(p.id));
                }

                if (displayedPresets.length === 0) {"""
content = re.sub(r"if \(grid\) \{\s*if \(REMINDER_PRESETS\.length === 0\) \{", new_render, content)
content = content.replace("grid.innerHTML = REMINDER_PRESETS.map", "grid.innerHTML = displayedPresets.map")
content = content.replace("chips.innerHTML = REMINDER_PRESETS.slice", "chips.innerHTML = displayedPresets.slice")

# 4. Update the card HTML inside renderReminderPresets
new_card = """                                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                                    <button class="btn btn-secondary" onclick="openReminderModal(null, '${escapeAttribute(p.id)}')"> 
                                        <i data-lucide="copy-plus"></i> Use preset 
                                    </button> 
                                    <div style="display:flex; gap:6px; align-items:center;">
                                        <span title="Used ${p.use_count || 0} times" style="font-size:0.75rem; color:var(--text-muted); display:flex; align-items:center; gap:4px; margin-right:4px;">
                                            <i data-lucide="users" style="width:12px;height:12px;"></i> ${p.use_count || 0}
                                        </span>
                                        <button class="btn btn-icon ${BOOKMARKED_PRESETS.has(p.id) ? 'active-star' : ''}" onclick="toggleBookmark('${escapeAttribute(p.id)}')">
                                            <i data-lucide="star" style="width:16px;height:16px; ${BOOKMARKED_PRESETS.has(p.id) ? 'fill:#eab308;color:#eab308;' : ''}"></i>
                                        </button>
                                        ${p.created_by_id === _discordUserId ? `
                                            <button class="btn btn-icon" onclick="editCommunityPreset('${escapeAttribute(p.id)}')">
                                                <i data-lucide="edit-2" style="width:16px;height:16px;"></i>
                                            </button>
                                            <button class="btn btn-icon" style="color:#ef4444;" onclick="deleteCommunityPreset('${escapeAttribute(p.id)}')">
                                                <i data-lucide="trash-2" style="width:16px;height:16px;"></i>
                                            </button>
                                        ` : ''}
                                    </div>
                                </div>
                            </div> 
                        </div>"""
content = re.sub(
    r"<button class=\"btn btn-secondary\" onclick=\"openReminderModal\(null, '\$\{escapeAttribute\(p\.id\)\}'\)\">\s*<i data-lucide=\"copy-plus\"></i> Use preset\s*</button>\s*</div>\s*</div>",
    new_card,
    content
)

# 5. Functions to add
functions_to_add = """
        let BOOKMARKED_PRESETS = new Set();
        let currentLoadedPresetId = null;
        let editingPresetId = null;

        async function toggleBookmark(id) {
            const isBookmarked = BOOKMARKED_PRESETS.has(id);
            try {
                if (isBookmarked) {
                    await fetchData(`/api/reminders/bookmarks/${id}`, { method: 'DELETE' });
                    BOOKMARKED_PRESETS.delete(id);
                } else {
                    await fetchData(`/api/reminders/bookmarks/${id}`, { method: 'POST' });
                    BOOKMARKED_PRESETS.add(id);
                }
                renderReminderPresets();
            } catch(e) {
                showNotification('Failed to update bookmark', true);
            }
        }

        async function deleteCommunityPreset(id) {
            if (!confirm('Are you sure you want to delete this community preset?')) return;
            try {
                await fetchData(`/api/reminders/presets/${id}`, { method: 'DELETE' });
                showNotification('Preset deleted');
                loadCommunityPresets();
            } catch(e) {
                showNotification('Failed to delete preset', true);
            }
        }

        function editCommunityPreset(id) {
            const p = REMINDER_PRESETS.find(x => x.id === id);
            if (!p) return;
            editingPresetId = id;
            document.getElementById('cp-title').value = p.title || '';
            document.getElementById('cp-message').value = p.message || '';
            document.getElementById('cp-body').value = p.body || '';
            document.getElementById('cp-recurrence').value = p.recurrence_type || 'none';
            document.getElementById('cp-interval').value = p.recurrence_interval || '1';
            document.getElementById('cp-mention').value = p.mention || 'none';
            document.getElementById('cp-image').value = p.image_url || '';
            document.getElementById('cp-thumbnail').value = p.thumbnail_url || '';
            document.getElementById('cp-footer').value = p.footer_text || '';
            document.getElementById('cp-footer-icon').value = p.footer_icon_url || '';
            document.getElementById('create-preset-modal').style.display = 'flex';
            updatePresetPreview();
        }
        
        async function saveAsCommunityPreset(data) {
            const method = editingPresetId ? 'PUT' : 'POST';
            const url = editingPresetId ? `/api/reminders/presets/${editingPresetId}` : '/api/reminders/presets';
            try {
                await fetchData(url, { method: method, body: JSON.stringify(data) });
                showNotification(editingPresetId ? 'Preset updated!' : 'Preset published to community!');
                editingPresetId = null;
                closeCreatePresetModal();
                loadCommunityPresets();
            } catch(e) {
                showNotification('Failed to save preset', true);
            }
        }
"""
content = content.replace("let REMINDER_PRESETS = [];", functions_to_add + "\n        let REMINDER_PRESETS = [];")

# 6. Hijack openReminderModal to store loaded_preset_id
content = re.sub(
    r"function openReminderModal\(reminderId = null, presetId = null\) \{",
    "function openReminderModal(reminderId = null, presetId = null) {\n            currentLoadedPresetId = presetId;",
    content
)

# 7. Track use on createReminder success
track_use = """                showNotification('Reminder saved!');
                if (currentLoadedPresetId) {
                    fetchData(`/api/reminders/presets/${currentLoadedPresetId}/track_use`, { method: 'POST' }).catch(e=>console.log(e));
                    currentLoadedPresetId = null;
                }"""
content = content.replace("showNotification('Reminder saved!');", track_use)

# 8. Remove old saveAsCommunityPreset body so our new one takes precedence
content = re.sub(r"async function saveAsCommunityPreset\(data\) \{[\s\S]*?catch \(e\) \{\s*console\.error\(e\);\s*\}\s*\}", "", content, count=1)

with open(r"f:\Whiteout Survival Bot\frontend-dashboard\manage.html", "w", encoding="utf-8") as f:
    f.write(content)
