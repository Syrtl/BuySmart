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
    try {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency || 'USD',
        maximumFractionDigits: 2
      }).format(n);
    } catch (_) {
      return '$' + n.toFixed(2);
    }
  }

  function formatRange(minValue, maxValue, suffix) {
    var a = Number(minValue);
    var b = Number(maxValue);
    if (!isFinite(a) || !isFinite(b)) return '—';
    var unit = suffix || '';
    return a.toFixed(1) + unit + ' - ' + b.toFixed(1) + unit;
  }

  function BuyTimingModal(options) {
    options = options || {};
    this.getApiBase = options.getApiBase;
    this.fetchWithTimeout = options.fetchWithTimeout;
    this.onError = options.onError;

    this.sessionCache = new Map();
    this.currentProductId = '';
    this.currentRequestToken = 0;

    this.modalEl = document.getElementById('timingModal');
    this.closeBtnEl = document.getElementById('timingModalClose');
    this.productEl = document.getElementById('timingModalProduct');
    this.statusEl = document.getElementById('timingModalStatus');
    this.bodyEl = document.getElementById('timingModalBody');

    this.handleDocKeyDown = this.handleDocKeyDown.bind(this);

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
    document.addEventListener('keydown', this.handleDocKeyDown);
  }

  BuyTimingModal.prototype.handleDocKeyDown = function (evt) {
    if (evt && evt.key === 'Escape' && this.isOpen()) {
      this.close();
    }
  };

  BuyTimingModal.prototype.isOpen = function () {
    return !!(this.modalEl && !this.modalEl.classList.contains('hidden'));
  };

  BuyTimingModal.prototype.open = function (params) {
    params = params || {};
    var productId = String(params.productId || '').trim();
    var title = String(params.title || '').trim();
    if (!productId) {
      this.reportError('Timing analysis is unavailable: missing product ID.');
      return;
    }
    if (!this.modalEl) return;

    this.currentProductId = productId;
    this.currentRequestToken += 1;
    this.modalEl.classList.remove('hidden');
    this.setStatus('Analyzing timing windows…', 'loading');
    this.setBody('');

    if (this.productEl) {
      this.productEl.textContent = title || productId;
      this.productEl.classList.remove('hidden');
    }

    this.loadTiming({
      productId: productId,
      currentPrice: params.currentPrice,
      title: params.title,
      category: params.category,
      requestToken: this.currentRequestToken
    });
  };

  BuyTimingModal.prototype.close = function () {
    if (!this.modalEl) return;
    this.modalEl.classList.add('hidden');
  };

  BuyTimingModal.prototype.setStatus = function (text, type) {
    if (!this.statusEl) return;
    if (!text) {
      this.statusEl.textContent = '';
      this.statusEl.className = 'timing-modal-status hidden';
      return;
    }
    this.statusEl.textContent = text;
    this.statusEl.className = 'timing-modal-status ' + (type || '');
  };

  BuyTimingModal.prototype.setBody = function (html) {
    if (!this.bodyEl) return;
    if (!html) {
      this.bodyEl.innerHTML = '';
      this.bodyEl.classList.add('hidden');
      return;
    }
    this.bodyEl.innerHTML = html;
    this.bodyEl.classList.remove('hidden');
  };

  BuyTimingModal.prototype.reportError = function (message) {
    this.setStatus(String(message || 'Failed to load timing analysis.'), 'error');
    if (typeof this.onError === 'function') this.onError(message);
  };

  BuyTimingModal.prototype.loadTiming = async function (params) {
    var productId = String(params.productId || '').trim();
    var requestToken = Number(params.requestToken || 0);
    var cacheKey = productId;
    var cached = this.sessionCache.get(cacheKey);
    if (cached) {
      if (requestToken !== this.currentRequestToken) return;
      this.render(cached, true);
      return;
    }

    try {
      var apiBase = typeof this.getApiBase === 'function' ? this.getApiBase() : '';
      var url = apiBase.replace(/\/+$/, '') + '/api/buy-timing?productId=' + encodeURIComponent(productId);
      if (params.currentPrice != null && isFinite(Number(params.currentPrice))) {
        url += '&currentPrice=' + encodeURIComponent(String(Number(params.currentPrice)));
      }
      if (params.title) {
        url += '&title=' + encodeURIComponent(String(params.title));
      }
      if (params.category) {
        url += '&category=' + encodeURIComponent(String(params.category));
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
      if (!data || typeof data !== 'object') {
        throw new Error('Unexpected response format');
      }

      this.sessionCache.set(cacheKey, data);
      if (requestToken !== this.currentRequestToken) return;
      this.render(data, false);
    } catch (err) {
      if (requestToken !== this.currentRequestToken) return;
      var msg = (err && err.message) ? err.message : 'Failed to load timing analysis';
      this.reportError('Timing analysis unavailable: ' + msg);
    }
  };

  BuyTimingModal.prototype.render = function (data, fromCache) {
    this.setStatus(fromCache ? 'Loaded from session cache.' : '', '');

    var best = data.bestWindow || {};
    var worst = data.worstWindow || {};
    var next = data.nextBestWindowThisYear || {};
    var confidence = String(data.confidence || 'low');
    var explanation = Array.isArray(data.explanation) ? data.explanation : [];

    var html = [
      '<div class="timing-grid">',
      '<section class="timing-card timing-best">',
      '<h3>Best Time To Buy</h3>',
      '<p><strong>' + escapeHtml(String(best.name || 'Unknown')) + '</strong></p>',
      '<p>' + escapeHtml(String(best.approxDateRange || 'Unknown')) + '</p>',
      '<p>Typical drop: ' + escapeHtml(formatRange(best.typicalDropPctRange && best.typicalDropPctRange[0], best.typicalDropPctRange && best.typicalDropPctRange[1], '%')) + '</p>',
      '<p>Average discount: ' + escapeHtml((Number(best.avgDiscountPct || 0)).toFixed(1) + '%') + '</p>',
      '</section>',
      '<section class="timing-card timing-worst">',
      '<h3>Worst Time To Buy</h3>',
      '<p><strong>' + escapeHtml(String(worst.name || 'Unknown')) + '</strong></p>',
      '<p>' + escapeHtml(String(worst.approxDateRange || 'Unknown')) + '</p>',
      '<p>Typical increase: ' + escapeHtml(formatRange(worst.typicalIncreasePctRange && worst.typicalIncreasePctRange[0], worst.typicalIncreasePctRange && worst.typicalIncreasePctRange[1], '%')) + '</p>',
      '<p>Average premium: ' + escapeHtml((Number(worst.avgPremiumPct || 0)).toFixed(1) + '%') + '</p>',
      '</section>',
      '</div>',
      '<section class="timing-next">',
      '<h3>Next Best Window</h3>',
      '<p><strong>' + escapeHtml(String(next.name || 'Unknown')) + '</strong></p>',
      '<p>' + escapeHtml(String(next.startDate || '—')) + ' to ' + escapeHtml(String(next.endDate || '—')) + '</p>',
      '<p>Starts in ' + escapeHtml(String(next.daysUntilStart != null ? next.daysUntilStart : '—')) + ' day(s)</p>',
      '</section>',
      '<p class="timing-confidence">Confidence: ' + escapeHtml(confidence) + '</p>'
    ];

    if (data.currency && data.currentPrice != null) {
      html.push('<p class="timing-current">Current observed price: ' + escapeHtml(formatMoney(data.currentPrice, data.currency)) + '</p>');
    }

    if (explanation.length) {
      html.push('<ul class="timing-explanation">');
      explanation.slice(0, 4).forEach(function (line) {
        html.push('<li>' + escapeHtml(String(line || '')) + '</li>');
      });
      html.push('</ul>');
    }

    this.setBody(html.join(''));
  };

  window.BuyTimingModal = BuyTimingModal;
})();
