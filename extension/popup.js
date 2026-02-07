/**
 * @typedef {{date: string, price: number}} PricePoint
 * @typedef {{
 *   productId: string,
 *   currency: string,
 *   days: number,
 *   points: PricePoint[],
 *   min: number,
 *   max: number,
 *   current: number,
 *   lastUpdated: string,
 *   source: 'mock'|'cached'
 * }} PriceHistoryResponse
 */

const STORAGE_KEY_API_BASE = 'procurewise_api_base';
const STORAGE_KEY_PAGE_CATALOG = 'procurewise_page_catalog';
const STORAGE_KEY_LAST_SESSION = 'procurewise_last_session';
const DEFAULT_API_BASE = 'http://localhost:8000';
const FETCH_TIMEOUT_MS = 8000;
const PAGE_RECOMMEND_TIMEOUT_MS = 60000;
const SNIPPET_MAX_LEN = 220;
const PAGE_CATALOG_MAX_ITEMS = 60;

const apiBaseEl = document.getElementById('apiBase');
const testConnectionBtn = document.getElementById('testConnection');
const storeEl = document.getElementById('store');
const queryEl = document.getElementById('query');
const recommendBtn = document.getElementById('recommend');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');
const scanPageBtn = document.getElementById('scanPage');
const scanStatusEl = document.getElementById('scanStatus');
const clearCatalogLink = document.getElementById('clearCatalog');
const clearSessionLink = document.getElementById('clearSession');
const historyProductIdEl = document.getElementById('historyProductId');
const loadPriceHistoryBtn = document.getElementById('loadPriceHistory');
const priceHistoryStatusEl = document.getElementById('priceHistoryStatus');
const priceHistoryStatsEl = document.getElementById('priceHistoryStats');
const priceHistoryChartWrapEl = document.getElementById('priceHistoryChartWrap');
const priceHistoryChartEl = document.getElementById('priceHistoryChart');

let pageCatalog = [];
let lastRequestDurationMs = null;
let lastHttpStatus = null;
let lastOverrideUsed = null;
let lastAssistantMode = null;
let sessionPollTimer = null;
let lastScanOrigin = '';
let lastResultPriceById = {};
let lastAutoHistoryKey = '';

function getApiBase() {
  const v = (apiBaseEl && apiBaseEl.value && apiBaseEl.value.trim()) || '';
  return v.replace(/\/+$/, '') || DEFAULT_API_BASE;
}

function loadSavedApiBase() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY_API_BASE);
    if (saved && saved.trim()) {
      apiBaseEl.value = saved.trim().replace(/\/+$/, '');
    } else {
      apiBaseEl.value = '';
      apiBaseEl.placeholder = DEFAULT_API_BASE;
    }
  } catch (_) {}
}

function saveApiBase() {
  try {
    localStorage.setItem(STORAGE_KEY_API_BASE, getApiBase());
  } catch (_) {}
}

function storageGet(key) {
  return new Promise(function (resolve) {
    try {
      chrome.storage.local.get([key], function (res) {
        resolve(res && res[key] ? res[key] : null);
      });
    } catch (_) {
      resolve(null);
    }
  });
}

function storageSet(key, value) {
  return new Promise(function (resolve) {
    try {
      var payload = {};
      payload[key] = value;
      chrome.storage.local.set(payload, function () { resolve(); });
    } catch (_) {
      resolve();
    }
  });
}

function storageRemove(key) {
  return new Promise(function (resolve) {
    try {
      chrome.storage.local.remove([key], function () { resolve(); });
    } catch (_) {
      resolve();
    }
  });
}

async function loadLastSession() {
  return await storageGet(STORAGE_KEY_LAST_SESSION);
}

async function saveSessionPatch(patch) {
  var prev = (await loadLastSession()) || {};
  var next = Object.assign({}, prev, patch || {}, { updatedAt: Date.now() });
  await storageSet(STORAGE_KEY_LAST_SESSION, next);
  return next;
}

function showStatus(message, type) {
  statusEl.textContent = message;
  statusEl.className = 'status ' + (type || '');
  statusEl.classList.remove('hidden');
}

function hideStatus() {
  statusEl.classList.add('hidden');
}

function friendlyErrorMessage(err) {
  const msg = (err && err.message) ? err.message : String(err);
  if (/failed to fetch|network|load/i.test(msg) || /TypeError/.test(err && err.name)) {
    return 'Backend isn\'t running. Start it with: ./scripts/run_backend.sh (in the project folder).';
  }
  if (/timeout|abort/i.test(msg)) {
    return 'Request timed out. Is the backend running? Try ./scripts/run_backend.sh';
  }
  return msg;
}

function safePreview(text) {
  return String(text || '').replace(/\s+/g, ' ').trim().slice(0, 200);
}

function normalizeTitleKey(value) {
  return String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
}

function compactDisplayTitle(value) {
  var raw = String(value || '');
  if (!raw) return '';
  var t = raw
    .replace(/\$?\s*\d+(?:[.,]\d+)?/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  var cutMarkers = [' with ', ' for ', ' featuring ', ' includes ', ' - ', ' — ', ' | ', ' / '];
  var lower = t.toLowerCase();
  for (var i = 0; i < cutMarkers.length; i++) {
    var m = cutMarkers[i];
    var idx = lower.indexOf(m);
    if (idx > 12) {
      t = t.slice(0, idx).trim();
      break;
    }
  }

  var words = t.split(/\s+/).filter(Boolean);
  if (words.length > 9) t = words.slice(0, 9).join(' ');
  return t;
}

function inferOriginFromCatalog(catalog) {
  if (!Array.isArray(catalog)) return '';
  for (var i = 0; i < catalog.length; i++) {
    var u = String((catalog[i] && catalog[i].url) || '');
    if (!u) continue;
    try {
      var origin = new URL(u).origin;
      if (origin) return origin;
    } catch (_) {}
  }
  return '';
}

function buildFallbackLink(title, store, scanOrigin) {
  var t = String(title || '').trim();
  if (!t) return null;
  var q = encodeURIComponent(t);
  var s = String(store || '').toLowerCase();
  if (s === 'amazon') return 'https://www.amazon.com/s?k=' + q;
  if (s === 'grainger') return 'https://www.grainger.com/search?searchQuery=' + q;
  if (s === 'page' && scanOrigin) {
    if (/amazon\./i.test(scanOrigin)) return scanOrigin + '/s?k=' + q;
    if (/grainger\./i.test(scanOrigin)) return scanOrigin + '/search?searchQuery=' + q;
    return scanOrigin + '/search?q=' + q;
  }
  return 'https://www.google.com/search?q=' + q;
}

function resolveItemUrl(item, urlMap, urlTitleMap, store, scanOrigin) {
  if (!item || typeof item !== 'object') return null;
  var id = String(item.id || '').trim();
  if (id && urlMap && urlMap[id]) return String(urlMap[id]);
  var titleKey = normalizeTitleKey(item.title || '');
  if (titleKey && urlTitleMap && urlTitleMap[titleKey]) return String(urlTitleMap[titleKey]);
  return buildFallbackLink(item.title || '', store, scanOrigin);
}

function renderResults(items, urlMap, urlTitleMap, store, scanOrigin) {
  resultsEl.innerHTML = '';
  lastResultPriceById = {};
  if (!items || items.length === 0) {
    resultsEl.innerHTML = '<li class="empty">No recommendations. Try a different query or store.</li>';
    return;
  }
  urlMap = urlMap || {};
  urlTitleMap = urlTitleMap || {};
  store = (store || '').toLowerCase();
  scanOrigin = scanOrigin || '';
  items.forEach(function (item) {
    var id = String(item.id || '').trim();
    if (id) {
      var maybePrice = parseNumericPrice(item.price);
      if (maybePrice != null) lastResultPriceById[id] = maybePrice;
    }
    const li = document.createElement('li');
    const title = escapeHtml(compactDisplayTitle(item.title || '') || (item.title || ''));
    const resolvedUrl = resolveItemUrl(item, urlMap, urlTitleMap, store, scanOrigin);
    const titleHtml = resolvedUrl
      ? '<a href="' + escapeHtml(resolvedUrl) + '" target="_blank" rel="noopener noreferrer">' + title + '</a>'
      : title;
    const priceStr = item.price != null && !isNaN(item.price) ? '$' + Number(item.price).toFixed(2) : '—';
    const bullets = (item.score_explanation && item.score_explanation.length)
      ? (Array.isArray(item.score_explanation) ? item.score_explanation.join(' ') : item.score_explanation)
      : (item.why || '');
    li.innerHTML = [
      '<span class="title">' + titleHtml + '</span>',
      '<div class="meta">',
      '  ' + priceStr,
      '  · ' + escapeHtml(item.category || '—'),
      (item.score != null ? '  <span class="score">score ' + Number(item.score).toFixed(2) + '</span>' : ''),
      '</div>',
      '<div class="why">' + escapeHtml(bullets) + '</div>'
    ].join('');
    resultsEl.appendChild(li);
  });
}

function escapeHtml(s) {
  if (s == null) return '';
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function showHistoryStatus(message, type) {
  if (!priceHistoryStatusEl) return;
  priceHistoryStatusEl.textContent = message || '';
  priceHistoryStatusEl.className = 'history-status ' + (type || '');
  priceHistoryStatusEl.classList.remove('hidden');
}

function hideHistoryStatus() {
  if (!priceHistoryStatusEl) return;
  priceHistoryStatusEl.classList.add('hidden');
}

function setHistoryStats(text) {
  if (!priceHistoryStatsEl) return;
  if (!text) {
    priceHistoryStatsEl.textContent = '';
    priceHistoryStatsEl.classList.add('hidden');
    return;
  }
  priceHistoryStatsEl.textContent = text;
  priceHistoryStatsEl.classList.remove('hidden');
}

function formatMoney(value, currency) {
  var n = Number(value);
  if (!isFinite(n)) return '—';
  var code = currency || 'USD';
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: code, maximumFractionDigits: 2 }).format(n);
  } catch (_) {
    return '$' + n.toFixed(2);
  }
}

function renderPriceHistoryChart(points) {
  if (!priceHistoryChartEl || !priceHistoryChartWrapEl) return;
  if (!Array.isArray(points) || points.length === 0) {
    priceHistoryChartEl.innerHTML = '';
    priceHistoryChartWrapEl.classList.add('hidden');
    return;
  }

  var width = 360;
  var height = 140;
  var padX = 16;
  var padY = 14;
  var innerW = width - (padX * 2);
  var innerH = height - (padY * 2);
  var prices = points.map(function (p) { return Number(p.price); }).filter(function (n) { return isFinite(n); });
  if (prices.length === 0) {
    priceHistoryChartEl.innerHTML = '';
    priceHistoryChartWrapEl.classList.add('hidden');
    return;
  }
  var min = Math.min.apply(null, prices);
  var max = Math.max.apply(null, prices);
  var range = Math.max(0.01, max - min);

  function xAt(i) {
    if (prices.length <= 1) return padX;
    return padX + ((innerW * i) / (prices.length - 1));
  }
  function yAt(v) {
    var t = (Number(v) - min) / range;
    return (height - padY) - (t * innerH);
  }

  var pointPairs = [];
  for (var i = 0; i < prices.length; i++) {
    pointPairs.push(xAt(i).toFixed(2) + ',' + yAt(prices[i]).toFixed(2));
  }
  var polyline = pointPairs.join(' ');
  var lastX = xAt(prices.length - 1).toFixed(2);
  var lastY = yAt(prices[prices.length - 1]).toFixed(2);

  priceHistoryChartEl.innerHTML = [
    '<line x1="' + padX + '" y1="' + (height - padY) + '" x2="' + (width - padX) + '" y2="' + (height - padY) + '" stroke="#d7dce5" stroke-width="1"/>',
    '<line x1="' + padX + '" y1="' + padY + '" x2="' + padX + '" y2="' + (height - padY) + '" stroke="#d7dce5" stroke-width="1"/>',
    '<polyline fill="none" stroke="#0d47a1" stroke-width="2" points="' + polyline + '"/>',
    '<circle cx="' + lastX + '" cy="' + lastY + '" r="3" fill="#1565c0"/>'
  ].join('');
  priceHistoryChartWrapEl.classList.remove('hidden');
}

function localMockPriceHistory(productId, days, currentPrice) {
  var nDays = Math.max(1, Number(days || 90));
  var base = Number(currentPrice);
  if (!isFinite(base) || base <= 0) base = 100;
  var floor = Math.max(1, base * 0.75);
  var ceil = Math.max(floor + 0.01, base * 1.25);
  var seed = 0;
  var text = String(productId || '') + '|' + String(nDays) + '|' + String(base.toFixed ? base.toFixed(2) : base);
  for (var i = 0; i < text.length; i++) {
    seed = ((seed * 31) + text.charCodeAt(i)) >>> 0;
  }
  function rand() {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  }
  var value = Math.max(floor, Math.min(ceil, base * (0.95 + (rand() * 0.1))));
  var points = [];
  var now = new Date();
  for (var d = nDays - 1; d >= 0; d--) {
    var dt = new Date(now);
    dt.setDate(now.getDate() - d);
    var revert = (base - value) * 0.1;
    var noise = ((rand() * 2) - 1) * (base * 0.012);
    var spike = rand() < 0.05 ? (((rand() * 2) - 1) * (base * 0.04)) : 0;
    value = Math.max(floor, Math.min(ceil, value + revert + noise + spike));
    points.push({
      date: dt.toISOString().slice(0, 10),
      price: Number(value.toFixed(2))
    });
  }
  return points;
}

function renderPriceHistoryResponse(data) {
  if (!data || !Array.isArray(data.points)) return;
  renderPriceHistoryChart(data.points);
  var stats = [
    'Current: ' + formatMoney(data.current, data.currency),
    'Min: ' + formatMoney(data.min, data.currency),
    'Max: ' + formatMoney(data.max, data.currency),
    'Source: ' + String(data.source || 'mock')
  ].join(' · ');
  setHistoryStats(stats);
}

function lookupResultPrice(productId) {
  if (!productId) return null;
  if (Object.prototype.hasOwnProperty.call(lastResultPriceById, productId)) {
    return lastResultPriceById[productId];
  }
  return null;
}

async function loadPriceHistory(productId, currentPrice) {
  var id = String(productId || '').trim();
  if (!id) {
    showHistoryStatus('Enter product ID first.', 'error');
    return;
  }
  if (loadPriceHistoryBtn) loadPriceHistoryBtn.disabled = true;
  showHistoryStatus('Loading price history…', 'loading');
  try {
    var apiBase = getApiBase();
    var url = apiBase + '/api/price-history?productId=' + encodeURIComponent(id) + '&days=90';
    var priceHint = currentPrice;
    if (priceHint == null) priceHint = lookupResultPrice(id);
    if (priceHint != null && isFinite(Number(priceHint))) {
      url += '&currentPrice=' + encodeURIComponent(String(Number(priceHint)));
    }
    var res = await fetchWithTimeout(url, { method: 'GET' }, 10000);
    if (!res.ok) {
      var raw = await res.text();
      throw new Error('Price history unavailable (HTTP ' + res.status + '): ' + safePreview(raw));
    }
    /** @type {PriceHistoryResponse} */
    var data = await res.json();
    renderPriceHistoryResponse(data);
    showHistoryStatus('Updated for ' + id + '.', '');
  } catch (e) {
    var fallbackPoints = localMockPriceHistory(id, 90, currentPrice);
    renderPriceHistoryResponse({
      productId: id,
      currency: 'USD',
      days: 90,
      points: fallbackPoints,
      min: Math.min.apply(null, fallbackPoints.map(function (p) { return p.price; })),
      max: Math.max.apply(null, fallbackPoints.map(function (p) { return p.price; })),
      current: fallbackPoints[fallbackPoints.length - 1].price,
      lastUpdated: new Date().toISOString(),
      source: 'mock'
    });
    showHistoryStatus('Server unavailable. Showing local demo history.', 'error');
  } finally {
    if (loadPriceHistoryBtn) loadPriceHistoryBtn.disabled = false;
  }
}

function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(timeoutId));
}

function requestAssistantInBackground(payload) {
  return new Promise(function (resolve, reject) {
    try {
      chrome.runtime.sendMessage({ action: 'assistantRecommend', payload: payload }, function (response) {
        var err = chrome.runtime.lastError;
        if (err) {
          reject(new Error(err.message || 'Background request failed'));
          return;
        }
        resolve(response || null);
      });
    } catch (e) {
      reject(e);
    }
  });
}

function parseNumericPrice(value) {
  if (value == null) return null;
  if (typeof value === 'number' && isFinite(value)) return value;
  const text = String(value);
  const m = text.replace(/,/g, '').match(/(\d+\.?\d*)/);
  if (!m) return null;
  const n = parseFloat(m[1]);
  return isFinite(n) ? n : null;
}

function mergeTitleAndSubtitle(title, subtitle) {
  var t = (title || '').trim();
  var s = (subtitle || '').trim();
  if (!t) return s;
  if (!s) return t;
  var tl = t.toLowerCase();
  var sl = s.toLowerCase();
  if (tl.indexOf(sl) >= 0 || sl.indexOf(tl) >= 0) return t.length >= s.length ? t : s;
  return (t + ' — ' + s);
}

function applySessionState(session, opts) {
  if (!session) return;
  opts = opts || {};
  var restoreInput = opts.restoreInput !== false;
  if (restoreInput && queryEl && session.queryText != null) queryEl.value = String(session.queryText || '');
  if (restoreInput && storeEl && session.store) {
    var sv = String(session.store || '').toLowerCase();
    if (sv) storeEl.value = sv;
  }

  lastRequestDurationMs = (session.lastRequestDurationMs != null) ? Number(session.lastRequestDurationMs) : null;
  lastHttpStatus = (session.lastHttpStatus != null) ? session.lastHttpStatus : null;
  lastOverrideUsed = (typeof session.lastOverrideUsed === 'boolean') ? !!session.lastOverrideUsed : null;
  lastAssistantMode = session.lastAssistantMode ? String(session.lastAssistantMode) : null;
  if (session.scanOrigin) {
    lastScanOrigin = String(session.scanOrigin || '');
  } else if (!lastScanOrigin) {
    lastScanOrigin = inferOriginFromCatalog(pageCatalog);
  }

  if (session.pending) {
    showStatus('Request in progress…', 'loading');
  } else if (session.statusMessage) {
    showStatus(String(session.statusMessage), String(session.statusType || 'error'));
  } else {
    hideStatus();
  }

  var results = Array.isArray(session.results) ? session.results : [];
  var urlMap = (session.urlMap && typeof session.urlMap === 'object') ? session.urlMap : {};
  var urlTitleMap = (session.urlTitleMap && typeof session.urlTitleMap === 'object') ? session.urlTitleMap : {};
  var renderStore = String(session.store || (storeEl ? storeEl.value : '') || '').toLowerCase();
  renderResults(results, urlMap, urlTitleMap, renderStore, lastScanOrigin);
  if (!session.pending && Array.isArray(results) && results.length > 0) {
    var top = results[0] || {};
    var topId = String(top.id || '').trim();
    var topPrice = parseNumericPrice(top.price);
    if (historyProductIdEl && topId) historyProductIdEl.value = topId;
    var key = topId + ':' + String(topPrice != null ? topPrice : '');
    if (topId && key !== lastAutoHistoryKey) {
      lastAutoHistoryKey = key;
      loadPriceHistory(topId, topPrice);
    }
  }
  updateDebugInfo();
}

async function startSessionPollingIfNeeded() {
  var session = await loadLastSession();
  if (!session || !session.pending) return;
  if (sessionPollTimer) return;
  sessionPollTimer = setInterval(async function () {
    var current = await loadLastSession();
    if (!current) return;
    applySessionState(current, { restoreInput: false });
    if (!current.pending && sessionPollTimer) {
      clearInterval(sessionPollTimer);
      sessionPollTimer = null;
    }
  }, 1500);
}

function loadPageCatalog() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PAGE_CATALOG);
    pageCatalog = raw ? JSON.parse(raw) : [];
  } catch (_) {
    pageCatalog = [];
  }
  lastScanOrigin = inferOriginFromCatalog(pageCatalog);
  updateScanStatus();
}

function savePageCatalog() {
  try {
    localStorage.setItem(STORAGE_KEY_PAGE_CATALOG, JSON.stringify(pageCatalog));
  } catch (_) {}
  updateScanStatus();
}

function updateScanStatus() {
  if (!scanStatusEl) return;
  if (pageCatalog.length === 0) {
    scanStatusEl.textContent = '';
    scanStatusEl.classList.add('hidden');
  } else {
    scanStatusEl.textContent = 'Scanned ' + pageCatalog.length + ' products';
    scanStatusEl.classList.remove('hidden');
  }
}

function buildCatalogOverridePayload() {
  return pageCatalog.slice(0, PAGE_CATALOG_MAX_ITEMS).map(function (item) {
    var snip = item.snippet || item.description || '';
    if (typeof snip === 'string' && snip.length > SNIPPET_MAX_LEN) snip = snip.slice(0, SNIPPET_MAX_LEN);
    var price = parseNumericPrice(item.price);
    var rating = parseNumericPrice(item.rating);
    var reviews = parseNumericPrice(item.reviews_count || item.review_count || item.reviews);
    return {
      id: item.id,
      title: item.title || '',
      price: price != null ? price : null,
      url: item.url || null,
      snippet: snip || null,
      rating: rating != null ? rating : null,
      reviews_count: reviews != null ? Math.round(reviews) : null
    };
  });
}

function setButtonsDisabled(disabled) {
  recommendBtn.disabled = disabled;
  if (scanPageBtn) scanPageBtn.disabled = disabled;
  if (loadPriceHistoryBtn) loadPriceHistoryBtn.disabled = disabled;
  var tc = document.getElementById('testConnection');
  if (tc) tc.disabled = disabled;
  var dp = document.getElementById('demoPreset');
  if (dp) dp.disabled = disabled;
}

function updateDebugInfo() {
  var el = document.getElementById('debugInfo');
  if (!el) return;
  var payload = buildCatalogOverridePayload();
  var payloadSize = JSON.stringify({ catalog_override: payload }).length;
  el.innerHTML = [
    'Scanned: ' + pageCatalog.length + ' products',
    'Payload: ~' + payloadSize + ' chars',
    lastRequestDurationMs != null ? 'Last request: ' + Math.round(lastRequestDurationMs) + ' ms' : 'Last request: —',
    lastHttpStatus != null ? 'Last HTTP: ' + lastHttpStatus : 'Last HTTP: —',
    lastOverrideUsed != null ? 'Override path: ' + (lastOverrideUsed ? 'yes' : 'no') : 'Override path: —',
    lastAssistantMode ? ('Assistant mode: ' + lastAssistantMode) : 'Assistant mode: —'
  ].join(' · ');
}

recommendBtn.addEventListener('click', async function () {
  var store = storeEl.value.trim().toLowerCase();
  var userText = queryEl.value.trim();
  if (!userText) {
    showStatus('Enter what you are looking for.', 'error');
    return;
  }
  if (store === 'page' && pageCatalog.length === 0) {
    showStatus('No scanned catalog found. Click "Scan this page" first.', 'error');
    return;
  }

  setButtonsDisabled(true);
  renderResults([]);
  saveApiBase();
  var apiBase = getApiBase();
  var usePageCatalog = (store === 'page' && pageCatalog.length > 0);
  var statusMsg = usePageCatalog ? 'Recommending…' : 'Loading…';
  showStatus(statusMsg, 'loading');

  lastHttpStatus = null;
  lastRequestDurationMs = null;
  lastOverrideUsed = null;
  lastAssistantMode = null;
  var t0 = Date.now();

  try {
    var urlMap = {};
    var urlTitleMap = {};
    var scanOrigin = lastScanOrigin || inferOriginFromCatalog(pageCatalog);
    pageCatalog.forEach(function (p) {
      if (p.id && p.url) urlMap[p.id] = p.url;
      var tk = normalizeTitleKey(p.title || '');
      if (tk && p.url) urlTitleMap[tk] = p.url;
    });
    var body = {
      user_text: userText,
      store: usePageCatalog ? 'page' : store,
      k: 8
    };
    if (usePageCatalog) {
      body.catalog_override = buildCatalogOverridePayload();
    }
    await saveSessionPatch({
      pending: true,
      queryText: userText,
      store: usePageCatalog ? 'page' : store,
      usePageCatalog: usePageCatalog,
      urlMap: usePageCatalog ? urlMap : {},
      urlTitleMap: usePageCatalog ? urlTitleMap : {},
      scanOrigin: usePageCatalog ? scanOrigin : '',
      results: [],
      statusMessage: 'Request in progress…',
      statusType: 'loading',
      lastHttpStatus: null,
      lastRequestDurationMs: null,
      lastOverrideUsed: null,
      lastAssistantMode: null,
      error: null
    });

    var bgResponse = await requestAssistantInBackground({
      apiBase: apiBase,
      body: body,
      timeoutMs: PAGE_RECOMMEND_TIMEOUT_MS,
      urlMap: usePageCatalog ? urlMap : {},
      urlTitleMap: usePageCatalog ? urlTitleMap : {},
      scanOrigin: usePageCatalog ? scanOrigin : '',
      queryText: userText,
      store: usePageCatalog ? 'page' : store,
      usePageCatalog: usePageCatalog
    });

    var session = (bgResponse && bgResponse.session) ? bgResponse.session : await loadLastSession();
    if (session) {
      applySessionState(session, { restoreInput: false });
      await startSessionPollingIfNeeded();
    } else {
      throw new Error('No response data');
    }
  } catch (e) {
    lastHttpStatus = lastHttpStatus || '—';
    lastRequestDurationMs = lastRequestDurationMs != null ? lastRequestDurationMs : Date.now() - t0;
    await saveSessionPatch({
      pending: false,
      queryText: userText,
      store: usePageCatalog ? 'page' : store,
      usePageCatalog: usePageCatalog,
      urlMap: usePageCatalog ? urlMap : {},
      urlTitleMap: usePageCatalog ? urlTitleMap : {},
      scanOrigin: usePageCatalog ? scanOrigin : '',
      statusMessage: (e && e.message) ? e.message : friendlyErrorMessage(e),
      statusType: 'error',
      lastHttpStatus: lastHttpStatus,
      lastRequestDurationMs: lastRequestDurationMs,
      error: { code: 'POPUP_ERROR', message: (e && e.message) ? e.message : friendlyErrorMessage(e) }
    });
    updateDebugInfo();
    showStatus((e && e.message) ? e.message : friendlyErrorMessage(e), 'error');
    renderResults([], {}, {}, usePageCatalog ? 'page' : store, scanOrigin);
  } finally {
    setButtonsDisabled(false);
  }
});

if (testConnectionBtn) {
  testConnectionBtn.addEventListener('click', async () => {
    saveApiBase();
    const apiBase = getApiBase();
    testConnectionBtn.disabled = true;
    showStatus('Checking…', 'loading');
    try {
      const res = await fetchWithTimeout(apiBase + '/health', { method: 'GET' }, 5000);
      const data = res.ok ? await res.json().catch(() => ({})) : null;
      if (res.ok && data && data.status === 'ok') {
        showStatus('Connected: ' + apiBase, '');
      } else {
        showStatus('Unexpected response: ' + res.status, 'error');
      }
    } catch (e) {
      showStatus(friendlyErrorMessage(e), 'error');
    } finally {
      testConnectionBtn.disabled = false;
    }
  });
}

if (loadPriceHistoryBtn) {
  loadPriceHistoryBtn.addEventListener('click', async function () {
    var id = historyProductIdEl ? historyProductIdEl.value : '';
    var price = lookupResultPrice(String(id || '').trim());
    await loadPriceHistory(id, price);
  });
}

document.addEventListener('DOMContentLoaded', async function () {
  loadSavedApiBase();
  loadPageCatalog();
  hideHistoryStatus();
  setHistoryStats('');
  renderPriceHistoryChart([]);
  var session = await loadLastSession();
  if (session) {
    applySessionState(session, { restoreInput: true });
    await startSessionPollingIfNeeded();
  }
  updateDebugInfo();
});

if (scanPageBtn) {
  scanPageBtn.addEventListener('click', async function () {
    scanPageBtn.disabled = true;
    showStatus('Scanning page…', 'loading');
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab || !tab.id) {
        showStatus('No active tab. Open a shopping page and try again.', 'error');
        return;
      }
      var scanOrigin = '';
      try {
        scanOrigin = tab.url ? (new URL(tab.url)).origin : '';
      } catch (_) {
        scanOrigin = '';
      }
      if (scanOrigin) lastScanOrigin = scanOrigin;
      const response = await chrome.tabs.sendMessage(tab.id, { action: 'extractProducts' });
      const items = (response && response.items) ? response.items : [];
      if (response && response.error) {
        showStatus('Could not extract products from this page. Try a product listing (e.g. Amazon search).', 'error');
        pageCatalog = [];
      } else if (items.length === 0) {
        showStatus('No product cards found on this page. Try a search results or category page.', 'error');
        pageCatalog = [];
      } else {
        var snip = function (s) {
          if (s == null) return null;
          s = String(s);
          return s.length > SNIPPET_MAX_LEN ? s.slice(0, SNIPPET_MAX_LEN) : s;
        };
        pageCatalog = items.slice(0, PAGE_CATALOG_MAX_ITEMS).map(function (it, i) {
          var subtitle = snip(it.subtitle || null);
          var snippet = snip(it.snippet || it.description || subtitle) || null;
          var title = mergeTitleAndSubtitle(it.title || '', subtitle || '');
          var itemUrl = it.url || null;
          if (!itemUrl && title) {
            itemUrl = buildFallbackLink(title, 'page', lastScanOrigin || scanOrigin || '');
          }
          return {
            id: it.id || ('p' + i),
            title: title || '',
            price: parseNumericPrice(it.price),
            url: itemUrl,
            snippet: snippet,
            rating: parseNumericPrice(it.rating),
            reviews_count: parseNumericPrice(it.reviews_count || it.review_count || it.reviews)
          };
        });
        savePageCatalog();
        showStatus('Scanned ' + pageCatalog.length + ' products', '');
        setTimeout(hideStatus, 2500);
      }
    } catch (e) {
      showStatus('Scan failed. Open Amazon or Grainger (or a product listing) and try again.', 'error');
      pageCatalog = [];
    } finally {
      scanPageBtn.disabled = false;
    }
  });
}

if (clearCatalogLink) {
  clearCatalogLink.addEventListener('click', function (e) {
    e.preventDefault();
    pageCatalog = [];
    lastScanOrigin = '';
    savePageCatalog();
    showStatus('Scanned catalog cleared', '');
    setTimeout(hideStatus, 2000);
  });
}

if (clearSessionLink) {
  clearSessionLink.addEventListener('click', async function (e) {
    e.preventDefault();
    if (sessionPollTimer) {
      clearInterval(sessionPollTimer);
      sessionPollTimer = null;
    }
    await storageRemove(STORAGE_KEY_LAST_SESSION);
    lastRequestDurationMs = null;
    lastHttpStatus = null;
    lastOverrideUsed = null;
    lastAssistantMode = null;
    lastResultPriceById = {};
    lastAutoHistoryKey = '';
    if (queryEl) queryEl.value = '';
    if (historyProductIdEl) historyProductIdEl.value = '';
    hideHistoryStatus();
    setHistoryStats('');
    renderPriceHistoryChart([]);
    renderResults([], {}, {}, (storeEl && storeEl.value) ? String(storeEl.value).toLowerCase() : '', lastScanOrigin);
    hideStatus();
    updateDebugInfo();
    showStatus('Prompt and results cleared', '');
    setTimeout(hideStatus, 1800);
  });
}

var demoPresetBtn = document.getElementById('demoPreset');
if (demoPresetBtn) {
  demoPresetBtn.addEventListener('click', function () {
    if (queryEl) queryEl.value = 'office chair under $150';
    if (storeEl) storeEl.value = 'amazon';
    saveSessionPatch({ queryText: queryEl ? queryEl.value : '', store: 'amazon' });
    showStatus('Demo preset applied', '');
    setTimeout(hideStatus, 2000);
  });
}

if (queryEl) {
  queryEl.addEventListener('input', function () {
    saveSessionPatch({ queryText: queryEl.value || '' });
  });
}

if (storeEl) {
  storeEl.addEventListener('change', function () {
    saveSessionPatch({ store: (storeEl.value || '').toLowerCase() });
  });
}
