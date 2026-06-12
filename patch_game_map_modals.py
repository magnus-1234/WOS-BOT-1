import re

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "r", encoding="utf-8") as f:
    content = f.read()

# Add imports for URL stuff
import_search = r'import \{ useCallback, useEffect, useRef, useState \} from "react";'
import_replace = """import { useCallback, useEffect, useRef, useState } from "react";
import Icon from "../Icon"; // Assuming Icon exists, otherwise we'll just use text. We'll use text to be safe."""
content = re.sub(import_search, import_replace, content)

# Add load/save logic and Modals
modal_search = r'(<div className="wos-game-map-viewport".*?>)'
modal_replace = r"""{shareOpen && (
        <div className="modal-backdrop" onClick={() => setShareOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <section className="share-modal foundry-share-modal" onClick={e => e.stopPropagation()} style={{ background: "rgba(20, 30, 40, 0.95)", padding: 24, borderRadius: 12, width: 400, maxWidth: "90%" }}>
            <h2 style={{ color: "#fff", marginTop: 0 }}>Share Game Map Plan</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <select value={shareAccess} onChange={e => setShareAccess(e.target.value as "editable" | "view-only")} disabled={isSavingShare} style={{ padding: 8, background: "#000", color: "#fff", border: "1px solid #444", borderRadius: 4 }}>
                <option value="editable">Editable link (Anyone with link can edit)</option>
                <option value="view-only">View-only link (Anyone with link can only view)</option>
              </select>
              <button 
                onClick={async () => {
                  if (!authUser) return alert("Sign in required");
                  setIsSavingShare(true);
                  try {
                    const payload = JSON.stringify({ assignments, camera, zoom });
                    const res = await fetch("/api/game-map-planner", {
                      method: "POST",
                      credentials: "include",
                      headers: { "Content-Type": "application/json", "x-user-id": authUser.id },
                      body: JSON.stringify({ payload, access: shareAccess })
                    });
                    const data = await res.json();
                    if (data.id) {
                      setShareId(data.id);
                      setShareOpen(false);
                      alert(`Plan Shared! Link: ${window.location.origin}/game-map?gameMapId=${data.id}`);
                    } else alert(data.error || "Failed to share");
                  } catch (e) {
                    alert("Error saving");
                  } finally { setIsSavingShare(false); }
                }}
                disabled={isSavingShare}
                style={{ background: "#3b82f6", color: "#fff", padding: "10px", border: "none", borderRadius: 4, cursor: "pointer" }}
              >
                {isSavingShare ? "Saving..." : "Generate Link"}
              </button>
            </div>
            {shareId && (
              <div style={{ marginTop: 12, padding: 8, background: "rgba(0,0,0,0.5)", color: "#aaa", fontSize: 12, wordBreak: "break-all" }}>
                {window.location.origin}/game-map?gameMapId={shareId}
              </div>
            )}
          </section>
        </div>
      )}
      \1"""

content = re.sub(modal_search, modal_replace, content)

effects_search = r'(const handlePointerDown =.*?\{)'
effects_replace = r"""useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get("gameMapId");
    if (id) {
      fetch(`/api/game-map-planner/${id}`)
        .then(res => res.json())
        .then(data => {
          if (data.payload) {
            const p = JSON.parse(data.payload);
            if (p.assignments) setAssignments(p.assignments);
            if (p.camera) setCamera(p.camera);
            if (p.zoom) setZoom(p.zoom);
            if (data.access === "view-only" && !data.isOwner) setReadonly(true);
          }
        });
    }
  }, []);

  \1"""

content = re.sub(effects_search, effects_replace, content, count=1)

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "w", encoding="utf-8") as f:
    f.write(content)
