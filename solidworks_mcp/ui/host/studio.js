/**
 * SolidWorks Interactive Studio - A2UI host.
 *
 * The Python server describes the studio as A2UI 1.0 messages (see ui/a2ui.py).
 * This page only renders them with the stock A2UI web renderer and relays A2UI
 * renderer-to-agent actions back to the server. It has no studio layout of its own.
 *
 * Two transports carry the actions:
 *   - inside an MCP Apps host (the Claude app): a tools/call request for the
 *     server's `studio_action` tool, relayed by the host;
 *   - in a plain browser tab: the local studio bridge over HTTP.
 */
import {MessageProcessor} from '@a2ui/web_core/v1_0';
import {basicCatalog} from '@a2ui/web_core/catalogs/basic/v1';
import '@a2ui/lit'; // registers <a2ui-surface>

const A2UI_VERSION = '1.0';
const DEBOUNCE_MS = 180;

const cfg = Object.assign({bridge: '', token: '', messages: null}, window.__SW_STUDIO__ || {});
const mount = document.getElementById('studio');
const banner = document.getElementById('banner');

/** Last dimension values confirmed by the server, keyed by data-model path. */
const serverValues = new Map();
const pendingTimers = new Map();
const subscriptions = [];
let surface = null;
let eventSource = null;

function setBanner(text, kind) {
  if (!banner) return;
  banner.textContent = text || '';
  banner.dataset.kind = kind || '';
  banner.hidden = !text;
}

function bridgeUrl(path) {
  const sep = path.includes('?') ? '&' : '?';
  return `${cfg.bridge}${path}${sep}token=${encodeURIComponent(cfg.token)}`;
}

/** Remember dimension values the server pushed so they are not echoed back. */
function noteServerValues(messages) {
  for (const msg of messages) {
    const dims = msg && msg.createSurface && msg.createSurface.dataModel && msg.createSurface.dataModel.dims;
    if (dims) {
      for (const [key, dim] of Object.entries(dims)) serverValues.set(`/dims/${key}/value`, dim.value);
    }
    const update = msg && msg.updateDataModel;
    if (update && /^\/dims\/[^/]+\/value$/.test(update.path || '')) {
      serverValues.set(update.path, update.value);
    }
  }
}

function applyMessages(payload) {
  const messages = Array.isArray(payload) ? payload : (payload && payload.messages) || [];
  if (!messages.length) return;
  noteServerValues(messages);
  processor.processMessages(messages);
}

/** Deliver one A2UI renderer-to-agent message and apply the A2UI messages that come back. */
async function sendAction(name, sourceComponentId, context) {
  const message = {
    version: A2UI_VERSION,
    action: {
      name,
      surfaceId: surface ? surface.id : 'solidworks-studio',
      sourceComponentId: sourceComponentId || 'root',
      timestamp: new Date().toISOString(),
      context: context || {},
    },
  };
  try {
    let reply;
    if (hostConnected) {
      const result = await rpcRequest('tools/call', {name: 'studio_action', arguments: {message}}, 120000);
      reply = result && result.structuredContent && result.structuredContent.a2ui;
      if (!reply) throw new Error('the server returned no studio update');
    } else if (cfg.bridge) {
      const resp = await fetch(bridgeUrl('/api/a2ui/action'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(message),
      });
      reply = await resp.json();
    } else {
      throw new Error('not connected');
    }
    applyMessages(reply);
    setBanner('', '');
  } catch (err) {
    setBanner(`Could not reach SolidWorks: ${err.message || err}`, 'error');
  }
}

/** A2UI action listener: every Button in the surface lands here. */
function onAction(action) {
  if (action.name === 'prompt_agent') {
    promptAgent(String((action.context && action.context.summary) || ''));
    return;
  }
  return sendAction(action.name, action.sourceComponentId, action.context);
}

const processor = new MessageProcessor([basicCatalog], onAction);

function watchDimensions() {
  while (subscriptions.length) subscriptions.pop().unsubscribe();
  const dims = surface.dataModel.get('/dims') || {};
  for (const key of Object.keys(dims)) {
    const path = `/dims/${key}/value`;
    if (!serverValues.has(path)) serverValues.set(path, dims[key].value);
    subscriptions.push(
      surface.dataModel.subscribe(path, value => {
        if (typeof value !== 'number' || value === serverValues.get(path)) return;
        clearTimeout(pendingTimers.get(path));
        pendingTimers.set(
          path,
          setTimeout(() => {
            const dim = surface.dataModel.get(`/dims/${key}`) || {};
            serverValues.set(path, value);
            sendAction('set_dimension', `slider_${key}`, {
              key,
              name: dim.name,
              value,
              units: dim.units,
              view: surface.dataModel.get('/viewport/view'),
            });
          }, DEBOUNCE_MS),
        );
      }),
    );
  }
}

processor.onSurfaceCreated(created => {
  surface = created;
  mount.replaceChildren();
  const el = document.createElement('a2ui-surface');
  el.surface = created;
  mount.appendChild(el);
});

function connectEvents() {
  // Only the browser-tab transport needs server push; in a host every update is a tool result.
  if (hostConnected || !cfg.bridge || eventSource) return;
  eventSource = new EventSource(bridgeUrl('/api/a2ui/events'));
  eventSource.onmessage = ev => {
    try {
      applyMessages(JSON.parse(ev.data));
    } catch (err) {
      console.warn('Bad studio event', err);
    }
  };
}

function start(payload) {
  if (payload) {
    if (payload.bridge) cfg.bridge = payload.bridge;
    if (payload.token) cfg.token = payload.token;
  }
  const messages = payload && payload.messages;
  if (messages && messages.length) {
    if (surface) {
      // A new studio was opened into the same view: start from a clean surface.
      processor.processMessages([{version: A2UI_VERSION, deleteSurface: {surfaceId: surface.id}}]);
      surface = null;
      serverValues.clear();
    }
    applyMessages(messages);
    if (surface) watchDimensions();
  }
  connectEvents();
}

async function loadFromBridge() {
  try {
    const resp = await fetch(bridgeUrl('/api/a2ui'));
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    start(await resp.json());
  } catch (err) {
    setBanner(`Could not load the studio from SolidWorks: ${err.message || err}`, 'error');
  }
}

// ---------------------------------------------------------------------------
// MCP Apps host integration (only active when embedded in an MCP Apps client)
// ---------------------------------------------------------------------------

const embedded = window.parent && window.parent !== window;
let hostConnected = false;
let rpcId = 0;
const rpcPending = new Map();

function rpcSend(message) {
  if (embedded) window.parent.postMessage(Object.assign({jsonrpc: '2.0'}, message), '*');
}

function rpcRequest(method, params, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const id = ++rpcId;
    rpcPending.set(id, {resolve, reject});
    rpcSend({id, method, params});
    setTimeout(() => {
      if (rpcPending.delete(id)) reject(new Error(`${method} timed out`));
    }, timeoutMs);
  });
}

function promptAgent(summary) {
  const text = summary ||
    'I adjusted the SolidWorks model in the interactive studio. Please verify the model and update any dependent drawings or simulations.';
  if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
  if (embedded) {
    rpcRequest('ui/message', {role: 'user', content: [{type: 'text', text}]}).catch(() => {});
    setBanner('Sent to the agent.', 'info');
  } else {
    setBanner('Prompt copied to the clipboard - paste it into the chat.', 'info');
  }
}

window.addEventListener('message', ev => {
  const msg = ev.data;
  if (!msg || msg.jsonrpc !== '2.0') return;
  if (msg.id !== undefined && !msg.method) {
    const pending = rpcPending.get(msg.id);
    if (!pending) return;
    rpcPending.delete(msg.id);
    if (msg.error) pending.reject(new Error(msg.error.message || 'request failed'));
    else pending.resolve(msg.result);
    return;
  }
  if (msg.method === 'ui/notifications/tool-result') {
    const structured = msg.params && msg.params.structuredContent;
    if (structured && structured.a2ui) start(structured.a2ui);
  } else if (msg.method === 'ping' && msg.id !== undefined) {
    rpcSend({id: msg.id, result: {}});
  }
});

function reportSize() {
  rpcSend({
    method: 'ui/notifications/size-changed',
    params: {width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight},
  });
}

function startStandalone() {
  if (cfg.messages && cfg.messages.length) {
    start(cfg);
  } else if (cfg.bridge && cfg.token) {
    loadFromBridge();
  }
}

if (embedded) {
  rpcRequest('ui/initialize', {
    protocolVersion: '2026-01-26',
    appInfo: {name: 'solidworks-studio', version: '1'},
    appCapabilities: {},
  })
    .then(() => {
      hostConnected = true;
      rpcSend({method: 'ui/notifications/initialized'});
      new ResizeObserver(reportSize).observe(document.documentElement);
    })
    // Framed by something that is not an MCP Apps host: behave like a browser tab.
    .catch(startStandalone);
} else {
  startStandalone();
}
