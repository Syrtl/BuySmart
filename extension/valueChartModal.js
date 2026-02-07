(function () {
  function safePreview(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  }

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
    if (Math.abs(n - Math.round(n)) < 0.005) {
      return '$' + String(Math.round(n));
    }
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

  function normalizeLocalPoints(rawPoints) {
    var points = (rawPoints || []).map(function (item, idx) {
      var rating = Math.max(1, Math.min(5, Number(item.rating || 0)));
      var reviews = Math.max(0, Number(item.reviewCount || 0));
      var qualityRaw = rating * Math.log10(reviews + 1);
      return {
        id: String(item.id || ('local-' + idx)),
        title: String(item.title || ('Comparable ' + (idx + 1))),
        price: Math.max(1, Number(item.price || 0)),
        rating: rating,
        reviewCount: Math.round(reviews),
        qualityRaw: qualityRaw
      };
    }).filter(function (p) {
      return isFinite(p.price) && p.price > 0;
    });

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
      point.price = Number(point.price.toFixed(2));
      point.rating = Number(point.rating.toFixed(2));
      point.qualityRaw = Number(point.qualityRaw.toFixed(5));
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

  function buildLocalFallback(productId, currentPrice, title) {
    var baseline = Number(currentPrice);
    if (!isFinite(baseline) || baseline <= 0) baseline = 100;

    var rng = mulberry32(hashString('value-local:' + productId + ':' + baseline.toFixed(2)));
    var rawPoints = [];
    for (var i = 0; i < 12; i++) {
      var drift = i === 0 ? 0 : (-0.35 + rng() * 0.7);
      var price = baseline * (1 + drift);
      var rating = 3.2 + rng() * 1.7;
      var reviews = 25 + Math.floor(Math.pow(rng(), 2) * 12000);
      rawPoints.push({
        id: i === 0 ? String(productId) : String(productId) + '-local-' + i,
        title: i === 0 ? (title || String(productId)) : ('Comparable Option ' + i),
        price: Math.max(3, price),
        rating: rating,
        reviewCount: reviews
      });
    }

    var points = normalizeLocalPoints(rawPoints);
    var optimalId = pickOptimal(points);
    var frontierIds = paretoFrontierIds(points);

    return {
      productId: String(productId),
      currency: 'USD',
      points: points,
      optimalId: optimalId,
      frontierIds: frontierIds,
      explanation: [
        'Quality score = rating x log10(reviewCount + 1), normalized to 0-100.',
        'Best Value picks the highest quality-per-dollar point after reliability filters.',
        'This local chart is demo fallback data because the backend was unreachable.'
      ]
    };
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

    this.modalEl = document.getElementById('valueChartModal');
    this.closeBtnEl = document.getElementById('valueChartModalClose');
    this.productEl = document.getElementById('valueChartModalProduct');
    this.statusEl = document.getElementById('valueChartModalStatus');
    this.chartWrapEl = document.getElementById('valueChartModalChartWrap');
    this.chartEl = document.getElementById('valueChartModalChart');
    this.bodyEl = document.getElementById('valueChartModalBody');
    this.tooltipEl = document.getElementById('valueChartModalTooltip');
    this.currentContext = { store: '', scanOrigin: '' };

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
    if (evt && evt.key === 'Escape' && this.isOpen()) {
      this.close();
    }
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

    this.modalEl.classList.remove('hidden');
    this.hideTooltip();
    this.setStatus('Loading value chart…', 'loading');
    this.setBody('');
    this.renderChart([], productId, '', []);

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
    var cacheKey = productId;

    var cached = this.sessionCache.get(cacheKey);
    if (cached) {
      if (requestToken !== this.currentRequestToken) return;
      this.renderFromResponse(cached, {
        productId: productId,
        fromCache: true,
        fallbackMode: false
      });
      return;
    }

    try {
      var apiBase = typeof this.getApiBase === 'function' ? this.getApiBase() : '';
      var url = apiBase.replace(/\/+$/, '') + '/api/value-chart?productId=' + encodeURIComponent(productId);
      if (params.currentPrice != null && isFinite(Number(params.currentPrice))) {
        url += '&currentPrice=' + encodeURIComponent(String(Number(params.currentPrice)));
      }
      if (params.title) {
        url += '&title=' + encodeURIComponent(String(params.title));
      }
      if (params.category) {
        url += '&category=' + encodeURIComponent(String(params.category));
      }
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
      if (!res.ok) {
        throw new Error('HTTP ' + res.status + ': ' + safePreview(raw));
      }

      var data = raw ? JSON.parse(raw) : null;
      if (!isValidResponse(data)) {
        throw new Error('Unexpected response format');
      }

      this.sessionCache.set(cacheKey, data);
      if (requestToken !== this.currentRequestToken) return;

      this.renderFromResponse(data, {
        productId: productId,
        fromCache: false,
        fallbackMode: false
      });
    } catch (err) {
      if (requestToken !== this.currentRequestToken) return;

      var fallback = buildLocalFallback(productId, params.currentPrice, params.title);
      this.renderFromResponse(fallback, {
        productId: productId,
        fromCache: false,
        fallbackMode: true
      });
      this.setStatus('Server unreachable. Showing local demo chart.', 'error');
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

    this.renderChart(points, currentId, optimalId, frontierIds, data.currency || 'USD');

    var pointMap = {};
    points.forEach(function (point) {
      pointMap[String(point.id || '')] = point;
    });
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
      var fallbackUrl = null;
      if (typeof this.resolveProductUrl === 'function') {
        try {
          fallbackUrl = this.resolveProductUrl(point, this.currentContext || {});
        } catch (_) {
          fallbackUrl = null;
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
    frontierIds.forEach(function (id) {
      frontierLookup[String(id)] = true;
    });
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

    if (!nearest) {
      this.hideTooltip();
      return;
    }
    if (this.chartWrapEl) {
      this.chartWrapEl.style.cursor = nearest.url ? 'pointer' : 'default';
    }

    var point = nearest.point || {};
    var flags = [];
    if (String(point.id || '') === this.chartState.currentId) flags.push('Current');
    if (String(point.id || '') === this.chartState.optimalId) flags.push('Best Value');

    this.tooltipEl.innerHTML = [
      '<strong>' + escapeHtml(String(point.title || point.id || 'Item')) + '</strong>',
      flags.length ? ('<br>' + escapeHtml(flags.join(' | '))) : '',
      '<br>',
      'Price: ' + escapeHtml(formatMoney(point.price, this.chartState.currency)),
      '<br>',
      'Quality: ' + escapeHtml(String(Number(point.quality || 0).toFixed(1))),
      '<br>',
      'Rating: ' + escapeHtml(String(Number(point.rating || 0).toFixed(2))),
      ' | Reviews: ' + escapeHtml(String(Math.round(Number(point.reviewCount || 0))))
    ].join('');

    var tooltipX = (nearest.x / this.chartState.width) * rect.width;
    var tooltipY = Math.max(26, evt.clientY - rect.top - 8);
    this.tooltipEl.style.left = tooltipX + 'px';
    this.tooltipEl.style.top = tooltipY + 'px';
    this.tooltipEl.classList.remove('hidden');
  };

  ValueChartModal.prototype.hideTooltip = function () {
    if (!this.tooltipEl) return;
    this.tooltipEl.classList.add('hidden');
    if (this.chartWrapEl) {
      this.chartWrapEl.style.cursor = 'default';
    }
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

    if (bestDist > (16 * 16)) return;
    window.open(nearest.url, '_blank', 'noopener,noreferrer');
  };

  window.ValueChartModal = ValueChartModal;
})();
