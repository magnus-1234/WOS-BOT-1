---
description: Push all changes to GitHub and auto-deploy to Oracle VM
---

# Push & Deploy Workflow

This workflow MUST be run automatically after every session where code changes are made to the bot or frontend — no user prompt needed. When the user says "make changes", "fix X", "add Y", etc., run this at the end.

## Pre-conditions
- Working directory: `f:\Whiteout Survival Bot`
- Main Bot git remote: `origin` → `https://github.com/magnus-1234/WOS-BOT-1.git`
- Oracle VM SSH target: `ubuntu@140.245.241.54`, repo path `~/bot`
- Oracle VM SSH key: `C:\Users\mohit\.ssh\oracle_vm_key`
- Do not use `git push oracle main`. The Oracle deploy must happen by SSH pull/restart with `C:\Users\mohit\.ssh\oracle_vm_key`. A plain push to the `oracle` git remote can fail with `Permission denied (publickey)` or be rejected because the branch is checked out.
- Frontend Dashboard git remote: `origin` → `https://github.com/magnus-1234/frontend-dashboard.git`
- GitHub Actions auto-deploys to Oracle VM on every push to `main`
- If files under `frontend-dashboard/` changed, there are two repositories to update: the nested frontend repo and the parent bot repo. Commit and push both.

---

## Steps

### 1. Check git status for both repos
// turbo
```powershell
cd "f:\Whiteout Survival Bot"; git status
if (Test-Path "f:\Whiteout Survival Bot\frontend-dashboard\.git") {
    cd "f:\Whiteout Survival Bot\frontend-dashboard"; git status
}
```

### 2. Stage only the relevant changes in both repos
// turbo
```powershell
if (Test-Path "f:\Whiteout Survival Bot\frontend-dashboard\.git") {
    cd "f:\Whiteout Survival Bot\frontend-dashboard"
    git add <changed frontend files>
}
cd "f:\Whiteout Survival Bot"
git add <changed parent repo files>
```

### 3. Commit with a descriptive message (summarize what changed)
Use a commit message that describes the actual change made:
```powershell
# Commit dashboard first when frontend-dashboard changed
if (Test-Path "f:\Whiteout Survival Bot\frontend-dashboard\.git") {
    cd "f:\Whiteout Survival Bot\frontend-dashboard"
    git commit -m "<description>"
}

# Then commit the parent bot repo
cd "f:\Whiteout Survival Bot"
git commit -m "<description>"
```
If nothing to commit (clean tree), skip steps 3 and 4.

### 4. Push to GitHub for both repos (triggers auto-deploy to Oracle VM)
// turbo
```powershell
# Push frontend-dashboard first when it changed
if (Test-Path "f:\Whiteout Survival Bot\frontend-dashboard\.git") {
    cd "f:\Whiteout Survival Bot\frontend-dashboard" && git push origin main
}

# Push main bot repo after the nested frontend push
cd "f:\Whiteout Survival Bot" && git push origin main
```

### 5. Deploy to Oracle VM via SSH (required after every bot push)
Bypasses the 2-minute GitHub Actions wait by pulling the pushed GitHub commit on the VM and restarting PM2. This is required whenever bot code changes are pushed.
// turbo
```powershell
ssh -i "C:\Users\mohit\.ssh\oracle_vm_key" -o StrictHostKeyChecking=no ubuntu@140.245.241.54 "cd bot && git pull && pm2 restart discordbot"
```

If the frontend dashboard also changed, deploy both repositories:
```powershell
ssh -i "C:\Users\mohit\.ssh\oracle_vm_key" -o StrictHostKeyChecking=no ubuntu@140.245.241.54 "cd bot && git pull && cd frontend-dashboard && git pull && pm2 restart discordbot"
```

If SSH deploy fails:
- Do not claim Oracle deploy succeeded.
- Report the exact failure, especially `Permission denied (publickey)`.
- GitHub pushes are still valid if `git push origin main` succeeded.
- The next fix is to restore/use the key at `C:\Users\mohit\.ssh\oracle_vm_key` or configure the current machine's SSH agent for `ubuntu@140.245.241.54`.

### 6. Confirm Deployment
After a successful SSH restart:
- ✅ Changes pushed to GitHub (both Main Bot and Frontend Dashboard)
- 🚀 Instant SSH Deploy successful (both repositories pulled on VM)
- 🔄 Bot restarted on Oracle VM (Ubuntu)
- The bot and frontend dashboard are now LIVE with the new changes!

After a partial push where Oracle SSH failed:
- Confirm which GitHub repos/branches were pushed.
- State that Oracle VM deploy did not complete.
- State that the working tree is clean or list remaining files.
