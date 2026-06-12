import re

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Imports
import_search = r'import \{ useCallback, useEffect, useRef, useState \} from "react";'
import_replace = """import { useCallback, useEffect, useRef, useState } from "react";"""
content = re.sub(import_search, import_replace, content)

# 2. Signature and Types
sig_search = r'export default function WosGameMap\(\{ embedded = false \}: \{ embedded\?: boolean \}\) \{'
sig_replace = """export type GameMapAssignment = { rallyLeader: string; joiners: string[] };

export default function WosGameMap({ embedded = false, authUser = null }: { embedded?: boolean; authUser?: any }) {"""
content = re.sub(sig_search, sig_replace, content)

# 3. States
states_replace = """  const [camera, setCamera] = useState(INITIAL_CAMERA);

  // Planner states
  const [assignments, setAssignments] = useState<Record<string, GameMapAssignment>>({});
  const [shareOpen, setShareOpen] = useState(false);
  const [manageSharesOpen, setManageSharesOpen] = useState(false);
  const [shareAccess, setShareAccess] = useState<"editable" | "view-only">("editable");
  const [shareId, setShareId] = useState("");
  const [myShares, setMyShares] = useState<any[]>([]);
  const [readonly, setReadonly] = useState(false);
  const [isSavingShare, setIsSavingShare] = useState(false);"""
content = content.replace("  const [camera, setCamera] = useState(INITIAL_CAMERA);", states_replace)

# 4. Effects
effect_search = r'(const handlePointerDown =.*?\{)'
effect_replace = r"""useEffect(() => {
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

  useEffect(() => {
    if (manageSharesOpen && authUser) {
      fetch("/api/game-map-planner/me", { credentials: "include", headers: { "x-user-id": authUser.id } })
        .then(res => res.json())
        .then(data => Array.isArray(data) ? setMyShares(data) : null);
    }
  }, [manageSharesOpen, authUser]);

  \1"""
content = re.sub(effect_search, effect_replace, content, count=1)

# 5. Sunfire rendering
sunfire_render_search = r'(<g key=\{node\.id\} transform=\{\`translate\(\$\{node\.planner\.col\} \$\{node\.planner\.row\}\)\`\} aria-label=\{\`\$\{node\.label\}\`\}>.*?)(</g>)'
sunfire_render_replace = r"""\1
      {assignments[node.id] && (
        <g transform="translate(0.5, 0.5)">
          <rect x="-0.8" y="-0.5" width="1.6" height="0.4" fill="rgba(0,0,0,0.7)" rx="0.1" />
          <text x="0" y="-0.2" textAnchor="middle" fontSize="0.2" fill="#fff" fontWeight="bold">
            {assignments[node.id].rallyLeader || "No Leader"}
          </text>
          {assignments[node.id].joiners.map((j, i) => (
            <text key={i} x="0" y={i * 0.2} textAnchor="middle" fontSize="0.15" fill="#ccc">
              {j}
            </text>
          ))}
        </g>
      )}
\2"""
content = re.sub(sunfire_render_search, sunfire_render_replace, content, flags=re.DOTALL)

# 6. Sidebar and Modals
sidebar_search = r'(          <button type="button" onClick=\{resetView\}>Reset View</button>\s*)(</aside>\s*</div>\s*</section>)'
sidebar_replace = r"""\1
          <div className="wos-map-planner-controls" style={{ marginTop: 20, display: "flex", flexDirection: "column", gap: 10, background: "rgba(10, 20, 30, 0.8)", padding: 15, borderRadius: 8 }}>
            <h3 style={{ margin: 0, color: "#fff", fontSize: 14 }}>Realtime Map Planner</h3>
            <p style={{ margin: 0, color: "#ccc", fontSize: 12 }}>Assign players to Sunfire Landmarks</p>
            {SUNFIRE_LANDMARKS.map(lm => (
              <div key={lm.id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ color: "#fff", fontSize: 12 }}>{lm.label}</label>
                <input 
                  type="text" 
                  placeholder="Rally Leader" 
                  value={assignments[lm.id]?.rallyLeader || ""}
                  disabled={readonly}
                  onChange={e => setAssignments(prev => ({ ...prev, [lm.id]: { ...prev[lm.id], rallyLeader: e.target.value, joiners: prev[lm.id]?.joiners || [] } }))}
                  style={{ background: "rgba(0,0,0,0.5)", border: "1px solid #444", color: "#fff", padding: "4px 8px", borderRadius: 4 }}
                />
              </div>
            ))}

            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button type="button" onClick={() => setShareOpen(true)} style={{ background: "#3b82f6", color: "#fff", border: "none", padding: "6px 12px", borderRadius: 4, cursor: "pointer", flex: 1 }}>
                Share Plan
              </button>
            </div>
            {authUser && (
              <button type="button" onClick={() => setManageSharesOpen(true)} style={{ background: "transparent", color: "#3b82f6", border: "1px solid #3b82f6", padding: "6px 12px", borderRadius: 4, cursor: "pointer" }}>
                My Shared Links
              </button>
            )}
          </div>
        </aside>

        {shareOpen && (
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
                        alert(`Plan Shared! Link: ${window.location.origin}/?gameMapId=${data.id}#gameMap`);
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
                  {window.location.origin}/?gameMapId={shareId}#gameMap
                </div>
              )}
            </section>
          </div>
        )}

        {manageSharesOpen && (
          <div className="modal-backdrop" onClick={() => setManageSharesOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <section className="share-modal foundry-share-modal" onClick={e => e.stopPropagation()} style={{ background: "rgba(20, 30, 40, 0.95)", padding: 24, borderRadius: 12, width: 400, maxWidth: "90%", maxHeight: "80vh", overflowY: "auto" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h2 style={{ color: "#fff", margin: 0 }}>My Shared Plans</h2>
                <button onClick={() => setManageSharesOpen(false)} style={{ background: "none", border: "none", color: "#fff", cursor: "pointer", fontSize: 20 }}>&times;</button>
              </div>
              {myShares.length === 0 ? (
                <p style={{ color: "#ccc" }}>You have no active shared plans.</p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
                  {myShares.map(share => (
                    <div key={share.id} style={{ background: "rgba(0,0,0,0.5)", padding: 12, borderRadius: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                      <a href={`/?gameMapId=${share.id}#gameMap`} target="_blank" rel="noreferrer" style={{ color: "#3b82f6", fontWeight: "bold" }}>
                        Link: {share.id}
                      </a>
                      <span style={{ fontSize: 12, color: "#aaa" }}>{new Date(share.createdAt).toLocaleString()}</span>
                      <button 
                        onClick={async () => {
                          try {
                            await fetch(`/api/game-map-planner/${share.id}`, { method: "DELETE", credentials: "include", headers: { "x-user-id": authUser?.id || "" } });
                            setMyShares(s => s.filter(x => x.id !== share.id));
                          } catch (e) {}
                        }}
                        style={{ background: "#ef4444", color: "#fff", border: "none", borderRadius: 4, padding: "4px 8px", cursor: "pointer", alignSelf: "flex-start", marginTop: 4 }}
                      >
                        Delete
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

      </div>
    </section>"""
content = re.sub(sidebar_search, sidebar_replace, content)

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied.")
