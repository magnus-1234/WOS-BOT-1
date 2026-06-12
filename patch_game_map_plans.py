import re

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_backend\\src\\index.ts", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add GameMapPlanDocument type
doc_type = """
type GameMapPlanDocument = {
  _id?: ObjectId;
  id: string;
  creatorUserId?: ObjectId;
  payload: string;
  access: 'editable' | 'view-only';
  isActive: boolean;
  createdAt: Date;
  updatedAt: Date;
};
"""
content = re.sub(r'(type FoundryPlanDocument = \{[^\}]+\};)', r'\1\n' + doc_type, content)

# 2. Add collection
content = re.sub(r'(const foundryPlans = db\.collection<FoundryPlanDocument>\(\'foundry_plans\'\);)', r"\1\n  const gameMapPlans = db.collection<GameMapPlanDocument>('game_map_plans');", content)

# 3. Add index and return collection
content = re.sub(r'(foundryPlans\.createIndex\(\{ id: 1 \}, \{ unique: true \}\),)', r'\1\n    gameMapPlans.createIndex({ id: 1 }, { unique: true }),', content)
content = re.sub(r'(foundryPlans\.createIndex\(\{ creatorUserId: 1, createdAt: -1 \}\),)', r'\1\n    gameMapPlans.createIndex({ creatorUserId: 1, createdAt: -1 }),', content)
content = re.sub(r'(adminSessions,\n\s*foundryPlans)', r'\1, gameMapPlans', content)

# 4. Add endpoints
endpoints = """
app.get('/api/game-map-planner/me', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) return;

    const { gameMapPlans } = await getCollections();
    const plans = await gameMapPlans.find({ creatorUserId: user._id, isActive: true }).sort({ createdAt: -1 }).toArray();

    res.json(plans.map(p => ({
      id: p.id,
      access: p.access,
      createdAt: p.createdAt,
      updatedAt: p.updatedAt,
    })));
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch game map plans' });
  }
});

app.get('/api/game-map-planner/:id', async (req, res) => {
  try {
    const { gameMapPlans } = await getCollections();
    const plan = await gameMapPlans.findOne({ id: req.params.id, isActive: true });
    if (!plan) {
      res.status(404).json({ error: 'Plan not found' });
      return;
    }

    const user = await getCurrentUser(req);
    const isOwner = !!(user && plan.creatorUserId && user._id?.equals(plan.creatorUserId));

    res.json({
      id: plan.id,
      payload: plan.payload,
      access: plan.access,
      isOwner,
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch plan' });
  }
});

app.post('/api/game-map-planner', express.json({ limit: '1mb' }), async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) return;

    const { payload, access } = req.body;
    if (typeof payload !== 'string' || !payload) {
      res.status(400).json({ error: 'Invalid payload' });
      return;
    }

    const { gameMapPlans } = await getCollections();
    const nanoid = randomBytes(8).toString('hex').slice(0, 10);
    const doc: GameMapPlanDocument = {
      id: nanoid,
      creatorUserId: user._id,
      payload,
      access: access === 'view-only' ? 'view-only' : 'editable',
      isActive: true,
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    await gameMapPlans.insertOne(doc);
    res.json({ id: doc.id });
  } catch (err) {
    res.status(500).json({ error: 'Failed to create plan' });
  }
});

app.patch('/api/game-map-planner/:id', express.json(), async (req, res) => {
  try {
    const user = await getCurrentUser(req);
    const { gameMapPlans } = await getCollections();
    const plan = await gameMapPlans.findOne({ id: req.params.id, isActive: true });
    
    if (!plan) {
      res.status(404).json({ error: 'Plan not found' });
      return;
    }

    const isOwner = !!(user && plan.creatorUserId && user._id?.equals(plan.creatorUserId));
    
    if (!isOwner && plan.access === 'view-only') {
      res.status(403).json({ error: 'This plan is view-only' });
      return;
    }

    const updates: Partial<GameMapPlanDocument> = { updatedAt: new Date() };
    
    if (req.body.payload !== undefined) {
      updates.payload = String(req.body.payload);
    }
    
    if (isOwner && req.body.access !== undefined) {
      updates.access = req.body.access === 'view-only' ? 'view-only' : 'editable';
    }

    await gameMapPlans.updateOne({ _id: plan._id }, { $set: updates });
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: 'Failed to update plan' });
  }
});

app.delete('/api/game-map-planner/:id', async (req, res) => {
  try {
    const user = await requireCurrentUser(req, res);
    if (!user?._id) return;

    const { gameMapPlans } = await getCollections();
    const result = await gameMapPlans.updateOne(
      { id: req.params.id, creatorUserId: user._id },
      { $set: { isActive: false, updatedAt: new Date() } }
    );

    if (result.matchedCount === 0) {
      res.status(404).json({ error: 'Plan not found or unauthorized' });
      return;
    }

    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: 'Failed to delete plan' });
  }
});
"""

content = content.replace("app.get('/api/foundry-planner/me', async (req, res) => {", endpoints + "\napp.get('/api/foundry-planner/me', async (req, res) => {")

with open("f:\\Whiteout Survival Bot\\whiteoutsurvival_dev_backend\\src\\index.ts", "w", encoding="utf-8") as f:
    f.write(content)

print("Patch applied.")
