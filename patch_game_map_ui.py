import re

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "r", encoding="utf-8") as f:
    content = f.read()

# Render assignments on SunfireLandmarks
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

# Append the assignment panel in the sidebar.
sidebar_search = r'(</aside>)'
sidebar_replace = """
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
\1"""

content = re.sub(sunfire_render_search, sunfire_render_replace, content, flags=re.DOTALL)
content = re.sub(sidebar_search, sidebar_replace, content)

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied.")
