import re

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\HomeApp.tsx", "r", encoding="utf-8") as f:
    content = f.read()

# Patch resolveActiveMenu
search_resolve = r'if \(params\.get\("foundry"\)\) \{\s*return "planner";\s*\}'
replace_resolve = r'if (params.get("foundry")) {\n    return "planner";\n  }\n  if (params.get("gameMapId")) {\n    return "gameMap";\n  }'
content = re.sub(search_resolve, replace_resolve, content)

# Patch syncMenuFromLocation
search_sync = r'setActiveMenu\(params\.get\("foundry"\) \? "planner" : resolveActiveMenu\(window\.location\)\);'
replace_sync = r'setActiveMenu(params.get("foundry") ? "planner" : params.get("gameMapId") ? "gameMap" : resolveActiveMenu(window.location));'
content = re.sub(search_sync, replace_sync, content)

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\HomeApp.tsx", "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied.")
