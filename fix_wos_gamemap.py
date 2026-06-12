with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "r", encoding="utf-8") as f:
    content = f.read()

# Add missing </aside> and Manage Shares modal
replacement = """          </div>
        </aside>

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
    </section>
  );
}
"""

content = content.replace("          </div>\n \n      </div>\n    </section>\n  );\n}", replacement)

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "w", encoding="utf-8") as f:
    f.write(content)

print("Fixed!")
