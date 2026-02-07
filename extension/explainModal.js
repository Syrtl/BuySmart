(function () {
  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = String(text == null ? '' : text);
    return div.innerHTML;
  }

  function safePreview(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  }

  function formatMoney(value) {
    var n = Number(value);
    if (!isFinite(n)) return '—';
    return '$' + n.toFixed(2);
  }

  function ExplainModal(options) {
    options = options || {};
    this.getApiBase = options.getApiBase;
    this.fetchWithTimeout = options.fetchWithTimeout;
    this.onError = options.onError;

    this.modalEl = document.getElementById('explainModal');
    this.closeBtnEl = document.getElementById('explainModalClose');
    this.productEl = document.getElementById('explainModalProduct');
    this.statusEl = document.getElementById('explainModalStatus');
    this.summaryEl = document.getElementById('explainModalSummary');
    this.bodyEl = document.getElementById('explainModalBody');

    this.currentToken = 0;

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

  ExplainModal.prototype.handleDocKeyDown = function (evt) {
    if (evt && evt.key === 'Escape' && this.isOpen()) this.close();
  };

  ExplainModal.prototype.isOpen = function () {
    return !!(this.modalEl && !this.modalEl.classList.contains('hidden'));
  };

  ExplainModal.prototype.close = function () {
    if (!this.modalEl) return;
    this.modalEl.classList.add('hidden');
  };

  ExplainModal.prototype.setStatus = function (text, type) {
    if (!this.statusEl) return;
    if (!text) {
      this.statusEl.textContent = '';
      this.statusEl.className = 'explain-modal-status hidden';
      return;
    }
    this.statusEl.textContent = text;
    this.statusEl.className = 'explain-modal-status ' + (type || '');
  };

  ExplainModal.prototype.setSummary = function (text) {
    if (!this.summaryEl) return;
    if (!text) {
      this.summaryEl.textContent = '';
      this.summaryEl.classList.add('hidden');
      return;
    }
    this.summaryEl.textContent = String(text);
    this.summaryEl.classList.remove('hidden');
  };

  ExplainModal.prototype.setBodyHtml = function (html) {
    if (!this.bodyEl) return;
    if (!html) {
      this.bodyEl.innerHTML = '';
      this.bodyEl.classList.add('hidden');
      return;
    }
    this.bodyEl.innerHTML = html;
    this.bodyEl.classList.remove('hidden');
  };

  ExplainModal.prototype.open = function (params) {
    params = params || {};
    var productId = String(params.productId || '').trim();
    var title = String(params.title || '').trim();
    var candidates = Array.isArray(params.candidates) ? params.candidates : [];
    var userText = String(params.userText || '').trim();

    if (!this.modalEl) return;
    if (!productId || !candidates.length) {
      this.setStatus('Explain is unavailable for this item.', 'error');
      return;
    }

    this.currentToken += 1;
    var token = this.currentToken;

    this.modalEl.classList.remove('hidden');
    this.setStatus('Preparing explanation…', 'loading');
    this.setSummary('');
    this.setBodyHtml('');

    if (this.productEl) {
      this.productEl.textContent = title || productId;
      this.productEl.classList.remove('hidden');
    }

    this.loadExplanation({
      productId: productId,
      title: title,
      userText: userText,
      intent: params.intent || null,
      candidates: candidates,
      token: token,
    });
  };

  ExplainModal.prototype.loadExplanation = async function (params) {
    try {
      var apiBase = typeof this.getApiBase === 'function' ? this.getApiBase() : '';
      var url = apiBase.replace(/\/+$/, '') + '/api/explain';
      var payload = {
        userText: params.userText,
        intent: params.intent,
        selectedId: params.productId,
        candidates: params.candidates,
      };

      var res;
      if (typeof this.fetchWithTimeout === 'function') {
        res = await this.fetchWithTimeout(
          url,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          },
          45000
        );
      } else {
        res = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      var raw = await res.text();
      if (!res.ok) {
        throw new Error('HTTP ' + res.status + ': ' + safePreview(raw));
      }

      var data = raw ? JSON.parse(raw) : null;
      if (params.token !== this.currentToken) return;

      this.renderResponse(data, params.productId);
    } catch (err) {
      if (params.token !== this.currentToken) return;
      this.setStatus((err && err.message) ? err.message : 'Explanation failed.', 'error');
      if (typeof this.onError === 'function') {
        this.onError((err && err.message) ? err.message : 'Explanation failed.');
      }
    }
  };

  ExplainModal.prototype.renderResponse = function (data, selectedId) {
    var summary = data && data.summary ? String(data.summary) : '';
    this.setStatus('', '');
    this.setSummary(summary || 'Explanation generated from deterministic ranking metrics.');

    var selectedText = '';
    var items = (data && Array.isArray(data.items)) ? data.items : [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      if (String(item.id || '') === String(selectedId || '')) {
        selectedText = String(item.explanation || '');
        break;
      }
    }
    if (!selectedText && items.length > 0) {
      selectedText = String(items[0].explanation || '');
    }

    var tableRows = [];
    var scoreTable = (data && Array.isArray(data.scoreTable)) ? data.scoreTable : [];
    for (var j = 0; j < scoreTable.length; j++) {
      var row = scoreTable[j] || {};
      var id = String(row.id || '');
      var isSelected = id === String(selectedId || '');
      tableRows.push(
        '<tr' + (isSelected ? ' class="is-selected"' : '') + '>' +
          '<td>' + escapeHtml(String(row.title || id)) + '</td>' +
          '<td>' + escapeHtml(formatMoney(row.price)) + '</td>' +
          '<td>' + escapeHtml(String(Number(row.qualityScore || 0).toFixed(1))) + '</td>' +
          '<td>' + escapeHtml(String(Number(row.priceFitScore || 0).toFixed(1))) + '</td>' +
          '<td>' + escapeHtml(String(Number(row.requirementMatch || 0).toFixed(1))) + '</td>' +
          '<td>' + escapeHtml(String(Number(row.totalScore || 0).toFixed(1))) + '</td>' +
        '</tr>'
      );
    }

    var html = [
      '<div class="explain-modal-block">',
      '<h3>Consultant Explanation</h3>',
      '<p>' + escapeHtml(selectedText || 'No explanation available.') + '</p>',
      '</div>',
      '<div class="explain-modal-block">',
      '<h3>Score Table</h3>',
      '<div class="explain-table-wrap">',
      '<table class="explain-table">',
      '<thead><tr><th>Item</th><th>Price</th><th>Quality</th><th>Price Fit</th><th>Req.</th><th>Total</th></tr></thead>',
      '<tbody>' + tableRows.join('') + '</tbody>',
      '</table>',
      '</div>',
      '</div>'
    ].join('');

    this.setBodyHtml(html);
  };

  window.ExplainModal = ExplainModal;
})();
