import re

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "r", encoding="utf-8") as f:
    content = f.read()

# Replace export default function WosGameMap
sig_search = r'export default function WosGameMap\(\{ embedded = false \}: \{ embedded\?: boolean \}\) \{'
sig_replace = """export type GameMapAssignment = { rallyLeader: string; joiners: string[] };

export default function WosGameMap({ embedded = false, authUser = null }: { embedded?: boolean; authUser?: any }) {"""

content = re.sub(sig_search, sig_replace, content)

# Add states
states_replace = """  const [camera, setCamera] = useState(INITIAL_CAMERA);

  // Planner states
  const [assignments, setAssignments] = useState<Record<string, GameMapAssignment>>({});
  const [shareOpen, setShareOpen] = useState(false);
  const [manageSharesOpen, setManageSharesOpen] = useState(false);
  const [shareAccess, setShareAccess] = useState<"editable" | "view-only">("editable");
  const [shareId, setShareId] = useState("");
  const [myShares, setMyShares] = useState<any[]>([]);
  const [readonly, setReadonly] = useState(false);
  const [savedAt, setSavedAt] = useState("");
  const [isSavingShare, setIsSavingShare] = useState(false);"""

content = content.replace("  const [camera, setCamera] = useState(INITIAL_CAMERA);", states_replace)

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_frontend(main website)\\src\\app\\game-map\\WosGameMap.tsx", "w", encoding="utf-8") as f:
    f.write(content)
