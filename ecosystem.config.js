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
    {
      name: "oracle-keepalive",
      script: "oracle_keepalive.py",
      interpreter: "python3",      // Windows: change to "python"
      cwd: "/home/ubuntu/bot",    // Oracle VM root path
      
      // ── Restart policy ──────────────────────────────────────────────────
      max_restarts: 5,             
      restart_delay: 10000,       
      min_uptime: "60s",          
      
      // ── Process behaviour ───────────────────────────────────────────────
      watch: false,
      autorestart: true,
      
      // ── Log files ───────────────────────────────────────────────────────
      out_file: "/home/ubuntu/bot/keepalive-out.log",
      error_file: "/home/ubuntu/bot/keepalive-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      merge_logs: false,
    },
    {
      name: "musicbot",
      script: "music_bot/bot.py",
      interpreter: "/home/ubuntu/bot/.venv/bin/python3",
      cwd: "/home/ubuntu/bot",

      max_restarts: 5,
      restart_delay: 30000,
      min_uptime: "60s",
      kill_timeout: 15000,
      max_memory_restart: "700M",

      watch: false,
      autorestart: true,

      env: {
        SKIP_INSTALL: "true",
        PYTHONUNBUFFERED: "1",
        // Prevent ~/.local packages from conflicting with venv packages
        PYTHONNOUSERSITE: "1",
        // NOTE: Secrets are set via `pm2 set` on the Oracle VM — not here.
        // Web control server settings (non-secret)
        MUSIC_WEB_SERVER_PORT: "8090",
        MUSIC_WEB_SERVER_HOST: "0.0.0.0",
        MUSIC_DEFAULT_VOLUME: "50",
        MUSIC_AUTO_RESUME: "false",
        LAVALINK_HOST: "lavalink.triniumhost.com",
        LAVALINK_PORT: "4333",
        LAVALINK_SECURE: "false",
        MONGO_DB_NAME: "reminderbot",
      },

      out_file: "/home/ubuntu/bot/musicbot-out.log",
      error_file: "/home/ubuntu/bot/musicbot-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      merge_logs: false,
    },
  ],
};
