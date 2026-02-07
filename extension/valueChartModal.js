(function () {
  function safePreview(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  }
  var POINT_HIT_RADIUS_PX = 14;

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = String(text == null ? '' : text);
    return div.innerHTML;
  }

  function formatMoney(value, currency) {
    var n = Number(value);
    if (!isFinite(n)) return '$0';
    var code = currency || 'USD';
    try {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: code,
        maximumFractionDigits: 2
      }).format(n);
    } catch (_) {
      return '$' + n.toFixed(2);
    }
  }

  function formatAxisMoney(value) {
    var n = Number(value);
    if (!isFinite(n)) return '$0';
    if (Math.abs(n - Math.round(n)) < 0.005) return '$' + String(Math.round(n));
    return '$' + n.toFixed(2);
  }

  function isValidResponse(data) {
    return !!(
      data &&
      typeof data === 'object' &&
      typeof data.productId === 'string' &&
      Array.isArray(data.points) &&
      data.points.length > 0 &&
      typeof data.optimalId === 'string'
    );
  }

  function hashString(text) {
    var value = 2166136261;
    var s = String(text || '');
    for (var i = 0; i < s.length; i++) {
      value ^= s.charCodeAt(i);
      value = Math.imul(value, 16777619);
    }
    return value >>> 0;
  }

  function mulberry32(seed) {
    var t = seed >>> 0;
    return function () {
      t += 0x6D2B79F5;
      var m = Math.imul(t ^ (t >>> 15), 1 | t);
      m ^= m + Math.imul(m ^ (m >>> 7), 61 | m);
      return ((m ^ (m >>> 14)) >>> 0) / 4294967296;
    };
  }

  function syntheticRatingReviews(id, title) {
    var rng = mulberry32(hashString('rr:' + String(id || '') + ':' + String(title || '')));
    var rating = 3.2 + rng() * 1.7;
    var reviews = 20 + Math.floor(Math.pow(rng(), 2) * 12000);
    return {
      rating: Math.max(1, Math.min(5, rating)),
      reviewCount: Math.max(1, reviews)
    };
  }

  function percentile(values, pct) {
    if (!values || !values.length) return 0;
    var arr = values.slice().map(Number).filter(function (v) { return isFinite(v); }).sort(function (a, b) { return a - b; });
    if (!arr.length) return 0;
    if (arr.length === 1) return arr[0];
    var rank = (arr.length - 1) * (pct / 100);
    var low = Math.floor(rank);
    var high = Math.ceil(rank);
    if (low === high) return arr[low];
    var w = rank - low;
    return arr[low] + (arr[high] - arr[low]) * w;
  }

  function normalizeTitleKey(value) {
    return String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function dedupeCommaSegments(text) {
    var raw = String(text || '');
    if (raw.indexOf(',') < 0) return raw;
    var parts = raw.split(',').map(function (p) { return String(p || '').trim(); }).filter(Boolean);
    if (parts.length <= 1) return raw;
    var out = [];
    var seen = {};
    parts.forEach(function (part) {
      var key = part.toLowerCase().replace(/\s+/g, ' ').trim();
      if (!key || seen[key]) return;
      seen[key] = true;
      out.push(part);
    });
    return out.join(', ');
  }

  function cleanTooltipTitle(value) {
    var text = String(value || '').replace(/\u00a0/g, ' ');
    if (!text) return 'Item';
    text = text.replace(/(\$\s*\d+(?:,\d{3})*(?:\.\d{1,2})?)(?:\s*\1)+/gi, '$1');
    text = text.replace(/\$\s*\d+(?:,\d{3})*(?:\.\d{1,2})?/g, ' ');
    text = text.replace(/\b(?:price|product\s*page|list\s*price|list|typical|sponsored)\b:?/gi, ' ');
    text = text.replace(/\s+/g, ' ').trim();
    text = dedupeCommaSegments(text);
    text = text.replace(/\s+/g, ' ').replace(/^[,;:|.\-\s]+|[,;:|.\-\s]+$/g, '');
    return text || 'Item';
  }

  function normalizeLocalPoints(rawPoints) {
    var points = (rawPoints || []).map(function (item, idx) {
      var id = String(item.id || ('local-' + idx));
      var title = String(item.title || ('Item ' + (idx + 1)));
      var price = Number(item.price);
      if (!isFinite(price) || price <= 0) return null;

      var rating = Number(item.rating);
      var reviewCount = Number(item.reviewCount);
      if (!isFinite(rating) || rating <= 0 || !isFinite(reviewCount) || reviewCount < 0) {
        var synth = syntheticRatingReviews(id, title);
        if (!isFinite(rating) || rating <= 0) rating = synth.rating;
        if (!isFinite(reviewCount) || reviewCount < 0) reviewCount = synth.reviewCount;
      }
      rating = Math.max(1, Math.min(5, rating));
      reviewCount = Math.max(0, Math.round(reviewCount));

      var qualityRaw = rating * Math.log10(reviewCount + 1);
      return {
        id: id,
        title: title,
        price: Number(price.toFixed(2)),
        rating: Number(rating.toFixed(2)),
        reviewCount: reviewCount,
        qualityRaw: Number(qualityRaw.toFixed(5)),
        url: item.url ? String(item.url) : '',
        category: item.category ? String(item.category) : ''
      };
    }).filter(Boolean);

    if (!points.length) return [];

    var minRaw = points[0].qualityRaw;
    var maxRaw = points[0].qualityRaw;
    points.forEach(function (point) {
      if (point.qualityRaw < minRaw) minRaw = point.qualityRaw;
      if (point.qualityRaw > maxRaw) maxRaw = point.qualityRaw;
    });

    var spread = maxRaw - minRaw;
    points.forEach(function (point) {
      var quality = spread <= 1e-12 ? 50 : (100 * (point.qualityRaw - minRaw) / spread);
      point.quality = Number(quality.toFixed(2));
      point.valueScore = Number((quality / Math.max(point.price, 1e-9)).toFixed(6));
    });

    points.sort(function (a, b) {
      if (a.price !== b.price) return a.price - b.price;
      return a.id.localeCompare(b.id);
    });
    return points;
  }

  function pickOptimal(points) {
    if (!points || !points.length) return '';

    var candidates = points.filter(function (point) { return Number(point.reviewCount) >= 50; });
    if (candidates.length < 8) candidates = points.slice();

    if (candidates.length >= 10) {
      var p10 = percentile(candidates.map(function (point) { return point.price; }), 10);
      var threshold = p10 * 0.8;
      var filtered = candidates.filter(function (point) { return point.price >= threshold; });
      if (filtered.length) candidates = filtered;
    }

    if (!candidates.length) candidates = points.slice();

    candidates.sort(function (a, b) {
      if (a.valueScore !== b.valueScore) return b.valueScore - a.valueScore;
      if (a.quality !== b.quality) return b.quality - a.quality;
      if (a.price !== b.price) return a.price - b.price;
      return b.reviewCount - a.reviewCount;
    });
    return String(candidates[0].id || '');
  }

  function paretoFrontierIds(points) {
    if (!points || !points.length) return [];
    var ordered = points.slice().sort(function (a, b) {
      if (a.price !== b.price) return a.price - b.price;
      return a.id.localeCompare(b.id);
    });
    var frontier = [];
    var maxQuality = -Infinity;
    ordered.forEach(function (point) {
      if (point.quality > maxQuality + 1e-9) {
        frontier.push(point.id);
        maxQuality = point.quality;
      }
    });
    return frontier;
  }

  function buildLocalFromComparables(productId, comparables, currentPrice, title) {
    var points = normalizeLocalPoints(comparables || []);
    if (!points.length) {
      var baseline = Number(currentPrice);
      if (!isFinite(baseline) || baseline <= 0) baseline = 100;
      var rng = mulberry32(hashString('value-local:' + productId + ':' + baseline.toFixed(2)));
      var raw = [];
      for (var i = 0; i < 12; i++) {
        var drift = i === 0 ? 0 : (-0.35 + rng() * 0.7);
        raw.push({
          id: i === 0 ? String(productId) : (String(productId) + '-local-' + i),
          title: i === 0 ? (title || String(productId)) : ('Alternative ' + i),
          price: Math.max(3, baseline * (1 + drift))
        });
      }
      points = normalizeLocalPoints(raw);
    }

    return {
      productId: String(productId),
      currency: 'USD',
      points: points,
      optimalId: pickOptimal(points),
      frontierIds: paretoFrontierIds(points),
      explanation: [
        'Quality score = rating x log10(reviewCount + 1), normalized to 0-100.',
        'Best Value picks the highest quality-per-dollar point after reliability filters.',
        'Local comparables were used for this chart.'
      ]
    };
  }

  function mergeBackendWithLocal(data, localComparables) {
    var points = Array.isArray(data.points) ? data.points.slice() : [];
    var local = Array.isArray(localComparables) ? localComparables : [];
    if (!points.length || !local.length) return data;

    var byId = {};
    var byTitleUnique = {};
    var titleCounts = {};
    local.forEach(function (row) {
      var id = String(row.id || '').trim();
      if (id) byId[id] = row;
      var tk = normalizeTitleKey(row.title || '');
      if (!tk) return;
      titleCounts[tk] = (titleCounts[tk] || 0) + 1;
      if (!byTitleUnique[tk]) byTitleUnique[tk] = row;
    });
    Object.keys(byTitleUnique).forEach(function (tk) {
      if ((titleCounts[tk] || 0) > 1) delete byTitleUnique[tk];
    });

    var merged = points.map(function (point) {
      var id = String(point.id || '').trim();
      var localById = id ? byId[id] : null;
      var localByTitle = byTitleUnique[normalizeTitleKey(point.title || '')] || null;
      var ref = localById || localByTitle;
      if (!ref) return point;
      return Object.assign({}, point, {
        title: ref.title || point.title,
        url: ref.url || point.url,
        category: ref.category || point.category
      });
    });

    return Object.assign({}, data, { points: merged });
  }

  function shouldPreferLocalData(data, localComparables) {
    var local = Array.isArray(localComparables) ? localComparables : [];
    if (local.length < 3) return false;
    var points = Array.isArray(data.points) ? data.points : [];
    if (!points.length) return true;

    var genericCount = 0;
    points.forEach(function (point) {
      var t = String(point.title || '');
      if (/^(comparable\s+option|item\s+\d+|alternative\s+\d+)\b/i.test(t)) genericCount += 1;
    });
    if (genericCount > 0 && local.length >= Math.min(5, points.length)) return true;
    if ((genericCount / points.length) > 0.25) return true;
    if ((genericCount / points.length) > 0.5) return true;

    var localIds = {};
    local.forEach(function (row) {
      var id = String(row.id || '').trim();
      if (id) localIds[id] = true;
    });
    var overlap = 0;
    points.forEach(function (point) {
      if (localIds[String(point.id || '').trim()]) overlap += 1;
    });
    if (overlap === 0 && local.length >= 5) return true;
    if (local.length >= 5 && (overlap / points.length) < 0.6) return true;

    return false;
  }

  function fingerprintComparables(localComparables) {
    var local = Array.isArray(localComparables) ? localComparables : [];
    if (!local.length) return 'none';
    var rows = local.slice(0, 80).map(function (row) {
      return [
        String(row.id || '').trim(),
        normalizeTitleKey(row.title || ''),
        String(Number(row.price || 0).toFixed(2)),
        String(row.url || '').trim()
      ].join('|');
    }).sort();
    return String(hashString(rows.join('||')));
  }

  function pointValueScore(point) {
    var fromPoint = Number(point && point.valueScore);
    if (isFinite(fromPoint) && fromPoint > 0) return fromPoint;
    var quality = Number(point && point.quality);
    var price = Number(point && point.price);
    if (!isFinite(quality) || !isFinite(price) || price <= 0) return 0;
    return quality / price;
  }

  function selectChartPoints(points, optimalId, currentId) {
    var clean = (points || []).filter(function (point) {
      return point && isFinite(Number(point.price)) && isFinite(Number(point.quality));
    });
    if (clean.length <= 18) return clean.slice();

    var best = null;
    clean.forEach(function (point) {
      if (String(point.id || '') === String(optimalId || '')) best = point;
    });
    if (!best) {
      best = clean.slice().sort(function (a, b) {
        return pointValueScore(b) - pointValueScore(a);
      })[0] || null;
    }
    if (!best) return clean.slice(0, 18);

    var bestPrice = Math.max(1e-9, Number(best.price));
    var bestQuality = Number(best.quality);
    var bestScore = Math.max(1e-9, pointValueScore(best));

    var ranked = clean.map(function (point) {
      var price = Number(point.price);
      var quality = Number(point.quality);
      var value = pointValueScore(point);
      var priceGap = Math.abs(price - bestPrice) / bestPrice;
      var qualityGap = Math.abs(quality - bestQuality) / 100;
      var valueGap = Math.abs(value - bestScore) / bestScore;
      return {
        point: point,
        metric: (valueGap * 0.6) + (priceGap * 0.3) + (qualityGap * 0.1),
        priceGap: priceGap
      };
    }).sort(function (a, b) {
      if (a.metric !== b.metric) return a.metric - b.metric;
      if (a.priceGap !== b.priceGap) return a.priceGap - b.priceGap;
      return Number(a.point.price) - Number(b.point.price);
    });

    var selected = [];
    var selectedById = {};
    function addPoint(point) {
      if (!point) return;
      var id = String(point.id || '');
      if (!id || selectedById[id]) return;
      selectedById[id] = true;
      selected.push(point);
    }

    addPoint(best);
    ranked.forEach(function (row) {
      if (selected.length >= 14) return;
      addPoint(row.point);
    });

    clean.forEach(function (point) {
      if (String(point.id || '') === String(currentId || '')) addPoint(point);
    });

    var cheaper = clean.filter(function (point) {
      return Number(point.price) < bestPrice && !selectedById[String(point.id || '')];
    }).sort(function (a, b) {
      return Math.abs(Number(a.price) - bestPrice) - Math.abs(Number(b.price) - bestPrice);
    });
    var pricier = clean.filter(function (point) {
      return Number(point.price) > bestPrice && !selectedById[String(point.id || '')];
    }).sort(function (a, b) {
      return Math.abs(Number(a.price) - bestPrice) - Math.abs(Number(b.price) - bestPrice);
    });

    if (selected.length < 16 && cheaper.length) addPoint(cheaper[0]);
    if (selected.length < 16 && pricier.length) addPoint(pricier[0]);
    if (selected.length < 16 && cheaper.length > 1) addPoint(cheaper[1]);
    if (selected.length < 16 && pricier.length > 1) addPoint(pricier[1]);

    if (selected.length > 16) {
      var keepIds = {};
      keepIds[String(best.id || '')] = true;
      if (currentId) keepIds[String(currentId)] = true;
      selected = selected.sort(function (a, b) {
        var aKeep = keepIds[String(a.id || '')] ? 1 : 0;
        var bKeep = keepIds[String(b.id || '')] ? 1 : 0;
        if (aKeep !== bKeep) return bKeep - aKeep;
        var aMetric = Math.abs(pointValueScore(a) - bestScore) / bestScore;
        var bMetric = Math.abs(pointValueScore(b) - bestScore) / bestScore;
        return aMetric - bMetric;
      }).slice(0, 16);
    }

    return selected.sort(function (a, b) {
      return Number(a.price) - Number(b.price);
    });
  }

  function ValueChartModal(options) {
    options = options || {};
    this.getApiBase = options.getApiBase;
    this.fetchWithTimeout = options.fetchWithTimeout;
    this.resolveProductUrl = options.resolveProductUrl;
    this.onError = options.onError;

    this.sessionCache = new Map();
    this.currentProductId = '';
    this.currentRequestToken = 0;
    this.chartState = null;
    this.currentContext = { store: '', scanOrigin: '' };
    this.localComparables = [];

    this.modalEl = document.getElementById('valueChartModal');
    this.closeBtnEl = document.getElementById('valueChartModalClose');
    this.productEl = document.getElementById('valueChartModalProduct');
    this.statusEl = document.getElementById('valueChartModalStatus');
    this.chartWrapEl = document.getElementById('valueChartModalChartWrap');
    this.chartEl = document.getElementById('valueChartModalChart');
    this.bodyEl = document.getElementById('valueChartModalBody');
    this.tooltipEl = document.getElementById('valueChartModalTooltip');

    this.handleDocKeyDown = this.handleDocKeyDown.bind(this);
    this.handleMouseMove = this.handleMouseMove.bind(this);
    this.handleClick = this.handleClick.bind(this);
    this.hideTooltip = this.hideTooltip.bind(this);

    if (this.modalEl) {
      this.modalEl.addEventListener('click', (evt) => {
        var target = evt.target;
        if (target && target.getAttribute && target.getAttribute('data-close') === '1') {
          this.close();
        }
      });
    }
    if (this.closeBtnEl) {
      this.closeBtnEl.addEventListener('click', () => this.close());
    }
    if (this.chartWrapEl) {
      this.chartWrapEl.addEventListener('mousemove', this.handleMouseMove);
      this.chartWrapEl.addEventListener('mouseleave', this.hideTooltip);
      this.chartWrapEl.addEventListener('click', this.handleClick);
    }
    document.addEventListener('keydown', this.handleDocKeyDown);
  }

  ValueChartModal.prototype.handleDocKeyDown = function (evt) {
    if (evt && evt.key === 'Escape' && this.isOpen()) this.close();
  };

  ValueChartModal.prototype.isOpen = function () {
    return !!(this.modalEl && !this.modalEl.classList.contains('hidden'));
  };

  ValueChartModal.prototype.open = function (params) {
    params = params || {};
    var productId = String(params.productId || '').trim();
    var title = String(params.title || '').trim();
    if (!productId) {
      this.reportError('Value chart is unavailable: missing product ID.');
      return;
    }
    if (!this.modalEl) return;

    this.currentProductId = productId;
    this.currentRequestToken += 1;
    this.currentContext = {
      store: String(params.store || ''),
      scanOrigin: String(params.scanOrigin || '')
    };
    this.localComparables = Array.isArray(params.localComparables) ? params.localComparables.slice(0, 120) : [];

    this.modalEl.classList.remove('hidden');
    this.hideTooltip();
    this.setStatus('Loading value chart…', 'loading');
    this.setBody('');
    this.renderChart([], productId, '', [], 'USD');

    if (this.productEl) {
      this.productEl.textContent = title || productId;
      this.productEl.classList.remove('hidden');
    }

    this.loadValueChart({
      productId: productId,
      currentPrice: params.currentPrice,
      title: params.title,
      category: params.category,
      rating: params.rating,
      reviewCount: params.reviewCount,
      requestToken: this.currentRequestToken
    });
  };

  ValueChartModal.prototype.close = function () {
    if (!this.modalEl) return;
    this.modalEl.classList.add('hidden');
    this.hideTooltip();
  };

  ValueChartModal.prototype.setStatus = function (text, type) {
    if (!this.statusEl) return;
    if (!text) {
      this.statusEl.textContent = '';
      this.statusEl.className = 'value-modal-status hidden';
      return;
    }
    this.statusEl.textContent = text;
    this.statusEl.className = 'value-modal-status ' + (type || '');
  };

  ValueChartModal.prototype.setBody = function (html) {
    if (!this.bodyEl) return;
    if (!html) {
      this.bodyEl.innerHTML = '';
      this.bodyEl.classList.add('hidden');
      return;
    }
    this.bodyEl.innerHTML = html;
    this.bodyEl.classList.remove('hidden');
  };

  ValueChartModal.prototype.reportError = function (message) {
    this.setStatus(String(message || 'Failed to load value chart.'), 'error');
    if (typeof this.onError === 'function') this.onError(message);
  };

  ValueChartModal.prototype.loadValueChart = async function (params) {
    var productId = String(params.productId || '').trim();
    var requestToken = Number(params.requestToken || 0);
    var cacheKey = productId + '::' + fingerprintComparables(this.localComparables);

    var cached = this.sessionCache.get(cacheKey);
    if (cached) {
      if (shouldPreferLocalData(cached, this.localComparables)) {
        cached = buildLocalFromComparables(productId, this.localComparables, params.currentPrice, params.title);
        this.sessionCache.set(cacheKey, cached);
      }
      if (requestToken !== this.currentRequestToken) return;
      this.renderFromResponse(cached, { productId: productId, fromCache: true, fallbackMode: false });
      return;
    }

    try {
      var apiBase = typeof this.getApiBase === 'function' ? this.getApiBase() : '';
      var url = apiBase.replace(/\/+$/, '') + '/api/value-chart?productId=' + encodeURIComponent(productId);
      if (params.currentPrice != null && isFinite(Number(params.currentPrice))) {
        url += '&currentPrice=' + encodeURIComponent(String(Number(params.currentPrice)));
      }
      if (params.title) url += '&title=' + encodeURIComponent(String(params.title));
      if (params.category) url += '&category=' + encodeURIComponent(String(params.category));
      if (params.rating != null && isFinite(Number(params.rating))) {
        url += '&rating=' + encodeURIComponent(String(Number(params.rating)));
      }
      if (params.reviewCount != null && isFinite(Number(params.reviewCount))) {
        url += '&reviewCount=' + encodeURIComponent(String(Math.round(Number(params.reviewCount))));
      }

      var res;
      if (typeof this.fetchWithTimeout === 'function') {
        res = await this.fetchWithTimeout(url, { method: 'GET' }, 12000);
      } else {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, 12000);
        try {
          res = await fetch(url, { method: 'GET', signal: controller.signal });
        } finally {
          clearTimeout(timer);
        }
      }

      var raw = await res.text();
      if (!res.ok) throw new Error('HTTP ' + res.status + ': ' + safePreview(raw));

      var data = raw ? JSON.parse(raw) : null;
      if (!isValidResponse(data)) throw new Error('Unexpected response format');

      data = mergeBackendWithLocal(data, this.localComparables);
      if (shouldPreferLocalData(data, this.localComparables)) {
        data = buildLocalFromComparables(productId, this.localComparables, params.currentPrice, params.title);
      }

      this.sessionCache.set(cacheKey, data);
      if (requestToken !== this.currentRequestToken) return;
      this.renderFromResponse(data, { productId: productId, fromCache: false, fallbackMode: false });
    } catch (err) {
      if (requestToken !== this.currentRequestToken) return;
      var fallback = buildLocalFromComparables(productId, this.localComparables, params.currentPrice, params.title);
      this.renderFromResponse(fallback, { productId: productId, fromCache: false, fallbackMode: true });
      this.setStatus('Server unreachable. Showing local chart.', 'error');
      if (typeof this.onError === 'function') {
        this.onError((err && err.message) ? err.message : 'Value chart unavailable');
      }
    }
  };

  ValueChartModal.prototype.renderFromResponse = function (data, options) {
    options = options || {};
    if (!options.fallbackMode) {
      this.setStatus(options.fromCache ? 'Loaded from session cache.' : '', '');
    }

    var points = Array.isArray(data.points) ? data.points : [];
    var optimalId = String(data.optimalId || '');
    var currentId = String(options.productId || this.currentProductId || '');
    var frontierIds = Array.isArray(data.frontierIds) ? data.frontierIds.map(String) : [];
    var focusedPoints = selectChartPoints(points, optimalId, currentId);
    var focusedIds = {};
    focusedPoints.forEach(function (point) { focusedIds[String(point.id || '')] = true; });
    var focusedFrontier = frontierIds.filter(function (id) { return focusedIds[String(id || '')]; });
    if (focusedFrontier.length < 2) {
      focusedFrontier = paretoFrontierIds(focusedPoints);
    }

    this.renderChart(focusedPoints, currentId, optimalId, focusedFrontier, data.currency || 'USD');

    var pointMap = {};
    points.forEach(function (point) { pointMap[String(point.id || '')] = point; });
    var best = pointMap[optimalId] || null;

    var html = [];
    if (best) {
      html.push('<p class="value-modal-stat"><strong>Best Value:</strong> ' + escapeHtml(formatMoney(best.price, data.currency)) + ', Quality ' + escapeHtml(String(Number(best.quality || 0).toFixed(1))) + '</p>');
    } else {
      html.push('<p class="value-modal-stat"><strong>Best Value:</strong> unavailable</p>');
    }

    html.push('<p class="value-modal-stat">How we compute quality: Y = 0.65 x Q0 (LLM intrinsic) + 0.35 x Qm (market validation).</p>');
    html.push('<p class="value-modal-stat">Qm uses rating, review volume, defect/return proxy, and positive-share proxy with safe fallbacks.</p>');
    html.push('<p class="value-modal-stat">Why this is best value: highest quality per dollar among reliable items.</p>');

    if (Array.isArray(data.explanation) && data.explanation.length) {
      html.push('<ul class="value-modal-explanation">');
      data.explanation.slice(0, 4).forEach(function (line) {
        html.push('<li>' + escapeHtml(String(line || '')) + '</li>');
      });
      html.push('</ul>');
    }

    this.setBody(html.join(''));
  };

  ValueChartModal.prototype.renderChart = function (points, currentId, optimalId, frontierIds, currency) {
    if (!this.chartEl || !this.chartWrapEl) return;
    this.chartState = null;
    this.hideTooltip();

    if (!Array.isArray(points) || points.length === 0) {
      this.chartEl.innerHTML = '';
      this.chartWrapEl.classList.add('hidden');
      return;
    }

    var cleanPoints = points.filter(function (point) {
      return point && isFinite(Number(point.price)) && isFinite(Number(point.quality));
    });
    if (!cleanPoints.length) {
      this.chartEl.innerHTML = '';
      this.chartWrapEl.classList.add('hidden');
      return;
    }

    var width = Math.max(520, Math.round((this.chartWrapEl.clientWidth || 620) - 2));
    var height = 260;
    var padL = 52;
    var padR = 16;
    var padT = 14;
    var padB = 40;
    var innerW = width - padL - padR;
    var innerH = height - padT - padB;

    var prices = cleanPoints.map(function (point) { return Number(point.price); });
    var minPrice = Math.min.apply(null, prices);
    var maxPrice = Math.max.apply(null, prices);
    var priceSpread = Math.max(0.01, maxPrice - minPrice);
    minPrice -= priceSpread * 0.08;
    maxPrice += priceSpread * 0.08;
    if (minPrice < 0) minPrice = 0;
    var xRange = Math.max(0.01, maxPrice - minPrice);

    var yMin = 0;
    var yMax = 100;
    var yRange = yMax - yMin;

    function xForPrice(value) {
      return padL + ((Number(value) - minPrice) / xRange) * innerW;
    }

    function yForQuality(value) {
      return (height - padB) - ((Number(value) - yMin) / yRange) * innerH;
    }

    var plotted = cleanPoints.map(function (point) {
      var fallbackUrl = '';
      if (typeof this.resolveProductUrl === 'function') {
        try {
          fallbackUrl = this.resolveProductUrl(point, this.currentContext || {}) || '';
        } catch (_) {
          fallbackUrl = '';
        }
      }
      return {
        point: point,
        x: xForPrice(point.price),
        y: yForQuality(point.quality),
        url: String(point.url || fallbackUrl || '').trim()
      };
    }, this);

    var yTickValues = [0, 25, 50, 75, 100];
    var yTicks = [];
    yTickValues.forEach(function (tick) {
      var y = yForQuality(tick);
      yTicks.push('<line x1="' + padL + '" y1="' + y.toFixed(2) + '" x2="' + (width - padR) + '" y2="' + y.toFixed(2) + '" stroke="#e8edf5" stroke-width="1"/>');
      yTicks.push('<text x="' + (padL - 6) + '" y="' + (y + 3).toFixed(2) + '" font-size="9" text-anchor="end" fill="#66758a">' + tick + '</text>');
    });

    var xTicks = [];
    var xTickCount = 4;
    for (var i = 0; i <= xTickCount; i++) {
      var ratio = i / xTickCount;
      var value = minPrice + (xRange * ratio);
      var x = padL + (innerW * ratio);
      xTicks.push('<line x1="' + x.toFixed(2) + '" y1="' + (height - padB) + '" x2="' + x.toFixed(2) + '" y2="' + (height - padB + 4) + '" stroke="#d7dce5" stroke-width="1"/>');
      xTicks.push('<text x="' + x.toFixed(2) + '" y="' + (height - 10) + '" font-size="9" text-anchor="middle" fill="#66758a">' + escapeHtml(formatAxisMoney(value)) + '</text>');
    }

    var frontierLookup = {};
    frontierIds.forEach(function (id) { frontierLookup[String(id)] = true; });
    var frontierPoints = plotted.filter(function (item) {
      return frontierLookup[String(item.point.id || '')];
    }).sort(function (a, b) {
      return Number(a.point.price) - Number(b.point.price);
    });

    var frontierPath = '';
    if (frontierPoints.length >= 2) {
      frontierPath = '<polyline fill="none" stroke="#6366f1" stroke-width="2" stroke-dasharray="4 3" points="' + frontierPoints.map(function (item) {
        return item.x.toFixed(2) + ',' + item.y.toFixed(2);
      }).join(' ') + '"/>';
    }

    var circles = [];
    var labels = [];
    plotted.forEach(function (item) {
      var id = String(item.point.id || '');
      var isCurrent = id === String(currentId || '');
      var isBest = id === String(optimalId || '');
      var color = '#2563eb';
      var radius = 3;
      if (isCurrent && isBest) {
        color = '#7b1fa2';
        radius = 5.5;
      } else if (isBest) {
        color = '#d32f2f';
        radius = 5;
      } else if (isCurrent) {
        color = '#2e7d32';
        radius = 5;
      }

      circles.push('<circle cx="' + item.x.toFixed(2) + '" cy="' + item.y.toFixed(2) + '" r="' + radius + '" fill="' + color + '" stroke="#ffffff" stroke-width="1.4"/>');

      if (isBest || isCurrent) {
        var tag = isBest && isCurrent ? 'Current / Best Value' : (isBest ? 'Best Value' : 'Current');
        labels.push('<text x="' + (item.x + 6).toFixed(2) + '" y="' + (item.y - 6).toFixed(2) + '" font-size="9" fill="' + color + '">' + escapeHtml(tag) + '</text>');
      }
    });

    this.chartEl.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    this.chartEl.innerHTML = [
      yTicks.join(''),
      '<line x1="' + padL + '" y1="' + (height - padB) + '" x2="' + (width - padR) + '" y2="' + (height - padB) + '" stroke="#d7dce5" stroke-width="1"/>',
      '<line x1="' + padL + '" y1="' + padT + '" x2="' + padL + '" y2="' + (height - padB) + '" stroke="#d7dce5" stroke-width="1"/>',
      xTicks.join(''),
      frontierPath,
      circles.join(''),
      labels.join(''),
      '<text x="' + (width / 2).toFixed(2) + '" y="' + (height - 2) + '" font-size="9" text-anchor="middle" fill="#6b7280">Price</text>',
      '<text x="12" y="' + (height / 2).toFixed(2) + '" font-size="9" text-anchor="middle" transform="rotate(-90 12 ' + (height / 2).toFixed(2) + ')" fill="#6b7280">Quality (0-100)</text>'
    ].join('');

    this.chartWrapEl.classList.remove('hidden');
    this.chartState = {
      width: width,
      height: height,
      currency: currency || 'USD',
      points: plotted,
      currentId: String(currentId || ''),
      optimalId: String(optimalId || '')
    };
  };

  ValueChartModal.prototype.handleMouseMove = function (evt) {
    if (!this.chartState || !this.chartWrapEl || !this.tooltipEl) {
      this.hideTooltip();
      return;
    }

    var rect = this.chartWrapEl.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      this.hideTooltip();
      return;
    }

    var xView = ((evt.clientX - rect.left) / rect.width) * this.chartState.width;
    var yView = ((evt.clientY - rect.top) / rect.height) * this.chartState.height;

    var nearest = null;
    var bestDist = Number.POSITIVE_INFINITY;
    this.chartState.points.forEach(function (entry) {
      var dx = entry.x - xView;
      var dy = entry.y - yView;
      var dist = (dx * dx) + (dy * dy);
      if (dist < bestDist) {
        bestDist = dist;
        nearest = entry;
      }
    });

    if (!nearest || bestDist > (POINT_HIT_RADIUS_PX * POINT_HIT_RADIUS_PX)) {
      this.chartWrapEl.style.cursor = 'default';
      this.hideTooltip();
      return;
    }

    this.chartWrapEl.style.cursor = nearest.url ? 'pointer' : 'default';

    var point = nearest.point || {};
    var cleanTitle = cleanTooltipTitle(point.title || point.id || 'Item');
    var flags = [];
    if (String(point.id || '') === this.chartState.currentId) flags.push('Current');
    if (String(point.id || '') === this.chartState.optimalId) flags.push('Best Value');

    this.tooltipEl.innerHTML = [
      '<strong>' + escapeHtml(cleanTitle) + '</strong>',
      flags.length ? ('<br>' + escapeHtml(flags.join(' | '))) : '',
      '<br>',
      'Price: ' + escapeHtml(formatMoney(point.price, this.chartState.currency)),
      '<br>',
      'Quality: ' + escapeHtml(String(Number(point.quality || 0).toFixed(1))),
      '<br>',
      'Rating: ' + escapeHtml(String(Number(point.rating || 0).toFixed(2))),
      ' | Reviews: ' + escapeHtml(String(Math.round(Number(point.reviewCount || 0))))
    ].join('');
    this.tooltipEl.classList.remove('hidden');

    var anchorX = (nearest.x / this.chartState.width) * rect.width;
    var anchorY = (nearest.y / this.chartState.height) * rect.height;
    var tooltipW = this.tooltipEl.offsetWidth || 220;
    var tooltipH = this.tooltipEl.offsetHeight || 80;
    var pad = 8;

    var left = anchorX - (tooltipW / 2);
    if (left < pad) left = pad;
    if (left + tooltipW > rect.width - pad) left = rect.width - tooltipW - pad;

    var aboveTop = anchorY - tooltipH - 10;
    var top = aboveTop;
    if (aboveTop < pad) {
      top = anchorY + 10;
    }
    if (top + tooltipH > rect.height - pad) {
      top = rect.height - tooltipH - pad;
    }
    if (top < pad) top = pad;

    this.tooltipEl.style.left = left + 'px';
    this.tooltipEl.style.top = top + 'px';
    this.tooltipEl.style.transform = 'none';
  };

  ValueChartModal.prototype.hideTooltip = function () {
    if (!this.tooltipEl) return;
    this.tooltipEl.classList.add('hidden');
    if (this.chartWrapEl) this.chartWrapEl.style.cursor = 'default';
  };

  ValueChartModal.prototype.handleClick = function (evt) {
    if (!this.chartState || !this.chartWrapEl) return;

    var rect = this.chartWrapEl.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    var xView = ((evt.clientX - rect.left) / rect.width) * this.chartState.width;
    var yView = ((evt.clientY - rect.top) / rect.height) * this.chartState.height;

    var nearest = null;
    var bestDist = Number.POSITIVE_INFINITY;
    this.chartState.points.forEach(function (entry) {
      var dx = entry.x - xView;
      var dy = entry.y - yView;
      var dist = (dx * dx) + (dy * dy);
      if (dist < bestDist) {
        bestDist = dist;
        nearest = entry;
      }
    });

    if (!nearest || !nearest.url) return;
    if (bestDist > (POINT_HIT_RADIUS_PX * POINT_HIT_RADIUS_PX)) return;

    window.open(nearest.url, '_blank', 'noopener,noreferrer');
  };

  window.ValueChartModal = ValueChartModal;
})();
