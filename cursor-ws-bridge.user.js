// ==UserScript==
// @name         Cursor WebSocket Bridge
// @namespace    https://tampermonkey.net/
// @version      1.0.0
// @description  Bridges Cursor's chat API to a WebSocket for OpenAI-compatible API access.
// @author       You
// @match        https://cursor.com/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(function () {
  'use strict';

  /** ========= 配置 ========= **/
  const WEBSOCKET_URL = 'ws://localhost:8765/ws';
  const DEBUG = true;
  const log = (...args) => { if (DEBUG) console.log('[cursor-ws-bridge]', ...args); };

  /** ========= WebSocket 连接管理 ========= **/
  let ws;
  function connect() {
    log(`正在连接 WebSocket: ${WEBSOCKET_URL}...`);
    ws = new WebSocket(WEBSOCKET_URL);

    ws.onopen = () => {
      log('WebSocket 连接已建立。');
      ws.send(JSON.stringify({ type: 'status', data: { status: 'ready' } }));
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        log('收到服务端消息:', message);
        if (message.type === 'chat_request') {
          handleChatRequest(message.data);
        }
      } catch (e) {
        log('解析服务端消息出错:', e);
      }
    };

    ws.onclose = (event) => {
      log(`WebSocket 连接已关闭 (code: ${event.code})。5秒后重新连接...`);
      setTimeout(connect, 5000);
    };

    ws.onerror = (error) => {
      log('WebSocket 发生错误:', error);
      // onerror 会自动触发 onclose，无需手动重连
    };
  }

  /** ========= 聊天请求处理 ========= **/
  async function handleChatRequest(requestData) {
    const { messages, model, request_id } = requestData;
    log(`处理聊天请求 ${request_id}, 模型: ${model}`);

    const listener = buildListenerForStreaming(request_id);
    window.__upstreamMessages = messages;

    const controller = new AbortController();

    try {
      await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          model: model || 'claude-sonnet-4-20250514',
          messages: [], // 会被拦截器替换
          trigger: 'submit-message',
          __rid: listener.rid,
        }),
        signal: controller.signal
      });
    } catch (error) {
      if (error.name !== 'AbortError') {
        log(`请求 ${request_id} 的 fetch 失败:`, error);
        sendError(request_id, `Fetch failed: ${error.message}`);
        cleanupListener(listener);
      }
    }
  }

  function sendData(payload) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  }

  function sendError(requestId, errorMessage) {
    sendData({
      type: 'error',
      data: {
        request_id: requestId,
        error: errorMessage
      }
    });
  }

  /** ========= SSE 流处理 ========= **/
  const bridge = { listeners: new Map() }; // rid -> listener

  function cleanupListener(listener) {
    if (listener) {
      bridge.listeners.delete(listener.rid);
    }
  }

  window.emitMeta = (meta) => {
    const rid = meta?.rid; if (!rid) return;
    const l = bridge.listeners.get(rid);
    if (l) {
      l.active = true;
      log(`[SSE] 元数据已关联, rid=${rid}, request_id=${l.request_id}`);
    }
  };

  window.emitDelta = (rid, delta) => {
    const l = bridge.listeners.get(rid);
    if (l?.active) l.onDelta(delta);
  };

  window.emitError = (rid, errorText) => {
    const l = bridge.listeners.get(rid);
    if (l?.active) l.onError(errorText);
  };

  window.emitDone = (rid) => {
    const l = bridge.listeners.get(rid);
    if (l?.active) l.onDone();
  };

  window.emitUsage = (rid, usage) => {
    const l = bridge.listeners.get(rid);
    if (l?.active) l.onUsage(usage);
  };

  function buildListenerForStreaming(request_id) {
    const rid = 'ws_req_' + Math.random().toString(16).slice(2);
    const listener = {
      rid,
      request_id,
      active: false,
      onDelta: (delta) => {
        sendData({ type: 'chat_delta', data: { request_id, content: delta } });
      },
      onError: (errorText) => {
        sendData({ type: 'error', data: { request_id, error: errorText } });
      },
      onUsage: (usage) => {
        sendData({ type: 'chat_usage', data: { request_id, usage } });
      },
      onDone: () => {
        log(`[SSE] 流结束, request_id=${request_id}`);
        sendData({ type: 'chat_done', data: { request_id } });
        listener.active = false;
        cleanupListener(listener);
      }
    };
    bridge.listeners.set(rid, listener);
    return listener;
  }

  /** ========= Fetch 拦截器 ========= **/
  const originalFetch = window.fetch;
  if (!originalFetch.__isBridge) {
    window.fetch = async function (input, init) {
      const url = typeof input === 'string' ? input : input?.url || '';
      const isChat = url.includes('/api/chat') && init?.method === 'POST';

      if (!isChat) {
        return originalFetch(input, init);
      }

      log('[Fetch Intercept] 拦截到 /api/chat 请求');
      let body;
      try {
        body = JSON.parse(init.body);
      } catch (e) {
        return originalFetch(input, init);
      }

      if (window.__upstreamMessages) {
        body.messages = (window.__upstreamMessages || []).map(m => ({
          role: m.role,
          parts: [{ type: 'text', text: String(m.content ?? '') }],
        }));
        log(`[Fetch Intercept] 已注入 ${body.messages.length} 条上游消息`);
        delete window.__upstreamMessages;
      }

      const newInit = { ...init, body: JSON.stringify(body) };
      const response = await originalFetch(input, newInit);

      // 克隆响应体用于 SSE 解析
      if (response.ok && response.body) {
        const streamClone = response.clone().body;
        parseSseStream(streamClone, body.__rid);
      }

      return response;
    };
    window.fetch.__isBridge = true;
  }

  /** ========= SSE 解析器 ========= **/
  async function parseSseStream(stream, rid) {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const processChunk = (chunk) => {
      const lines = chunk.split('\n').filter(line => line.startsWith('data:'));
      for (const line of lines) {
        const jsonStr = line.replace(/^data: /, '');
        if (jsonStr === '[DONE]') {
          window.emitDone(rid);
          return;
        }
        try {
          const event = JSON.parse(jsonStr);
          if (event.type === 'text-delta' && typeof event.delta === 'string') {
            window.emitDelta(rid, event.delta);
          } else if (event.type === 'error' && event.errorText) {
            window.emitError(rid, event.errorText);
          } else if (event.messageMetadata?.usage) {
            window.emitUsage(rid, event.messageMetadata.usage);
          } else if (['text-end', 'finish-step', 'finish'].includes(event.type)) {
            window.emitDone(rid);
          }
        } catch (e) {
          // 忽略解析错误
        }
      }
    };

    try {
      window.emitMeta({ rid });
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          window.emitDone(rid);
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const chunk = buffer.substring(0, boundary);
          buffer = buffer.substring(boundary + 2);
          processChunk(chunk);
        }
      }
    } catch (e) {
      log(`[SSE] 解析流时出错, rid=${rid}:`, e);
      window.emitDone(rid);
    }
  }

  /** ========= 启动 ========= **/
  connect();

})();
