/*
 * Shared MCP Apps (SEP-1865, protocol 2026-01-26) bridge for the SolidWorks views.
 * Inlined into each view by solidworks_mcp/ui/apps.py, so the views stay single files.
 *
 *   McpApp.connect({name, version, onToolResult, onToolCancelled}) -> Promise<hostContext>
 *   McpApp.callTool(name, args, timeoutMs)  -> Promise<CallToolResult>
 *   McpApp.sendMessage(text)                -> Promise (ui/message, falls back to the clipboard)
 *   McpApp.connected()                      -> boolean
 *   McpApp.text(result)                     -> the text content of a tool result
 *
 * The bridge answers ping and ui/resource-teardown, applies the host's theme variables,
 * and reports the content height (ui/notifications/size-changed) as the view changes.
 */
window.McpApp = (() => {
  'use strict';

  const PROTOCOL_VERSION = '2026-01-26';
  const embedded = window.parent && window.parent !== window;
  const pending = new Map();
  let rpcId = 0;
  let hostConnected = false;
  let handlers = {};
  let lastSize = '';

  function send(message) {
    if (embedded) window.parent.postMessage(Object.assign({jsonrpc: '2.0'}, message), '*');
  }

  function request(method, params, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const id = ++rpcId;
      const timer = setTimeout(() => {
        if (pending.delete(id)) reject(new Error(`${method} timed out`));
      }, timeoutMs);
      pending.set(id, {
        resolve: v => { clearTimeout(timer); resolve(v); },
        reject: e => { clearTimeout(timer); reject(e); },
      });
      send({id, method, params});
    });
  }

  function notify(method, params) {
    send(params === undefined ? {method} : {method, params});
  }

  function text(result) {
    return ((result && result.content) || []).filter(c => c && c.type === 'text').map(c => c.text).join('\n');
  }

  function applyHostContext(ctx) {
    if (!ctx) return;
    if (ctx.theme === 'light' || ctx.theme === 'dark') document.documentElement.style.colorScheme = ctx.theme;
    const vars = ctx.styles && ctx.styles.variables;
    if (vars) {
      for (const [name, value] of Object.entries(vars)) {
        if (name.startsWith('--') && typeof value === 'string') document.documentElement.style.setProperty(name, value);
      }
    }
  }

  // Measure the content, not the frame: documentElement.scrollHeight never drops below the
  // iframe's own height, so the view could grow but never shrink.
  function reportSize() {
    if (!hostConnected) return;
    const style = getComputedStyle(document.body);
    const height = Math.ceil(document.body.getBoundingClientRect().height +
      parseFloat(style.marginTop) + parseFloat(style.marginBottom));
    const width = Math.ceil(document.documentElement.scrollWidth);
    const key = `${width}x${height}`;
    if (key === lastSize) return;
    lastSize = key;
    notify('ui/notifications/size-changed', {width, height});
  }

  window.addEventListener('message', ev => {
    if (ev.source !== window.parent) return;
    const msg = ev.data;
    if (!msg || msg.jsonrpc !== '2.0') return;

    if (msg.id !== undefined && !msg.method) {           // a response to one of our requests
      const p = pending.get(msg.id);
      if (!p) return;
      pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error.message || 'request failed'));
      else p.resolve(msg.result);
      return;
    }

    const params = msg.params || {};
    switch (msg.method) {
      case 'ui/notifications/tool-result':
        if (handlers.onToolResult) handlers.onToolResult(params);
        break;
      case 'ui/notifications/tool-cancelled':
        if (handlers.onToolCancelled) handlers.onToolCancelled(params);
        break;
      case 'ui/notifications/host-context-changed':
        applyHostContext(params);
        break;
      case 'ui/notifications/tool-input':
      case 'ui/notifications/tool-input-partial':
        break;
      case 'ui/resource-teardown':
      case 'ping':
        if (msg.id !== undefined) send({id: msg.id, result: {}});
        break;
      default:
        if (msg.id !== undefined) send({id: msg.id, error: {code: -32601, message: `Method not found: ${msg.method}`}});
    }
  });

  function connect(options) {
    handlers = options || {};
    if (!embedded) return Promise.reject(new Error('not inside an MCP Apps host'));
    return request('ui/initialize', {
      protocolVersion: PROTOCOL_VERSION,
      appInfo: {name: handlers.name || 'solidworks-view', version: handlers.version || '1.0.0'},
      appCapabilities: {availableDisplayModes: ['inline']},
    }).then(result => {
      hostConnected = true;
      const ctx = result && result.hostContext;
      applyHostContext(ctx);
      notify('ui/notifications/initialized');
      new ResizeObserver(reportSize).observe(document.body);
      reportSize();
      return ctx;
    });
  }

  function callTool(name, args, timeoutMs = 120000) {
    if (!hostConnected) return Promise.reject(new Error('not connected to the MCP Apps host'));
    return request('tools/call', {name, arguments: args || {}}, timeoutMs).then(result => {
      if (result && result.isError) throw new Error(text(result) || `${name} failed`);
      return result;
    });
  }

  function sendMessage(messageText) {
    if (navigator.clipboard) navigator.clipboard.writeText(messageText).catch(() => {});
    if (!hostConnected) return Promise.reject(new Error('copied to the clipboard'));
    return request('ui/message', {role: 'user', content: [{type: 'text', text: messageText}]});
  }

  return {connect, callTool, sendMessage, text, embedded, connected: () => hostConnected};
})();
