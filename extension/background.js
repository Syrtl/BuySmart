const STORAGE_KEY_LAST_SESSION = 'procurewise_last_session';

function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(timeoutId));
}

function storageGet(key) {
  return new Promise((resolve) => {
    chrome.storage.local.get([key], (res) => resolve(res[key] || null));
  });
}

function storageSet(key, value) {
  return new Promise((resolve) => {
    const payload = {};
    payload[key] = value;
    chrome.storage.local.set(payload, () => resolve());
  });
}

async function saveSessionPatch(patch) {
  const prev = (await storageGet(STORAGE_KEY_LAST_SESSION)) || {};
  const next = { ...prev, ...patch, updatedAt: Date.now() };
  await storageSet(STORAGE_KEY_LAST_SESSION, next);
  return next;
}

function safePreview(text) {
  const raw = String(text || '');
  return raw.replace(/\s+/g, ' ').trim().slice(0, 200);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.action !== 'assistantRecommend') return;

  (async () => {
    const payload = message.payload || {};
    const apiBase = String(payload.apiBase || '').replace(/\/+$/, '');
    const body = payload.body || {};
    const timeoutMs = Number(payload.timeoutMs || 60000);
    const urlMap = payload.urlMap || {};
    const urlTitleMap = payload.urlTitleMap || {};
    const scanOrigin = String(payload.scanOrigin || '');
    const queryText = String(payload.queryText || '');
    const store = String(payload.store || '');
    const usePageCatalog = !!payload.usePageCatalog;
    const startedAt = Date.now();

    await saveSessionPatch({
      pending: true,
      queryText,
      store,
      usePageCatalog,
      urlMap,
      urlTitleMap,
      scanOrigin,
      error: null,
      statusMessage: 'Request in progress…',
      statusType: 'loading',
    });

    try {
      const res = await fetchWithTimeout(
        apiBase + '/assistant/recommend',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
        timeoutMs
      );
      const rawText = await res.text();
      let data = null;
      try {
        data = rawText ? JSON.parse(rawText) : null;
      } catch (_) {
        data = null;
      }

      const durationMs = Date.now() - startedAt;
      const recs = (data && Array.isArray(data.recommendations)) ? data.recommendations : [];
      const usingOverride = data && typeof data.using_override === 'boolean' ? !!data.using_override : null;
      const assistantMode = data && data.assistant_mode ? String(data.assistant_mode) : null;

      let statusMessage = '';
      let statusType = '';
      if (!res.ok) {
        statusMessage = 'HTTP ' + res.status + (rawText ? (': ' + safePreview(rawText)) : '');
        statusType = 'error';
      } else if (data && data.error && data.error.message) {
        statusMessage = String(data.error.message);
        statusType = 'error';
      } else if (recs.length === 0 && data && data.follow_up_question) {
        statusMessage = String(data.follow_up_question);
        statusType = 'error';
      }

      const session = await saveSessionPatch({
        pending: false,
        queryText,
        store,
        usePageCatalog,
        urlMap,
        urlTitleMap,
        scanOrigin,
        results: recs,
        lastHttpStatus: res.status,
        lastRequestDurationMs: durationMs,
        lastOverrideUsed: usingOverride,
        lastAssistantMode: assistantMode,
        followUpQuestion: data ? data.follow_up_question || null : null,
        parsedRequest: data ? data.parsed_request || null : null,
        statusMessage,
        statusType,
        error: (!res.ok) ? { code: 'HTTP_ERROR', message: statusMessage } : (data && data.error ? data.error : null),
      });

      sendResponse({
        ok: res.ok,
        httpStatus: res.status,
        data,
        rawText,
        session,
      });
    } catch (err) {
      const durationMs = Date.now() - startedAt;
      const messageText = (err && err.message) ? String(err.message) : 'Request failed';
      const session = await saveSessionPatch({
        pending: false,
        queryText,
        store,
        usePageCatalog,
        urlMap,
        urlTitleMap,
        scanOrigin,
        results: [],
        lastHttpStatus: null,
        lastRequestDurationMs: durationMs,
        statusMessage: messageText,
        statusType: 'error',
        error: { code: 'NETWORK_ERROR', message: messageText },
      });
      sendResponse({
        ok: false,
        error: messageText,
        session,
      });
    }
  })();

  return true;
});
