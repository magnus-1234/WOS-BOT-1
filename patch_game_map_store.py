import re

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\api\\game-map-planner\\store.ts", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("foundry-planner", "game-map-planner")
content = content.replace("FoundryPlanDoc", "GameMapPlanDoc")
content = content.replace("foundry_plans", "game_map_plans")
content = content.replace("foundry plans", "game map plans")

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\api\\game-map-planner\\store.ts", "w", encoding="utf-8") as f:
    f.write(content)
