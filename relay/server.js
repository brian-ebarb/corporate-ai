#!/usr/bin/env node
/**
 * Corporate AI Dashboard Relay
 * ==============================
 * Bridges browser dashboard ↔ Corporate AI backend.
 *
 * CA WS:      ws://CA_HOST:CA_PORT/ws        (Corporate AI event stream)
 * CA HTTP:    http://CA_HOST:CA_PORT         (REST API)
 * Relay WS:   ws://localhost:RELAY_PORT/ws   (browser push)
 * Relay HTTP: http://localhost:RELAY_PORT    (browser REST)
 */

const express = require('express');
const http    = require('http');
const cors    = require('cors');
const fs      = require('fs');
const path    = require('path');
let WebSocket, WebSocketServer;
try {
  ({ WebSocket, WebSocketServer } = require('ws'));
} catch {
  console.error('Missing "ws" package. Run: npm install ws');
  process.exit(1);
}

/* ────────────────────────────────────────────────────────────
   CONFIGURATION
   ──────────────────────────────────────────────────────────── */
const CA_HOST     = process.env.CA_HOST     || 'localhost';
const CA_PORT     = parseInt(process.env.CA_PORT     || '8000', 10);
const RELAY_PORT  = parseInt(process.env.RELAY_PORT  || '3001', 10);
const RECONNECT_MS = 5000;
const MAX_EVENTS   = 1000;

const CA_WS_URL   = `ws://${CA_HOST}:${CA_PORT}/ws`;
const CA_HTTP_URL = `http://${CA_HOST}:${CA_PORT}`;

/* ────────────────────────────────────────────────────────────
   STATE
   ──────────────────────────────────────────────────────────── */
const STATE = {
  connected: false,
  caWs: null,
  events: [],           // ring buffer of CA events
  browsers: new Set(),  // browser WebSocket clients
};

/* ────────────────────────────────────────────────────────────
   EXPRESS + HTTP SERVER + BROWSER WS
   ──────────────────────────────────────────────────────────── */
const app    = express();
const server = http.createServer(app);
const wss    = new WebSocketServer({ server });

app.use(cors({ origin: '*' }));
app.use(express.json());

/* Browser WebSocket connections */
wss.on('connection', (ws) => {
  STATE.browsers.add(ws);

  // Send current CA connection status
  ws.send(JSON.stringify({ type: 'CA_STATUS', data: { connected: STATE.connected } }));

  // Replay last 50 buffered events
  const recent = STATE.events.slice(-50);
  if (recent.length) {
    ws.send(JSON.stringify({ type: 'CA_EVENTS_BATCH', data: recent }));
  }

  ws.on('close', () => STATE.browsers.delete(ws));
  ws.on('error', () => STATE.browsers.delete(ws));
});

function broadcastToBrowsers(msg) {
  const str = JSON.stringify(msg);
  for (const ws of STATE.browsers) {
    if (ws.readyState === WebSocket.OPEN) ws.send(str);
  }
}

function addToRingBuffer(evt) {
  STATE.events.push(evt);
  if (STATE.events.length > MAX_EVENTS) STATE.events.shift();
}

/* ────────────────────────────────────────────────────────────
   CORPORATE AI WEBSOCKET CONNECTION
   ──────────────────────────────────────────────────────────── */
let _reconnTimer = null;

function caConnect() {
  if (STATE.caWs) {
    try { STATE.caWs.terminate(); } catch {}
    STATE.caWs = null;
  }

  console.log(`[CA] Connecting to ${CA_WS_URL}…`);
  const ws = new WebSocket(CA_WS_URL);
  STATE.caWs = ws;

  ws.on('open', () => {
    console.log(`[CA] Connected to ${CA_WS_URL}`);
    STATE.connected = true;
    broadcastToBrowsers({ type: 'CA_STATUS', data: { connected: true } });
  });

  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString());
      // CA websocket_server sends { type: 'CA_EVENT', data: evt }
      // but raw events are also acceptable
      const evt = (msg.type === 'CA_EVENT') ? msg.data : msg;
      addToRingBuffer(evt);
      broadcastToBrowsers({ type: 'CA_EVENT', data: evt });
    } catch (e) {
      console.warn('[CA] Bad WS message:', e.message);
    }
  });

  ws.on('close', (code) => {
    console.warn(`[CA] WS closed (code=${code}). Reconnecting in ${RECONNECT_MS}ms…`);
    STATE.connected = false;
    STATE.caWs = null;
    broadcastToBrowsers({ type: 'CA_STATUS', data: { connected: false } });
    if (!_reconnTimer) {
      _reconnTimer = setTimeout(() => { _reconnTimer = null; caConnect(); }, RECONNECT_MS);
    }
  });

  ws.on('error', (err) => {
    console.error('[CA] WS error:', err.message);
    // close handler will trigger reconnect
  });
}

/* ────────────────────────────────────────────────────────────
   REST ENDPOINTS
   ──────────────────────────────────────────────────────────── */

/* GET /api/status */
app.get('/api/status', (req, res) => {
  res.json({
    connected:  STATE.connected,
    host:       CA_HOST,
    port:       CA_PORT,
    eventCount: STATE.events.length,
  });
});

/* GET /api/agents — proxy to CA, wrap for dashboard compatibility */
app.get('/api/agents', async (req, res) => {
  try {
    const r = await fetch(`${CA_HTTP_URL}/agents`, { signal: AbortSignal.timeout(5000) });
    const data = await r.json();
    const agents = Array.isArray(data) ? data : (data.agents || []);
    res.json({ agents });
  } catch (e) {
    console.error('[Relay] /api/agents error:', e.message);
    res.json({ agents: [] });
  }
});

/* POST /api/chat — proxy to CA; returns { ok: true } immediately */
app.post('/api/chat', async (req, res) => {
  try {
    const r = await fetch(`${CA_HTTP_URL}/chat`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(req.body),
      signal:  AbortSignal.timeout(10000),
    });
    const data = await r.json();
    res.json(data);
  } catch (e) {
    console.error('[Relay] /api/chat error:', e.message);
    res.status(503).json({ ok: false, error: e.message });
  }
});

/* GET /api/tasks — proxy to CA */
app.get('/api/tasks', async (req, res) => {
  try {
    const r = await fetch(`${CA_HTTP_URL}/tasks`, { signal: AbortSignal.timeout(5000) });
    res.json(await r.json());
  } catch {
    res.json([]);
  }
});

/* GET /api/events — last N buffered events */
app.get('/api/events', (req, res) => {
  const n = Math.min(parseInt(req.query.n || '100', 10), MAX_EVENTS);
  res.json(STATE.events.slice(-n));
});

/* ────────────────────────────────────────────────────────────
   INTEL FEED — File-backed intel store
   ──────────────────────────────────────────────────────────── */
const INTEL_FEED_PATH = process.env.INTEL_FEED_PATH
  || path.join(__dirname, 'intel-feed.json');

function readIntelFeed() {
  try {
    const raw    = fs.readFileSync(INTEL_FEED_PATH, 'utf8');
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : (parsed.items || []);
  } catch { return []; }
}

function writeIntelFeed(items) {
  try { fs.writeFileSync(INTEL_FEED_PATH, JSON.stringify(items, null, 2)); return true; }
  catch { return false; }
}

/* GET /api/intel */
app.get('/api/intel', (req, res) => {
  let items   = readIntelFeed();
  let changed = false;
  items = items.map(item => {
    if (!item.id) {
      item.id = `intel-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      changed = true;
    }
    return item;
  });
  if (changed) writeIntelFeed(items);
  res.json({ ok: true, items, count: items.length, source: INTEL_FEED_PATH });
});

/* POST /api/intel */
app.post('/api/intel', (req, res) => {
  const incoming = Array.isArray(req.body) ? req.body : [req.body];
  const items    = readIntelFeed();
  let added      = 0;
  for (const item of incoming) {
    if (!item.title) continue;
    items.push({
      id:          item.id || `intel-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      title:       item.title,
      summary:     item.summary     || '',
      category:    item.category    || 'ai-news',
      importance:  item.importance  || 'notable',
      source:      item.source      || '',
      sourceLabel: item.sourceLabel || '',
      ts:          item.ts          || Date.now(),
    });
    added++;
  }
  if (added > 0 && writeIntelFeed(items)) {
    broadcastToBrowsers({ type: 'INTEL_UPDATED', data: { count: items.length } });
    res.json({ ok: true, added });
  } else {
    res.status(500).json({ ok: false, error: 'Failed to write intel feed' });
  }
});

/* DELETE /api/intel/:id */
app.delete('/api/intel/:id', (req, res) => {
  let items  = readIntelFeed();
  const before = items.length;
  items = items.filter(i => i.id !== req.params.id);
  writeIntelFeed(items);
  res.json({ ok: true, removed: before - items.length });
});

/* ── Chat history (local store, used by dashboard LS) ── */
const chatHistories = {};
app.get('/api/chat/history/:agentId', (req, res) => {
  res.json({ history: chatHistories[req.params.agentId] || [] });
});
app.delete('/api/chat/history/:agentId', (req, res) => {
  chatHistories[req.params.agentId] = [];
  res.json({ ok: true });
});

/* POST /api/session/reset — proxy to CA; clears Bob's conversation history */
app.post('/api/session/reset', async (req, res) => {
  const sessionId = req.query.session_id || req.body?.session_id || 'default';
  try {
    const r = await fetch(
      `${CA_HTTP_URL}/session/reset?session_id=${encodeURIComponent(sessionId)}`,
      { method: 'POST', signal: AbortSignal.timeout(5000) }
    );
    const data = await r.json();
    res.json(data);
  } catch (e) {
    console.error('[Relay] /api/session/reset error:', e.message);
    res.status(503).json({ ok: false, error: e.message });
  }
});

/* GET /api/config — proxy to CA */
app.get('/api/config', async (req, res) => {
  try {
    const r = await fetch(`${CA_HTTP_URL}/config`, { signal: AbortSignal.timeout(5000) });
    res.json(await r.json());
  } catch (e) {
    res.json({ user: {} });
  }
});

/* Health */
app.get('/health', (req, res) => res.json({ ok: true }));

/* ────────────────────────────────────────────────────────────
   START
   ──────────────────────────────────────────────────────────── */
server.listen(RELAY_PORT, () => {
  console.log('\n  Corporate AI Dashboard Relay');
  console.log('  ============================');
  console.log(`  Relay:      http://localhost:${RELAY_PORT}`);
  console.log(`  CA backend: ${CA_HTTP_URL}`);
  console.log(`  CA WS:      ${CA_WS_URL}`);
  console.log(`  Intel feed: ${INTEL_FEED_PATH}\n`);
  caConnect();
});
