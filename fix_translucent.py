with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "r", encoding="utf-8") as f:
    content = f.read()

# Sidebar
content = content.replace(
    'background: "rgba(10, 20, 30, 0.8)"',
    'background: "rgba(10, 20, 30, 0.7)", backdropFilter: "blur(12px)"'
)

# Modals
content = content.replace(
    'style={{ background: "rgba(20, 30, 40, 0.95)",',
    'style={{ background: "rgba(15, 20, 30, 0.75)", backdropFilter: "blur(16px)",'
)

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "w", encoding="utf-8") as f:
    f.write(content)

print("Translucent patch applied.")
