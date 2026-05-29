# Push Process

This workspace has two separate git repositories:

1. Root bot/API repo: `F:\Whiteout Survival Bot`
   - Remote: `origin` -> `https://github.com/magnus-1234/WOS-BOT-1.git`
   - Push with:
     `git push origin main`

2. Frontend repo: `F:\Whiteout Survival Bot\frontend-dashboard`
   - Remote: `origin` -> `https://github.com/magnus-1234/frontend-dashboard`
   - Push frontend changes from this folder with:
     `git push origin main`

When frontend files are changed, always commit and push inside `frontend-dashboard` too. Root pushes do not publish frontend repo changes.
