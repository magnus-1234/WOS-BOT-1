# Push Process

This workspace has three separate git repositories:

1. Root bot/API repo: `F:\Whiteout Survival Bot` (Wait, drive letter might be H on this machine, but the original text used F)
   - Remote: `origin` -> `https://github.com/magnus-1234/WOS-BOT-1.git`
   - Push with:
     `git push origin main`

2. Frontend repo: `F:\Whiteout Survival Bot\frontend-dashboard`
   - Remote: `origin` -> `https://github.com/magnus-1234/frontend-dashboard`
   - Push frontend changes from this folder with:
     `git push origin main`

3. Music Bot repo: `H:\MUSIC-DC-BOT`
   - Remote: `origin` -> `https://github.com/magnus-1234/MUSIC-DC-BOT.git`
   - Push music bot changes from this folder with:
     `git push origin main`

When frontend files are changed, always commit and push inside `frontend-dashboard` too. Root pushes do not publish frontend repo changes.
When music bot files are changed, always navigate to `H:\MUSIC-DC-BOT` and commit and push there. Root pushes do not publish music bot repo changes.
