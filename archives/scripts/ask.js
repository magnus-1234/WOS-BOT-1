/*
  /ask.js

  Slash command implementing a hybrid RAG + Base Context approach for Whiteout Survival (WoS).

  This updated version prefers the local Xenova embedder (`@xenova/transformers`) to compute
  embeddings (recommended for offline/fast local embedding). If Xenova is not available it will fall
  back to the Hugging Face Inference Embeddings API when a `HUGGINGFACE_API_TOKEN` is provided.
  It also supports ChromaDB if available.

  Features:
  - Loads textual knowledge files from `data/wos/` (one or many .txt files).
  - Uses Hugging Face Inference API to compute embeddings (requires HUGGINGFACE_API_TOKEN in .env).
  - Optional ChromaDB integration for faster lookups (if `chromadb` is installed). Falls back to
    in-memory cosine-similarity search and persists to `data/wos/index.json`.
  - System prompt + retrieved context sent to OpenRouter LLM (OPENROUTER_API_KEY_* env vars) to answer.
  - Async/await, modular functions, and graceful error handling.

  Deployment notes:
  - On Render (ephemeral storage) the index will be rebuilt on each startup if `data/wos/index.json`
    isn't persisted. Persist the file to a persistent disk if available.
  - Environment variables used:
      OPENROUTER_API_KEY_1 (or OPENROUTER_API_KEY_2/OPENROUTER_API_KEY)
      OPENROUTER_MODEL (optional)
      HUGGINGFACE_API_TOKEN (preferred; falls back to xenova if not set)

  Install (project root, PowerShell):
    npm install discord.js @discordjs/builders node-fetch dotenv
    # Recommended: install local Xenova embedder for offline/fast embeddings
    npm install @xenova/transformers
    # Optionally, install ChromaDB client for production vector store
    npm install chromadb
    # If you prefer remote HF embeddings instead of local, set HUGGINGFACE_API_TOKEN in .env

  How to wire:
  - Place this file in your command loader so it registers the `/ask` slash command.

*/

const fs = require('fs');
const path = require('path');
const { SlashCommandBuilder } = require('@discordjs/builders');
const fetch = global.fetch || require('node-fetch');
require('dotenv').config();

// Config
const KB_DIR = path.join(__dirname, 'data', 'wos'); // data/wos under DISCORD BOT
const INDEX_PATH = path.join(KB_DIR, 'index.json');
const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY_1 || process.env.OPENROUTER_API_KEY || process.env.OPENROUTER_API_KEY_2;
const OPENROUTER_MODEL = process.env.OPENROUTER_API_KEY || process.env.OPENROUTER_MODEL || 'meta-llama/llama-3.3-8b-instruct:free';
const HUGGINGFACE_API_TOKEN = process.env.HUGGINGFACE_API_TOKEN || process.env.HUGGINGFACE_API_TOKEN_1 || process.env.HUGGINGFACE_API_TOKEN_2 || process.env.HUGGINGFACE_API_TOKEN_3;
const CHROMA_SERVER_URL = process.env.CHROMA_SERVER_URL || process.env.CHROMA_URL || null;

// runtime caches
let embeddingPipeline = null; // xenova pipeline (fallback)
let inMemoryIndex = null; // [{ id, text, src, embedding }]
let useChroma = false;
let chromaClient = null;

// ------------------------- Utility math -------------------------
function dot(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}
function norm(a) {
  return Math.sqrt(dot(a, a));
}
function cosineSimilarity(a, b) {
  const na = norm(a);
  const nb = norm(b);
  if (na === 0 || nb === 0) return 0;
  return dot(a, b) / (na * nb);
}

// ------------------------- Embeddings (Hugging Face preferred, Xenova fallback) -------------------------
async function hfEmbedText(text, model = 'sentence-transformers/all-MiniLM-L6-v2') {
  if (!HUGGINGFACE_API_TOKEN) throw new Error('Hugging Face API token not set in HUGGINGFACE_API_TOKEN');
  try {
    const res = await fetch('https://api-inference.huggingface.co/embeddings', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${HUGGINGFACE_API_TOKEN}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ inputs: text, model })
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`HF embeddings API error: ${res.status} ${txt}`);
    }
    const payload = await res.json();
    // payload may be { embedding: [...] } or { data: [{embedding: [...]}] } or [ { embedding } ]
    if (Array.isArray(payload) && payload[0]?.embedding) return payload[0].embedding;
    if (payload?.embedding) return payload.embedding;
    if (payload?.data && Array.isArray(payload.data) && payload.data[0]?.embedding) return payload.data[0].embedding;
    throw new Error('Unexpected HF embedding response format');
  } catch (err) {
    console.error('[ask.js] hfEmbedText error', err.message);
    throw err;
  }
}

async function ensureEmbedder() {
  // Prefer Xenova local pipeline first (fast, offline). If not available, fall back to HF if token present.
  if (embeddingPipeline) return 'xenova';
  try {
    // dynamic require so code still loads if package not installed
    const xf = require('@xenova/transformers');
    // pre-load the sentence-transformers-style feature extractor
    embeddingPipeline = await xf.pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2');
    console.log('[ask.js] Loaded @xenova/transformers pipeline (local embeddings)');
    return 'xenova';
  } catch (err) {
    // Xenova not available; fall back to HF if token exists
    if (HUGGINGFACE_API_TOKEN) {
      console.log('[ask.js] Xenova not available; using Hugging Face Inference API for embeddings');
      return 'huggingface';
    }
    console.warn('[ask.js] No embedding provider available. Install @xenova/transformers or set HUGGINGFACE_API_TOKEN');
    throw err;
  }
}

async function embedText(text) {
  const provider = await ensureEmbedder();
  if (provider === 'huggingface') {
    return await hfEmbedText(text);
  }
  // xenova provider
  try {
    const out = await embeddingPipeline(text);
    // Xenova may return an object with 'data' and 'dims' describing a tensor-like result.
    // Normalize common output shapes into a flat numeric array.
    if (out && typeof out === 'object' && out.data) {
      // out.data may be an object with numeric keys or an Array-like; convert to array
      try {
        if (Array.isArray(out.data)) return out.data;
        const keys = Object.keys(out.data).sort((a, b) => Number(a) - Number(b));
        const arr = keys.map(k => out.data[k]);
        return arr;
      } catch (e) {
        // fallthrough to other handlers
      }
    }
    if (Array.isArray(out)) {
      if (Array.isArray(out[0])) {
        const dims = out[0].length;
        const sum = new Array(dims).fill(0);
        out.forEach(tok => { for (let i = 0; i < dims; i++) sum[i] += tok[i]; });
        for (let i = 0; i < dims; i++) sum[i] /= out.length;
        return sum;
      } else {
        return out;
      }
    }
    throw new Error('Unexpected embedding output format');
  } catch (err) {
    console.error('[ask.js] embedText error', err.message);
    throw err;
  }
}

// ------------------------- Vector store (Chroma optional, fallback in-memory) -------------------------
async function ensureVectorStore() {
  if (inMemoryIndex) return inMemoryIndex;
  // ChromaDB is optional. Only attempt to load it when CHROMA_ENABLED is set to a truthy value.
  // This avoids chromadb trying to import optional native/default embed packages during startup
  // in environments where those packages are not installed.
  const chromaEnabled = (process.env.CHROMA_ENABLED || '').toLowerCase() === 'true' || process.env.CHROMA_ENABLED === '1';
  if (chromaEnabled) {
    // If a Chroma server URL is provided, prefer connecting to it (avoids local native bindings)
    try {
      const chroma = require('chromadb');
      const ChromaClient = chroma.ChromaClient || chroma.Client || chroma;
      if (ChromaClient) {
        try {
          if (CHROMA_SERVER_URL) {
            // Try common constructor signatures for server URL compatibility
            try {
              chromaClient = new ChromaClient({ url: CHROMA_SERVER_URL });
            } catch (e1) {
              try {
                chromaClient = new ChromaClient({ path: CHROMA_SERVER_URL });
              } catch (e2) {
                try {
                  chromaClient = new ChromaClient(CHROMA_SERVER_URL);
                } catch (e3) {
                  throw e3;
                }
              }
            }
            useChroma = true;
            console.log('[ask.js] Connected to Chroma server at', CHROMA_SERVER_URL);
          } else {
            // fallback to local client (may require native deps)
            chromaClient = new ChromaClient();
            useChroma = true;
            console.log('[ask.js] ChromaDB client available — will use it for vector storage.');
          }
        } catch (e) {
          console.warn('[ask.js] Chroma client instantiation failed, will use in-memory store:', e.message);
          useChroma = false;
        }
      }
    } catch (err) {
      useChroma = false;
      console.warn('[ask.js] chromadb not installed or failed to require; set CHROMA_ENABLED=true and install chromadb or run a Chroma server and set CHROMA_SERVER_URL to enable.');
    }
  } else {
    useChroma = false;
  }

  // Load persisted index if present
  try {
    if (fs.existsSync(INDEX_PATH)) {
      const raw = fs.readFileSync(INDEX_PATH, 'utf8');
      inMemoryIndex = JSON.parse(raw);
      console.log('[ask.js] Loaded persisted index with', inMemoryIndex.length, 'items');
      return inMemoryIndex;
    }
  } catch (err) {
    console.warn('[ask.js] Could not read index.json:', err.message);
  }

  // If no index persisted, create an empty index ready for indexing.
  inMemoryIndex = [];
  return inMemoryIndex;
}

// If chroma is available, ensure collection exists
async function ensureChromaCollection(name = 'wos') {
  if (!useChroma || !chromaClient) return null;
  try {
    // Some chroma client versions try to instantiate a DefaultEmbeddingFunction which requires
    // an extra package. To avoid that dependency we provide our own embedding function which
    // delegates to the same embedText() used elsewhere. This keeps Chroma usage portable.
    const embeddingFunction = async (texts) => {
      // texts may be a single string or an array of strings depending on client
      const arr = Array.isArray(texts) ? texts : [texts];
      const outs = await Promise.all(arr.map(t => embedText(t)));
      return outs;
    };

    if (typeof chromaClient.getOrCreateCollection === 'function') {
      try {
        return await chromaClient.getOrCreateCollection({ name, embeddingFunction });
      } catch (e) {
        // fallback to calling without embeddingFunction if signature differs
        try { return await chromaClient.getOrCreateCollection({ name }); } catch (e2) { /* fallthrough */ }
      }
    }

    if (typeof chromaClient.createCollection === 'function') {
      try {
        return await chromaClient.createCollection({ name, embeddingFunction });
      } catch (e) {
        // may already exist or client doesn't accept embeddingFunction; try getting it
        try { return chromaClient.getCollection({ name }); } catch (e2) { /* fallthrough */ }
      }
    }

    // Some clients expose `collection` or similar
    return chromaClient.collection || null;
  } catch (err) {
    console.warn('[ask.js] ensureChromaCollection failed:', err.message);
    return null;
  }
}

async function persistIndex() {
  try {
    if (!fs.existsSync(KB_DIR)) fs.mkdirSync(KB_DIR, { recursive: true });
    fs.writeFileSync(INDEX_PATH, JSON.stringify(inMemoryIndex), 'utf8');
    console.log('[ask.js] Persisted index to', INDEX_PATH);
  } catch (err) {
    console.warn('[ask.js] Failed to persist index:', err.message);
  }
}

// ------------------------- Indexing -------------------------
async function indexKnowledgeBase({ forceReindex = false } = {}) {
  await ensureVectorStore();
  if (!fs.existsSync(KB_DIR)) {
    console.warn(`[ask.js] KB dir not found at ${KB_DIR}. Create the directory and add .txt files per your KB.`);
    return inMemoryIndex;
  }

  // If index already exists and not forcing, skip.
  if (inMemoryIndex && inMemoryIndex.length > 0 && !forceReindex) {
    return inMemoryIndex;
  }

  // Read all .txt files in KB_DIR
  const files = fs.readdirSync(KB_DIR).filter(f => f.toLowerCase().endsWith('.txt'));
  const entries = [];
  for (const file of files) {
    const fp = path.join(KB_DIR, file);
    const raw = fs.readFileSync(fp, 'utf8');
    // Split into useful chunks: split on double-newline or line-by-line if not paragraphed
    const paragraphs = raw.split(/\n\n+/).map(p => p.trim()).filter(Boolean);
    for (let i = 0; i < paragraphs.length; i++) {
      const text = paragraphs[i].replace(/\s+/g, ' ').trim();
      if (!text) continue;
      const id = `${file}::${i}`;
      entries.push({ id, text, src: file, embedding: null });
    }
  }

  // compute embeddings in sequence (to control memory)—cache them
  for (let i = 0; i < entries.length; i++) {
    const item = entries[i];
    try {
      item.embedding = await embedText(item.text);
    } catch (err) {
      console.error('[ask.js] Failed to embed item:', item.id, err.message);
      item.embedding = null;
    }
  }

  // filter out failures
  inMemoryIndex = entries.filter(e => Array.isArray(e.embedding));
  // If chroma is available, upsert into a collection for faster queries
  if (useChroma && chromaClient) {
    try {
      const coll = await ensureChromaCollection('wos');
      if (coll && typeof coll.add === 'function') {
        const ids = inMemoryIndex.map(i => i.id);
        const embeddings = inMemoryIndex.map(i => i.embedding);
        const documents = inMemoryIndex.map(i => i.text);
        const metadatas = inMemoryIndex.map(i => ({ src: i.src }));
        await coll.add({ ids, embeddings, documents, metadatas });
        console.log('[ask.js] Upserted', ids.length, 'items into Chroma collection wos');
      }
    } catch (err) {
      console.warn('[ask.js] Failed to upsert to Chroma:', err.message);
    }
  }
  await persistIndex();
  console.log('[ask.js] Indexed', inMemoryIndex.length, 'KB items');
  return inMemoryIndex;
}

// ------------------------- Retrieval -------------------------
async function retrieveRelevant(query, topK = 5) {
  await ensureVectorStore();
  if (!inMemoryIndex || inMemoryIndex.length === 0) {
    console.warn('[ask.js] No index items found; attempting to index KB now.');
    await indexKnowledgeBase();
  }

  if (!inMemoryIndex || inMemoryIndex.length === 0) return [];
  let qEmb;
  try {
    qEmb = await embedText(query);
  } catch (err) {
    console.warn('[ask.js] Failed to embed query:', err.message);
    return [];
  }

  // Shortcut: if the user asks specifically about the bear trap, prefer returning
  // the paragraphs from `beartrap.txt` directly so answers come from that file.
  // This ensures targeted responses even when Chroma isn't available or has import issues.
  try {
    const ql = (query || '').toLowerCase();
    if (ql.includes('bear trap') || ql.includes('beartrap') || ql.includes('bear-trap')) {
      const items = (inMemoryIndex || []).filter(it => (it.src || '').toLowerCase() === 'beartrap.txt');
      const out = items.slice(0, topK).map(it => ({ id: it.id, text: it.text, src: it.src, score: 1.0 }));
      if (out.length > 0) return out;
    }
  } catch (e) {
    // ignore and continue to normal retrieval
  }

  // If chroma available, prefer using it for fast semantic search
  if (useChroma && chromaClient) {
    try {
      const coll = await ensureChromaCollection('wos');
      if (coll && typeof coll.query === 'function') {
        const queryRes = await coll.query({ query_embeddings: [qEmb], n_results: topK, include: ['metadatas', 'documents', 'distances'] });
        const docs = (queryRes?.documents?.[0]) || (queryRes?.results?.[0]?.documents) || [];
        const metadatas = (queryRes?.metadatas?.[0]) || (queryRes?.results?.[0]?.metadatas) || [];
        const distances = (queryRes?.distances?.[0]) || (queryRes?.results?.[0]?.distances) || [];
        const out = [];
        for (let i = 0; i < docs.length; i++) {
          out.push({ id: null, text: docs[i], src: (metadatas[i]?.src || 'wos'), score: distances[i] != null ? 1 - distances[i] : 0 });
        }
        return out.slice(0, topK);
      }
    } catch (err) {
      console.warn('[ask.js] Chroma query failed, falling back to in-memory:', err.message);
    }
  }

  const scored = inMemoryIndex.map(item => {
    const score = cosineSimilarity(qEmb, item.embedding || []);
    return { id: item.id, text: item.text, src: item.src, score };
  });
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, topK);
}

// ------------------------- OpenRouter LLM call -------------------------
async function askOpenRouter(systemPrompt, userQuestion, opts = {}) {
  const model = OPENROUTER_MODEL;
  if (!OPENROUTER_API_KEY) throw new Error('OPENROUTER_API_KEY not set in environment');

  const messages = [
    { role: 'system', content: systemPrompt },
    { role: 'user', content: userQuestion }
  ];

  const body = {
    model,
    messages,
    max_tokens: opts.max_tokens || 512,
    temperature: typeof opts.temperature === 'number' ? opts.temperature : 0.0,
    n: 1
  };

  try {
    const res = await fetch('https://api.openrouter.ai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${OPENROUTER_API_KEY}`
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`OpenRouter API error: ${res.status} ${txt}`);
    }
    const payload = await res.json();
    const content = payload?.choices?.[0]?.message?.content || payload?.choices?.[0]?.text || null;
    return { raw: payload, text: content };
  } catch (err) {
    console.error('[ask.js] askOpenRouter error', err.message);
    throw err;
  }
}

// ------------------------- System prompt builder -------------------------
function buildSystemPrompt(retrievedSnippets = []) {
  const base = `You are Frosty, a highly accurate Whiteout Survival (WoS) expert. Answer only from the provided context when available. If the information is not in the context or you're unsure, say "I don't know" or recommend where to find the info. Keep answers concise and factual.`;

  let ctx = '';
  if (retrievedSnippets && retrievedSnippets.length > 0) {
    ctx += '\n\nRelevant knowledge snippets (for reference):\n';
    retrievedSnippets.forEach((s, i) => {
      ctx += `\n[${i + 1}] (${s.src}) score=${s.score.toFixed(3)}:\n${s.text}\n`;
    });
  } else {
    ctx += '\n\n(No relevant snippets found in KB.)\n';
  }

  ctx += '\n\nPolicy: Use the snippets above as your primary source. If a snippet directly answers the question, answer using it and cite the snippet number in brackets. If none apply, say you are unsure.\n';

  return `${base}\n\n${ctx}`;
}

// ------------------------- Exported command -------------------------
module.exports = {
  data: new SlashCommandBuilder()
    .setName('ask')
    .setDescription('Ask Frosty about Whiteout Survival (WoS)')
    .addStringOption(opt => opt.setName('question').setDescription('Your question about WoS').setRequired(true)),

  async execute(interaction) {
    const question = interaction.options.getString('question', true);
    await interaction.deferReply({ ephemeral: false });

    try {
      // Ensure vector store and index
      await ensureVectorStore();
      // If no persisted index, index the KB (best-effort; expensive on first run)
      if (!inMemoryIndex || inMemoryIndex.length === 0) {
        await indexKnowledgeBase();
      }

      // Retrieve top 5 snippets
      let retrieved = [];
      try {
        retrieved = await retrieveRelevant(question, 5);
      } catch (err) {
        console.warn('[ask.js] Retrieval failed:', err.message);
        retrieved = [];
      }

      const systemPrompt = buildSystemPrompt(retrieved);

      // If no embeddings / KB is empty, warn user and still call LLM with base context
      if (!retrieved || retrieved.length === 0) {
        await interaction.followUp({ content: 'I could not find relevant knowledge in the local WoS knowledge base; I will answer with base knowledge but may be less accurate.', ephemeral: true });
      }

      // Ask OpenRouter
      const answer = await askOpenRouter(systemPrompt, question, { max_tokens: 512, temperature: 0.0 });

      if (!answer || !answer.text) {
        await interaction.editReply('I could not get an answer from the model right now. Try again later.');
        return;
      }

      await interaction.editReply({ content: answer.text });
    } catch (err) {
      console.error('[ask.js] command error:', err);
      await interaction.editReply({ content: `Error while answering: ${err.message}` });
    }
  }
};

// Export internals for testing and maintenance (safe to use in dev/test only)
module.exports._internal = {
  KB_DIR,
  INDEX_PATH,
  ensureEmbedder,
  embedText,
  ensureVectorStore,
  indexKnowledgeBase,
  retrieveRelevant,
  askOpenRouter,
  buildSystemPrompt,
};
