const STORAGE_KEY_API_BASE = 'procurewise_api_base';
const STORAGE_KEY_PAGE_CATALOG = 'procurewise_page_catalog';
const STORAGE_KEY_LAST_SESSION = 'procurewise_last_session';
const STORAGE_KEY_THEME = 'buysmart_theme';
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
const themeToggleEl = document.getElementById('themeToggle');

let pageCatalog = [];
let lastRequestDurationMs = null;
let lastHttpStatus = null;
let lastOverrideUsed = null;
let lastAssistantMode = null;
let sessionPollTimer = null;
let lastScanOrigin = '';
let lastResultPriceById = {};
let lastResultMetaById = {};
let lastResultUrlById = {};
let lastResultUrlByTitle = {};
let lastResultImageById = {};
let lastResultImageByTitle = {};
let lastRenderedStore = '';
let lastRenderedScanOrigin = '';
let lastRenderedCards = [];
let priceHistoryModal = null;
let timingModal = null;
let valueChartModal = null;

function getApiBase() {
  const fromInput = (apiBaseEl && apiBaseEl.value && apiBaseEl.value.trim()) || '';
  if (fromInput) {
    return fromInput.replace(/\/+$/, '');
  }
  try {
    const saved = localStorage.getItem(STORAGE_KEY_API_BASE) || '';
    if (saved.trim()) {
      return saved.trim().replace(/\/+$/, '');
    }
  } catch (_) {}
  return DEFAULT_API_BASE;
}

function loadSavedApiBase() {
  if (!apiBaseEl) return;
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
  if (!apiBaseEl) return;
  try {
    localStorage.setItem(STORAGE_KEY_API_BASE, getApiBase());
  } catch (_) {}
}

function getSavedTheme() {
  try {
    var saved = localStorage.getItem(STORAGE_KEY_THEME);
    if (saved === 'dark' || saved === 'light') return saved;
  } catch (_) {}
  return 'light';
}

function applyTheme(theme) {
  var t = (theme === 'dark') ? 'dark' : 'light';
  try {
    document.documentElement.setAttribute('data-theme', t);
  } catch (_) {}
  if (themeToggleEl) {
    themeToggleEl.checked = (t === 'dark');
  }
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

function buildUniqueTitleUrlMap(items) {
  var map = {};
  var counts = {};
  (items || []).forEach(function (p) {
    var key = normalizeTitleKey(p && p.title ? p.title : '');
    if (!key) return;
    counts[key] = (counts[key] || 0) + 1;
    if (!map[key] && p && p.url) map[key] = String(p.url);
  });
  Object.keys(map).forEach(function (key) {
    if ((counts[key] || 0) > 1) delete map[key];
  });
  return map;
}

function normalizeImageUrl(value) {
  var url = String(value || '').trim();
  if (!url) return '';
  try {
    if (url.startsWith('//')) return window.location.protocol + url;
    if (url.startsWith('/')) return new URL(url, window.location.origin).href;
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
  } catch (_) {}
  return '';
}

function buildUniqueTitleImageMap(items) {
  var map = {};
  var counts = {};
  (items || []).forEach(function (p) {
    var key = normalizeTitleKey(p && p.title ? p.title : '');
    if (!key) return;
    counts[key] = (counts[key] || 0) + 1;
    var image = normalizeImageUrl(p && p.image ? p.image : '');
    if (!map[key] && image) map[key] = image;
  });
  Object.keys(map).forEach(function (key) {
    if ((counts[key] || 0) > 1) delete map[key];
  });
  return map;
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
  var direct = String(item.url || '').trim();
  if (direct) return direct;
  var id = String(item.id || '').trim();
  if (id && urlMap && urlMap[id]) return String(urlMap[id]);
  var titleKey = normalizeTitleKey(item.title || '');
  if (titleKey && urlTitleMap && urlTitleMap[titleKey]) return String(urlTitleMap[titleKey]);
  if (String(store || '').toLowerCase() === 'page') return null;
  return buildFallbackLink(item.title || '', store, scanOrigin);
}

function resolveItemImage(item, imageMap, imageTitleMap) {
  if (!item || typeof item !== 'object') return '';
  var direct = normalizeImageUrl(item.image || '');
  if (direct) return direct;
  var id = String(item.id || '').trim();
  if (id && imageMap && imageMap[id]) return String(imageMap[id]);
  var titleKey = normalizeTitleKey(item.title || '');
  if (titleKey && imageTitleMap && imageTitleMap[titleKey]) return String(imageTitleMap[titleKey]);
  return '';
}

function renderResults(items, urlMap, urlTitleMap, store, scanOrigin) {
  resultsEl.innerHTML = '';
  lastResultPriceById = {};
  lastResultMetaById = {};
  lastResultUrlById = {};
  lastResultUrlByTitle = {};
  lastResultImageById = {};
  lastResultImageByTitle = {};
  lastRenderedCards = [];
  lastRenderedStore = String(store || '').toLowerCase();
  lastRenderedScanOrigin = String(scanOrigin || '');
  if (!items || items.length === 0) {
    resultsEl.innerHTML = '<li class="empty">No recommendations. Try a different query or store.</li>';
    return;
  }
  urlMap = urlMap || {};
  urlTitleMap = urlTitleMap || {};
  store = (store || '').toLowerCase();
  scanOrigin = scanOrigin || '';
  var imageMap = {};
  (pageCatalog || []).forEach(function (p) {
    var pid = String((p && p.id) || '').trim();
    var pimg = normalizeImageUrl(p && p.image ? p.image : '');
    if (pid && pimg) imageMap[pid] = pimg;
  });
  var imageTitleMap = buildUniqueTitleImageMap(pageCatalog || []);
  items.forEach(function (item) {
    var id = String(item.id || '').trim();
    var maybePrice = parseNumericPrice(item.price);
    var maybeRating = parseNumericPrice(item.rating);
    var maybeReviews = parseNumericPrice(item.reviewCount || item.reviews_count || item.review_count || item.reviews);
    if (id && maybePrice != null) lastResultPriceById[id] = maybePrice;
    if (id) {
      lastResultMetaById[id] = {
        title: String(item.title || ''),
        category: String(item.category || ''),
        rating: maybeRating,
        reviewCount: maybeReviews != null ? Math.round(maybeReviews) : null
      };
    }

    const li = document.createElement('li');
    li.className = 'product-card';

    const main = document.createElement('div');
    main.className = 'product-card-main';

    const info = document.createElement('div');
    info.className = 'product-info';

    const titleContainer = document.createElement('div');
    titleContainer.className = 'title';
    const titleText = compactDisplayTitle(item.title || '') || (item.title || '');
    const resolvedUrl = resolveItemUrl(item, urlMap, urlTitleMap, store, scanOrigin);
    const resolvedImage = resolveItemImage(item, imageMap, imageTitleMap);
    if (id && resolvedUrl) {
      lastResultUrlById[id] = String(resolvedUrl);
    }
    var normalizedTitle = normalizeTitleKey(item.title || '');
    if (normalizedTitle && resolvedUrl && !lastResultUrlByTitle[normalizedTitle]) {
      lastResultUrlByTitle[normalizedTitle] = String(resolvedUrl);
    }
    if (id && resolvedImage) {
      lastResultImageById[id] = String(resolvedImage);
    }
    if (normalizedTitle && resolvedImage && !lastResultImageByTitle[normalizedTitle]) {
      lastResultImageByTitle[normalizedTitle] = String(resolvedImage);
    }
    lastRenderedCards.push({
      id: id || '',
      title: String(item.title || ''),
      price: maybePrice != null ? Number(maybePrice) : null,
      rating: maybeRating != null ? Number(maybeRating) : null,
      reviewCount: maybeReviews != null ? Math.round(Number(maybeReviews)) : null,
      image: resolvedImage ? String(resolvedImage) : null,
      url: resolvedUrl ? String(resolvedUrl) : null,
      category: String(item.category || '')
    });
    if (resolvedUrl) {
      const link = document.createElement('a');
      link.href = resolvedUrl;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = titleText;
      titleContainer.appendChild(link);
    } else {
      titleContainer.textContent = titleText;
    }

    const meta = document.createElement('div');
    meta.className = 'meta';
    const priceStr = maybePrice != null ? ('$' + Number(maybePrice).toFixed(2)) : '—';
    const scorePart = item.score != null ? (' · score ' + Number(item.score).toFixed(2)) : '';
    meta.textContent = priceStr + ' · ' + String(item.category || '—') + scorePart;

    const why = document.createElement('div');
    why.className = 'why';
    const bullets = (item.score_explanation && item.score_explanation.length)
      ? (Array.isArray(item.score_explanation) ? item.score_explanation.join(' ') : item.score_explanation)
      : (item.why || '');
    why.textContent = bullets;

    info.appendChild(titleContainer);
    info.appendChild(meta);
    info.appendChild(why);

    if (resolvedImage) {
      const media = document.createElement('div');
      media.className = 'product-media';
      const img = document.createElement('img');
      img.className = 'product-image';
      img.src = resolvedImage;
      img.alt = titleText || 'Product image';
      img.loading = 'lazy';
      img.referrerPolicy = 'no-referrer';
      img.addEventListener('error', function () {
        media.remove();
      });
      media.appendChild(img);
      main.appendChild(media);
    }

    const actions = document.createElement('div');
    actions.className = 'product-actions';

    if (resolvedUrl) {
      const openLink = document.createElement('a');
      openLink.className = 'action-btn';
      openLink.href = resolvedUrl;
      openLink.target = '_blank';
      openLink.rel = 'noopener noreferrer';
      openLink.textContent = 'Open';
      actions.appendChild(openLink);
    } else {
      const openDisabled = document.createElement('button');
      openDisabled.type = 'button';
      openDisabled.className = 'action-btn disabled';
      openDisabled.disabled = true;
      openDisabled.textContent = 'Open';
      actions.appendChild(openDisabled);
    }

    const priceBtn = document.createElement('button');
    priceBtn.type = 'button';
    priceBtn.className = 'action-btn secondary';
    priceBtn.textContent = '📈 3M Price';
    if (!id) {
      priceBtn.disabled = true;
      priceBtn.classList.add('disabled');
    } else {
      priceBtn.addEventListener('click', function () {
        if (!priceHistoryModal) {
          showStatus('Price history modal is unavailable.', 'error');
          return;
        }
        priceHistoryModal.open({
          productId: id,
          currentPrice: maybePrice,
          title: String(item.title || '')
        });
      });
    }
    actions.appendChild(priceBtn);

    const explainBtn = document.createElement('button');
    explainBtn.type = 'button';
    explainBtn.className = 'action-btn secondary';
    explainBtn.textContent = '⏳ Timing';
    if (!id) {
      explainBtn.disabled = true;
      explainBtn.classList.add('disabled');
    } else {
      explainBtn.addEventListener('click', function () {
        if (!timingModal) {
          showStatus('Timing modal is unavailable.', 'error');
          return;
        }
        var meta = lastResultMetaById[id] || {};
        timingModal.open({
          productId: id,
          currentPrice: maybePrice,
          title: String(meta.title || item.title || ''),
          category: String(meta.category || item.category || '')
        });
      });
    }
    actions.appendChild(explainBtn);

    const valueBtn = document.createElement('button');
    valueBtn.type = 'button';
    valueBtn.className = 'action-btn secondary';
    valueBtn.textContent = '📊 Value';
    if (!id) {
      valueBtn.disabled = true;
      valueBtn.classList.add('disabled');
    } else {
      valueBtn.addEventListener('click', function () {
        if (!valueChartModal) {
          showStatus('Value chart modal is unavailable.', 'error');
          return;
        }
        var meta = lastResultMetaById[id] || {};
        valueChartModal.open({
          productId: id,
          currentPrice: maybePrice,
          title: String(meta.title || item.title || ''),
          category: String(meta.category || item.category || ''),
          rating: meta.rating,
          reviewCount: meta.reviewCount,
          store: store,
          scanOrigin: scanOrigin,
          localComparables: buildValueChartComparables(
            id,
            String(meta.title || item.title || ''),
            maybePrice,
            String(meta.category || item.category || ''),
            meta.rating,
            meta.reviewCount
          )
        });
      });
    }
    actions.appendChild(valueBtn);

    const placeholderBtn = document.createElement('button');
    placeholderBtn.type = 'button';
    placeholderBtn.className = 'action-btn secondary disabled';
    placeholderBtn.disabled = true;
    placeholderBtn.textContent = 'Explain';
    actions.appendChild(placeholderBtn);

    main.appendChild(info);
    main.appendChild(actions);
    li.appendChild(main);
    resultsEl.appendChild(li);
  });
}

function escapeHtml(s) {
  if (s == null) return '';
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
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
  var urlTitleMap = (session.urlTitleMap && typeof session.urlTitleMap === 'object')
    ? session.urlTitleMap
    : buildUniqueTitleUrlMap(pageCatalog);
  var renderStore = String(session.store || (storeEl ? storeEl.value : '') || '').toLowerCase();
  renderResults(results, urlMap, urlTitleMap, renderStore, lastScanOrigin);
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
      image: normalizeImageUrl(item.image || '') || null,
      snippet: snip || null,
      rating: rating != null ? rating : null,
      reviews_count: reviews != null ? Math.round(reviews) : null
    };
  });
}

function setButtonsDisabled(disabled) {
  recommendBtn.disabled = disabled;
  if (scanPageBtn) scanPageBtn.disabled = disabled;
  var tc = document.getElementById('testConnection');
  if (tc) tc.disabled = disabled;
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

function resolveValuePointUrl(point, context) {
  point = point || {};
  context = context || {};
  var id = String(point.id || '').trim();
  var title = String(point.title || '').trim();
  var url = String(point.url || '').trim();
  if (url) return url;
  if (id && lastResultUrlById[id]) return String(lastResultUrlById[id]);
  var tk = normalizeTitleKey(title);
  if (tk && lastResultUrlByTitle[tk]) return String(lastResultUrlByTitle[tk]);

  var store = String(context.store || lastRenderedStore || (storeEl && storeEl.value) || '').toLowerCase();
  var origin = String(context.scanOrigin || lastRenderedScanOrigin || lastScanOrigin || '');
  return buildFallbackLink(title, store, origin);
}

function buildValueChartComparables(currentId, currentTitle, currentPrice, currentCategory, currentRating, currentReviewCount) {
  var comparables = [];
  var seen = {};

  function addComparable(raw) {
    if (!raw || typeof raw !== 'object') return;
    var id = String(raw.id || '').trim();
    var title = String(raw.title || '').trim();
    var key = id || normalizeTitleKey(title);
    if (!key || seen[key]) return;
    var price = parseNumericPrice(raw.price);
    if (price == null || !isFinite(price) || price <= 0) return;
    seen[key] = true;
    comparables.push({
      id: id || key,
      title: title || 'Product',
      price: Number(price),
      rating: parseNumericPrice(raw.rating),
      reviewCount: parseNumericPrice(raw.reviewCount || raw.reviews_count || raw.review_count || raw.reviews),
      url: raw.url ? String(raw.url) : null,
      category: raw.category ? String(raw.category) : null
    });
  }

  if (Array.isArray(pageCatalog) && pageCatalog.length > 0) {
    pageCatalog.forEach(addComparable);
  }

  if (comparables.length < 5 && Array.isArray(lastRenderedCards) && lastRenderedCards.length > 0) {
    lastRenderedCards.forEach(addComparable);
  }

  addComparable({
    id: currentId,
    title: currentTitle,
    price: currentPrice,
    rating: currentRating,
    reviewCount: currentReviewCount,
    category: currentCategory,
    url: (currentId && lastResultUrlById[currentId]) ? lastResultUrlById[currentId] : null
  });

  return comparables;
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
    var urlTitleMap = buildUniqueTitleUrlMap(pageCatalog);
    var scanOrigin = lastScanOrigin || inferOriginFromCatalog(pageCatalog);
    pageCatalog.forEach(function (p) {
      if (p.id && p.url) urlMap[p.id] = p.url;
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

document.addEventListener('DOMContentLoaded', async function () {
  applyTheme(getSavedTheme());
  if (themeToggleEl) {
    themeToggleEl.addEventListener('change', function () {
      var nextTheme = themeToggleEl.checked ? 'dark' : 'light';
      applyTheme(nextTheme);
      try {
        localStorage.setItem(STORAGE_KEY_THEME, nextTheme);
      } catch (_) {}
    });
  }
  loadSavedApiBase();
  loadPageCatalog();
  if (window.PriceHistoryModal) {
    priceHistoryModal = new window.PriceHistoryModal({
      getApiBase: getApiBase,
      fetchWithTimeout: fetchWithTimeout,
      onError: function (message) {
        if (message) showStatus(String(message), 'error');
      }
    });
  }
  if (window.BuyTimingModal) {
    timingModal = new window.BuyTimingModal({
      getApiBase: getApiBase,
      fetchWithTimeout: fetchWithTimeout,
      onError: function (message) {
        if (message) showStatus(String(message), 'error');
      }
    });
  }
  if (window.ValueChartModal) {
    valueChartModal = new window.ValueChartModal({
      getApiBase: getApiBase,
      fetchWithTimeout: fetchWithTimeout,
      resolveProductUrl: resolveValuePointUrl,
      onError: function (message) {
        if (message) showStatus(String(message), 'error');
      }
    });
  }
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
          var itemImage = normalizeImageUrl(it.image || '') || null;
          if (!itemUrl && title) {
            itemUrl = buildFallbackLink(title, 'page', lastScanOrigin || scanOrigin || '');
          }
          return {
            id: it.id || ('p' + i),
            title: title || '',
            price: parseNumericPrice(it.price),
            url: itemUrl,
            image: itemImage,
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
    lastResultMetaById = {};
    if (queryEl) queryEl.value = '';
    if (priceHistoryModal) priceHistoryModal.close();
    if (timingModal) timingModal.close();
    if (valueChartModal) valueChartModal.close();
    renderResults([], {}, {}, (storeEl && storeEl.value) ? String(storeEl.value).toLowerCase() : '', lastScanOrigin);
    hideStatus();
    updateDebugInfo();
    showStatus('Prompt and results cleared', '');
    setTimeout(hideStatus, 1800);
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
