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
    if (!isFinite(n)) return '—';
    var code = currency || 'USD';
    try {
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: code, maximumFractionDigits: 2 }).format(n);
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
      Array.isArray(data.points) &&
      data.points.length > 0 &&
      typeof data.productId === 'string'
    );
  }

  function PriceHistoryModal(options) {
    options = options || {};
    this.getApiBase = options.getApiBase;
    this.fetchWithTimeout = options.fetchWithTimeout;
    this.onError = options.onError;
    this.sessionCache = new Map();
    this.currentProductId = '';
    this.currentRequestToken = 0;
    this.chartState = null;

    this.modalEl = document.getElementById('priceHistoryModal');
    this.closeBtnEl = document.getElementById('priceHistoryModalClose');
    this.productEl = document.getElementById('priceHistoryModalProduct');
    this.statusEl = document.getElementById('priceHistoryModalStatus');
    this.chartWrapEl = document.getElementById('priceHistoryModalChartWrap');
    this.chartEl = document.getElementById('priceHistoryModalChart');
    this.statsEl = document.getElementById('priceHistoryModalStats');
    this.tooltipEl = document.getElementById('priceHistoryModalTooltip');

    this.handleDocKeyDown = this.handleDocKeyDown.bind(this);
    this.handleMouseMove = this.handleMouseMove.bind(this);
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
    }
    document.addEventListener('keydown', this.handleDocKeyDown);
  }

  PriceHistoryModal.prototype.handleDocKeyDown = function (evt) {
    if (evt && evt.key === 'Escape' && this.isOpen()) {
      this.close();
    }
  };

  PriceHistoryModal.prototype.isOpen = function () {
    return !!(this.modalEl && !this.modalEl.classList.contains('hidden'));
  };

  PriceHistoryModal.prototype.open = function (params) {
    params = params || {};
    var productId = String(params.productId || '').trim();
    var title = String(params.title || '').trim();
    var currentPrice = params.currentPrice;
    if (!productId) {
      this.reportError('Price history is unavailable: missing product ID.');
      return;
    }
    if (!this.modalEl) return;

    this.currentProductId = productId;
    this.currentRequestToken += 1;
    this.modalEl.classList.remove('hidden');
    this.hideTooltip();
    this.setStatus('Loading history…', 'loading');
    this.setStats('');
    this.renderChart([]);

    if (this.productEl) {
      if (title) {
        this.productEl.textContent = title;
        this.productEl.classList.remove('hidden');
      } else {
        this.productEl.textContent = productId;
        this.productEl.classList.remove('hidden');
      }
    }

    this.loadHistory(productId, currentPrice, this.currentRequestToken);
  };

  PriceHistoryModal.prototype.close = function () {
    if (!this.modalEl) return;
    this.modalEl.classList.add('hidden');
    this.hideTooltip();
  };

  PriceHistoryModal.prototype.setStatus = function (text, type) {
    if (!this.statusEl) return;
    if (!text) {
      this.statusEl.textContent = '';
      this.statusEl.className = 'price-modal-status hidden';
      return;
    }
    this.statusEl.textContent = text;
    this.statusEl.className = 'price-modal-status ' + (type || '');
  };

  PriceHistoryModal.prototype.setStats = function (text) {
    if (!this.statsEl) return;
    if (!text) {
      this.statsEl.textContent = '';
      this.statsEl.classList.add('hidden');
      return;
    }
    this.statsEl.textContent = text;
    this.statsEl.classList.remove('hidden');
  };

  PriceHistoryModal.prototype.reportError = function (message) {
    this.setStatus(String(message || 'Failed to load price history.'), 'error');
    if (typeof this.onError === 'function') this.onError(message);
  };

  PriceHistoryModal.prototype.loadHistory = async function (productId, currentPrice, requestToken) {
    var cacheKey = String(productId);
    var cached = this.sessionCache.get(cacheKey);
    if (cached) {
      if (requestToken !== this.currentRequestToken) return;
      this.renderFromResponse(cached, true);
      return;
    }

    try {
      var apiBase = typeof this.getApiBase === 'function' ? this.getApiBase() : '';
      var url = apiBase.replace(/\/+$/, '') + '/api/price-history?productId=' + encodeURIComponent(productId) + '&weeks=13';
      if (currentPrice != null && isFinite(Number(currentPrice))) {
        url += '&currentPrice=' + encodeURIComponent(String(Number(currentPrice)));
      }

      var res;
      if (typeof this.fetchWithTimeout === 'function') {
        res = await this.fetchWithTimeout(url, { method: 'GET' }, 10000);
      } else {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, 10000);
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
      this.renderFromResponse(data, false);
    } catch (err) {
      if (requestToken !== this.currentRequestToken) return;
      var msg = (err && err.message) ? err.message : 'Failed to load history';
      this.reportError('Price history unavailable: ' + msg);
    }
  };

  PriceHistoryModal.prototype.renderFromResponse = function (data, fromSessionCache) {
    this.setStatus(fromSessionCache ? 'Loaded from session cache.' : '', '');
    this.renderChart(data.points, data.currency);
    var stats = [
      'Min: ' + formatMoney(data.min, data.currency),
      'Max: ' + formatMoney(data.max, data.currency),
      'Current: ' + formatMoney(data.current, data.currency)
    ].join(' · ');
    this.setStats(stats);
  };

  PriceHistoryModal.prototype.renderChart = function (points, currency) {
    if (!this.chartEl || !this.chartWrapEl) return;
    this.chartState = null;
    this.hideTooltip();

    if (!Array.isArray(points) || points.length === 0) {
      this.chartEl.innerHTML = '';
      this.chartWrapEl.classList.add('hidden');
      return;
    }

    var width = Math.max(460, Math.round((this.chartWrapEl.clientWidth || 560) - 2));
    var height = 220;
    var padL = 44;
    var padR = 12;
    var padT = 12;
    var padB = 34;
    var innerW = width - padL - padR;
    var innerH = height - padT - padB;
    var prices = points.map(function (p) { return Number(p.price); }).filter(function (n) { return isFinite(n); });
    if (!prices.length) {
      this.chartEl.innerHTML = '';
      this.chartWrapEl.classList.add('hidden');
      return;
    }

    var min = Math.min.apply(null, prices);
    var max = Math.max.apply(null, prices);
    var spread = Math.max(0.01, max - min);
    min -= spread * 0.08;
    max += spread * 0.08;
    var range = Math.max(0.01, max - min);

    var xPoints = [];
    var pointPairs = [];
    var circles = [];
    var xLabels = [];

    for (var i = 0; i < prices.length; i++) {
      var px = prices.length <= 1 ? padL : padL + ((innerW * i) / (prices.length - 1));
      var py = (height - padB) - (((prices[i] - min) / range) * innerH);
      xPoints.push(px);
      pointPairs.push(px.toFixed(2) + ',' + py.toFixed(2));
      circles.push('<circle cx="' + px.toFixed(2) + '" cy="' + py.toFixed(2) + '" r="2.5" fill="#1565c0"/>');
      var lbl = points[i] && points[i].label ? String(points[i].label) : ((prices.length - i - 1) + 'w ago');
      xLabels.push('<text x="' + px.toFixed(2) + '" y="' + (height - 11) + '" font-size="9" text-anchor="middle" fill="#5f6b7a">' + escapeHtml(lbl) + '</text>');
    }

    var yTicks = [];
    var yTickCount = 4;
    for (var t = 0; t <= yTickCount; t++) {
      var ratio = t / yTickCount;
      var v = max - (range * ratio);
      var y = (height - padB) - (((v - min) / range) * innerH);
      yTicks.push('<line x1="' + padL + '" y1="' + y.toFixed(2) + '" x2="' + (width - padR) + '" y2="' + y.toFixed(2) + '" stroke="#e8edf5" stroke-width="1"/>');
      yTicks.push('<text x="' + (padL - 5) + '" y="' + (y + 3).toFixed(2) + '" font-size="9" text-anchor="end" fill="#66758a">' + escapeHtml(formatAxisMoney(v)) + '</text>');
    }

    this.chartEl.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    this.chartEl.innerHTML = [
      yTicks.join(''),
      '<line x1="' + padL + '" y1="' + (height - padB) + '" x2="' + (width - padR) + '" y2="' + (height - padB) + '" stroke="#d7dce5" stroke-width="1"/>',
      '<line x1="' + padL + '" y1="' + padT + '" x2="' + padL + '" y2="' + (height - padB) + '" stroke="#d7dce5" stroke-width="1"/>',
      '<polyline fill="none" stroke="#0d47a1" stroke-width="2" points="' + pointPairs.join(' ') + '"/>',
      circles.join(''),
      xLabels.join('')
    ].join('');
    this.chartWrapEl.classList.remove('hidden');

    this.chartState = {
      width: width,
      xPoints: xPoints,
      points: points.slice(),
      currency: currency || 'USD'
    };
  };

  PriceHistoryModal.prototype.handleMouseMove = function (evt) {
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
    var nearest = 0;
    var dist = Number.POSITIVE_INFINITY;
    for (var i = 0; i < this.chartState.xPoints.length; i++) {
      var d = Math.abs(this.chartState.xPoints[i] - xView);
      if (d < dist) {
        dist = d;
        nearest = i;
      }
    }
    var point = this.chartState.points[nearest];
    if (!point) {
      this.hideTooltip();
      return;
    }
    var tooltipX = (this.chartState.xPoints[nearest] / this.chartState.width) * rect.width;
    var tooltipY = Math.max(26, evt.clientY - rect.top - 8);
    this.tooltipEl.innerHTML = [
      escapeHtml(String(point.label || '')),
      ' · ',
      escapeHtml(String(point.date || '')),
      '<br>',
      escapeHtml(formatMoney(point.price, this.chartState.currency))
    ].join('');
    this.tooltipEl.style.left = tooltipX + 'px';
    this.tooltipEl.style.top = tooltipY + 'px';
    this.tooltipEl.classList.remove('hidden');
  };

  PriceHistoryModal.prototype.hideTooltip = function () {
    if (!this.tooltipEl) return;
    this.tooltipEl.classList.add('hidden');
  };

  window.PriceHistoryModal = PriceHistoryModal;
})();
