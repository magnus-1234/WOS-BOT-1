// PM2 Ecosystem Configuration — Oracle VM
// Usage:
//   pm2 start ecosystem.config.js
//   pm2 save
//   pm2 startup   # auto-start on VM reboot
//
// Key settings preventing the outage restart loop:
//   max_restarts  — stop respawning after 5 crashes (prevents infinite crash loop)
//   restart_delay — 30 s cool-down between restarts
//   kill_timeout  — 15 s for graceful shutdown before SIGKILL
//                   (prevents "ExtensionAlreadyLoaded" duplicate-cog errors)
//   watch: false  — never restart on file changes

module.exports = {
  apps: [
    {
      name: "discordbot",
      script: "app.py",
      interpreter: "python3",      // Windows: change to "python"
      cwd: "/home/ubuntu/bot",    // Oracle VM root path (where this app.py lives)

      // ── Restart policy ──────────────────────────────────────────────────
      max_restarts: 5,             // hard-stop loop after 5 consecutive crashes
      restart_delay: 30000,       // 30 s cool-down between restarts
      min_uptime: "60s",          // runs < 60 s count as crashes
      kill_timeout: 15000,        // 15 s graceful shutdown before SIGKILL
      max_memory_restart: "1G", // increased from 300M as bot uses ~325MB+ due to ML models

      // ── Process behaviour ───────────────────────────────────────────────
      watch: false,
      autorestart: true,

      // ── Environment variables ───────────────────────────────────────────
      env: {
        // Skip pip install on every start — removes the 1-3 min startup gap.
        // Set to false only after updating requirements.txt.
        SKIP_INSTALL: "true",

        // Oracle VM: 8080 is fine (no Windows firewall restriction)
        PORT: "8080",

        PYTHONUNBUFFERED: "1",
      },

      // ── Log files ───────────────────────────────────────────────────────
      out_file: "/home/ubuntu/bot/discordbot-out.log",
      error_file: "/home/ubuntu/bot/discordbot-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      merge_logs: false,
    },
  ],
};
