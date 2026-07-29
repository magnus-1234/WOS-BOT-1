// PM2 Ecosystem Configuration — Oracle VM
// Usage:
//   pm2 start ecosystem.config.js
//   pm2 save
//   pm2 startup   # auto-start on VM reboot
//
// Key settings preventing the outage restart loop:
//   max_restarts  — 15 restarts before stopping (transient Discord gateway drops
//                   count as restarts; 5 was too low and exhausted quickly)
//   cron_restart  — resets the restart counter daily at 04:00 UTC (quiet period)
//   restart_delay — 10 s cool-down between restarts (fast recovery)
//   kill_timeout  — 15 s for graceful shutdown before SIGKILL
//                   (prevents "ExtensionAlreadyLoaded" duplicate-cog errors)
//   max_memory_restart — 700M (VM total RAM is ~956MB, bot uses ~384MB;
//                        stays below the OOM threshold)
//   watch: false  — never restart on file changes

module.exports = {
  apps: [
    {
      name: "discordbot",
      script: "app.py",
      interpreter: "python3",      // Windows: change to "python"
      cwd: "/home/ubuntu/bot",    // Oracle VM root path (where this app.py lives)

      // ── Restart policy ──────────────────────────────────────────────────
      max_restarts: 15,            // Discord gateway drops count as restarts; 5 was too low
      restart_delay: 10000,        // 10 s cool-down between restarts
      min_uptime: "60s",           // runs < 60 s count as crashes
      kill_timeout: 15000,         // 15 s graceful shutdown before SIGKILL
      max_memory_restart: "700M",  // VM has ~956MB total; trigger before OS OOM killer
      cron_restart: "0 4 * * *",   // reset restart counter daily at 04:00 UTC

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
      interpreter: "python3",
      cwd: "/home/ubuntu/bot",

      // ── Restart policy ──────────────────────────────────────────────────
      max_restarts: 20,            // watchdog must stay up at all times
      restart_delay: 5000,         // quick restart if watchdog itself crashes
      min_uptime: "30s",

      // ── Process behaviour ───────────────────────────────────────────────
      watch: false,
      autorestart: true,

      // ── Log files ───────────────────────────────────────────────────────
      out_file: "/home/ubuntu/bot/keepalive-out.log",
      error_file: "/home/ubuntu/bot/keepalive-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      merge_logs: false,
    },
  ],
};
