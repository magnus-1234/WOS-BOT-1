import re
with open(r"f:\Whiteout Survival Bot\frontend-dashboard\manage.html", "r", encoding="utf-8") as f:
    content = f.read()

track_use = """                showNotification('Reminder created!');
                if (typeof currentLoadedPresetId !== 'undefined' && currentLoadedPresetId) {
                    fetchData(`/api/reminders/presets/${currentLoadedPresetId}/track_use`, { method: 'POST' }).catch(e=>console.log(e));
                    currentLoadedPresetId = null;
                }"""

content = content.replace("showNotification('Reminder created!');", track_use)

with open(r"f:\Whiteout Survival Bot\frontend-dashboard\manage.html", "w", encoding="utf-8") as f:
    f.write(content)
