# AI Agent Behavior Rules & Directives
*This file defines the behavior, design constraints, and rules for all AI Coding Assistants (e.g., Antigravity, Cursor, Cline, Claude) working in this workspace.*

---

## 🚨 ACTIVE AGENT RULES (USER-CONFIGURED)

> [!IMPORTANT]
> **RULE 1: SCOPE OF DESIGN & THEME CHANGES**
> - **Primary Styling Focus**: The **Cyberpunk Theme** (`data-theme="cyberpunk-cool"`) is the flagship theme for styling and design enhancements.
> - **Scoping Rule**: Only apply design, layout, color, visual polish, animations, and micro-interaction changes to the **Cyberpunk theme**. Do NOT alter the visual design, aesthetics, or styles of other themes (`light`, `high-contrast`, `hacker`, `cartoon`) unless the user explicitly requests it.
>
> **RULE 2: DASHBOARD & ADMIN PAGES FUNCTIONALITY**
> - **Global Integration**: Any functional changes, logic updates, form submissions, settings, state management, or backend API integrations made to dashboard pages (`dashboard.html`, `manage.html`) or administrative tools (`admin.html`) **MUST be applied and supported across ALL themes**.
> - **Theme-Agnostic Code**: Ensure Javascript logic and data-binding do not assume a specific theme, and that CSS changes for layout or interactive controls use theme variables (e.g. `--primary`, `--glass-bg`, `--text-main`) so they remain fully responsive and beautifully styled on every theme.

---

## 🎨 Design & Styling Guidelines (Cyberpunk Theme)

To maintain a professional, state-of-the-art visual appearance, all changes to the Cyberpunk Theme must adhere to the following design system:

### 1. Color Palette (Cyberpunk Cool)
- **Primary**: Indigo/Violet accent (`#6366f1` / `#8b5cf6`) with smooth gradients.
- **Background**: Deep cyber-space dark (`#030712` or `#0f1115`).
- **Cards/Panels**: Translucent glass (`rgba(17, 24, 39, 0.7)` with border `rgba(255, 255, 255, 0.05)`).
- **Text Primary**: Crisp off-white (`#f9fafb`).
- **Text Muted**: Soft grey (`#9ca3af`).

### 2. Aesthetics & UI Details
- **Glassmorphism**: Use `backdrop-filter: blur(8px)` with thin borders and subtle shadows for that premium, floating cyber-card effect.
- **Micro-Animations**: Add brief, responsive transition curves (`transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1)`) on hovers, card interactions, and button presses.
- **Typography**: Utilize Google Fonts `Outfit` for headers and `Inter` for body copy. Avoid standard browser system fonts.

---

## 🛠️ Operational & Git Workflows

### 1. Automatic Version Control & Deployment
At the end of any session containing code modifications, the Agent must trigger the deployment process:
1. Stage changes in both repositories (`WOS-BOT-1` and `frontend-dashboard`).
2. Commit with professional, concise, semantic messages (e.g., `feat: optimized cyberpunk theme charts`, `fix: server settings state handler`).
3. Push to `main` for both repositories.
4. Execute the SSH deployment command to instantly apply updates on the Oracle VM:
   ```powershell
   ssh -i "C:\Users\mohit\.ssh\oracle_vm_key" -o StrictHostKeyChecking=no ubuntu@140.245.241.54 "cd bot && git pull && cd frontend-dashboard && git pull && pm2 restart discordbot"
   ```

### 2. Code Quality & Formatting
- **No Placeholders**: Never write TODOs or simple placeholder blocks in user-facing code. Deliver complete, working features.
- **Semantic HTML**: Use clean, modern semantic HTML tags (`<header>`, `<nav>`, `<main>`, `<section>`, `<aside>`).
- **CSS Hierarchy**: Organize rules cleanly, avoid unnecessary `!important` flags, and leverage CSS custom properties (variables) for theme compatibility.
