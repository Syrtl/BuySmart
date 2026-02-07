(function () {
  const MAX_ITEMS = 60;
  const SNIPPET_MAX_LEN = 220;

  function parsePrice(text) {
    if (!text || typeof text !== 'string') return null;
    const cleaned = text.replace(/,/g, '');
    const match = cleaned.match(/\$?\s*(\d+\.?\d*)/);
    return match ? parseFloat(match[1]) : null;
  }

  function parseRating(text) {
    if (!text || typeof text !== 'string') return null;
    const m = text.replace(',', '.').match(/(\d+(?:\.\d+)?)\s*(?:out of 5|\/5|stars?|звезды|звезд)/i);
    if (!m) return null;
    const n = parseFloat(m[1]);
    return Number.isFinite(n) ? n : null;
  }

  function parseReviewsCount(text) {
    if (!text || typeof text !== 'string') return null;
    const m = text.replace(/,/g, '').match(/(\d{1,7})\s*(?:ratings?|reviews?|отзыв)/i) || text.replace(/,/g, '').match(/(\d{1,7})/);
    if (!m) return null;
    const n = parseInt(m[1], 10);
    return Number.isFinite(n) ? n : null;
  }

  function getText(el) {
    if (!el) return '';
    return (el.textContent || '').trim().replace(/\s+/g, ' ');
  }

  function getAttr(el, name) {
    return (el && el.getAttribute && el.getAttribute(name)) || '';
  }

  function firstNonEmpty(parts) {
    for (const p of parts || []) {
      const t = (p || '').trim();
      if (t) return t;
    }
    return '';
  }

  function mergeTitleAndSubtitle(title, subtitle) {
    const t = (title || '').trim();
    const s = (subtitle || '').trim();
    if (!t) return s.slice(0, 180);
    return t.slice(0, 180);
  }

  function mergeSnippet(subtitle, snippet) {
    const s1 = (subtitle || '').trim();
    const s2 = (snippet || '').trim();
    if (!s1 && !s2) return null;
    if (!s1) return s2.slice(0, SNIPPET_MAX_LEN);
    if (!s2) return s1.slice(0, SNIPPET_MAX_LEN);
    const s1l = s1.toLowerCase();
    const s2l = s2.toLowerCase();
    if (s2l.includes(s1l)) return s2.slice(0, SNIPPET_MAX_LEN);
    if (s1l.includes(s2l)) return s1.slice(0, SNIPPET_MAX_LEN);
    return (s1 + ' ' + s2).slice(0, SNIPPET_MAX_LEN);
  }

  function appendSnippet(base, part) {
    const b = (base || '').trim();
    const p = (part || '').trim();
    if (!p) return b || null;
    if (!b) return p.slice(0, SNIPPET_MAX_LEN);
    const bl = b.toLowerCase();
    const pl = p.toLowerCase();
    if (bl.includes(pl)) return b.slice(0, SNIPPET_MAX_LEN);
    if (pl.includes(bl)) return p.slice(0, SNIPPET_MAX_LEN);
    return (b + ' ' + p).slice(0, SNIPPET_MAX_LEN);
  }

  function stableId(url, title) {
    const s = (url || '') + (title || '');
    let h = 0;
    for (let i = 0; i < s.length; i++) h = ((h << 5) - h) + s.charCodeAt(i) | 0;
    return Math.abs(h).toString(36).slice(0, 12);
  }

  function extractAmazon() {
    const items = [];
    const seen = new Set();
    const cards = document.querySelectorAll('[data-component-type="s-search-result"], .s-result-item[data-asin], [data-asin]:not([data-asin=""])');
    for (const card of cards) {
      if (items.length >= MAX_ITEMS) break;
      const asin = card.getAttribute('data-asin');
      const link = card.querySelector('h2 a[href*="/dp/"], h2 a[href*="/gp/product/"]') || card.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]');
      const titleEl = card.querySelector('h2 span, .a-text-normal');
      const title = getText(titleEl || link);
      if (!title) continue;
      const subtitle = firstNonEmpty([
        getText(card.querySelector('.a-size-base.a-color-base')),
        getText(card.querySelector('.a-size-base.a-color-secondary')),
        getText(card.querySelector('.a-row.a-size-base.a-color-secondary')),
        getText(card.querySelector('[data-cy="title-recipe"] .a-size-base')),
      ]);
      let url = link ? (link.href || getAttr(link, 'href') || '') : '';
      if (!url) {
        const anyLink = card.querySelector('a[href]');
        url = anyLink ? (anyLink.href || getAttr(anyLink, 'href') || '') : '';
      }
      if (url && !url.startsWith('http')) url = new URL(url, window.location.origin).href;
      const key = url || title;
      if (seen.has(key)) continue;
      seen.add(key);
      let price = null;
      const whole = card.querySelector('.a-price-whole');
      const fraction = card.querySelector('.a-price-fraction');
      if (whole) {
        const wholeText = getText(whole).replace(/[^0-9.]/g, '');
        const fracText = fraction ? getText(fraction) : '00';
        price = parseFloat(wholeText + '.' + fracText.replace(/[^0-9]/g, '').slice(0, 2)) || null;
      }
      if (price === null) {
        const priceEl = card.querySelector('[class*="price"]');
        if (priceEl) price = parsePrice(getText(priceEl));
      }
      const img = card.querySelector('img[src]');
      const image = img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : '';
      const snippetEl = card.querySelector('.a-color-secondary');
      let snippet = mergeSnippet(subtitle, getText(snippetEl));
      const ratingText = firstNonEmpty([
        getText(card.querySelector('.a-icon-alt')),
        getText(card.querySelector('[aria-label*="out of 5"]')),
        getText(card.querySelector('[aria-label*="stars"]')),
      ]);
      const reviewsText = firstNonEmpty([
        getText(card.querySelector('[aria-label*="ratings"]')),
        getText(card.querySelector('[aria-label*="reviews"]')),
        getText(card.querySelector('.a-size-base.s-underline-text')),
      ]);
      const rating = parseRating(ratingText);
      const reviewsCount = parseReviewsCount(reviewsText);
      if (rating != null) snippet = appendSnippet(snippet, 'rating ' + rating + '/5');
      if (reviewsCount != null) snippet = appendSnippet(snippet, String(reviewsCount) + ' reviews');
      const fullTitle = mergeTitleAndSubtitle(title, subtitle);
      items.push({
        id: stableId(url, title),
        title: fullTitle.slice(0, 320),
        price: price,
        url: url || null,
        image: image || null,
        snippet: snippet || null,
        subtitle: subtitle || null,
        rating: rating,
        reviews_count: reviewsCount
      });
    }
    return items;
  }

  function extractGrainger() {
    const items = [];
    const seen = new Set();
    const cards = document.querySelectorAll('[data-testid="product-card"], .product-card, .tile--product, .product, a[href*="/product/"]');
    const linkSelector = 'a[href*="/product/"]';
    for (const card of cards) {
      if (items.length >= MAX_ITEMS) break;
      const link = card.matches && card.matches(linkSelector) ? card : card.querySelector(linkSelector);
      const titleEl = card.querySelector('h2, h3, [class*="title"], [class*="description"] a, a[title]');
      const title = getText(titleEl).slice(0, 300) || (link ? (link.getAttribute('title') || getText(link)).slice(0, 300) : '');
      if (!title && !link) continue;
      const subtitle = firstNonEmpty([
        getText(card.querySelector('[class*="subtitle"]')),
        getText(card.querySelector('[class*="model"]')),
        getText(card.querySelector('[class*="description"]')),
      ]);
      let url = link ? (link.href || getAttr(link, 'href') || '') : '';
      if (!url) {
        const anyLink = card.querySelector('a[href]');
        url = anyLink ? (anyLink.href || getAttr(anyLink, 'href') || '') : '';
      }
      if (url && !url.startsWith('http')) url = new URL(url, window.location.origin).href;
      const key = url || title;
      if (seen.has(key)) continue;
      seen.add(key);
      let price = null;
      const priceEl = card.querySelector('[class*="price"], [data-testid*="price"]');
      if (priceEl) price = parsePrice(getText(priceEl));
      if (price === null && card.textContent) price = parsePrice(card.textContent);
      const img = card.querySelector('img[src]');
      const image = img ? (img.getAttribute('src') || '') : '';
      const ratingText = firstNonEmpty([
        getText(card.querySelector('[class*="rating"]')),
        getText(card.querySelector('[aria-label*="out of 5"]')),
      ]);
      const reviewsText = firstNonEmpty([
        getText(card.querySelector('[class*="review"]')),
        getText(card.querySelector('[aria-label*="reviews"]')),
      ]);
      const rating = parseRating(ratingText);
      const reviewsCount = parseReviewsCount(reviewsText);
      const fullTitle = mergeTitleAndSubtitle(title, subtitle);
      let snippet = mergeSnippet(subtitle, null);
      if (rating != null) snippet = appendSnippet(snippet, 'rating ' + rating + '/5');
      if (reviewsCount != null) snippet = appendSnippet(snippet, String(reviewsCount) + ' reviews');
      items.push({
        id: stableId(url, title),
        title: (fullTitle || 'Product'),
        price: price,
        url: url || null,
        image: image || null,
        snippet: snippet,
        subtitle: subtitle || null,
        rating: rating,
        reviews_count: reviewsCount
      });
    }
    return items;
  }

  function extractGeneric() {
    const items = [];
    const seen = new Set();
    const priceLike = document.querySelectorAll('[class*="price"], [data-a-color="price"], [data-testid*="price"]');
    const priceEls = Array.from(priceLike).filter(function (el) {
      const t = getText(el);
      return /\$|USD|\d+\.\d{2}/.test(t) && t.length < 30;
    });
    for (const priceEl of priceEls) {
      if (items.length >= MAX_ITEMS) break;
      const price = parsePrice(getText(priceEl));
      if (price === null) continue;
      let card = priceEl.closest('article, [class*="card"], [class*="tile"], [class*="product"], li, .item');
      if (!card) card = priceEl.parentElement;
      if (!card) continue;
      const link = card.querySelector('a[href^="http"], a[href^="/"]');
      const titleEl = card.querySelector('h2, h3, h4, [class*="title"]');
      const title = getText(titleEl || link).slice(0, 300);
      if (!title) continue;
      const subtitle = firstNonEmpty([
        getText(card.querySelector('[class*="subtitle"]')),
        getText(card.querySelector('[class*="description"]')),
        getText(card.querySelector('p')),
      ]);
      let url = link ? (link.href || (link.getAttribute('href') ? new URL(link.getAttribute('href'), window.location.origin).href : '')) : '';
      if (!url) {
        const anyLink = card.querySelector('a[href]');
        url = anyLink ? (anyLink.href || (anyLink.getAttribute('href') ? new URL(anyLink.getAttribute('href'), window.location.origin).href : '')) : '';
      }
      const key = url || title;
      if (seen.has(key)) continue;
      seen.add(key);
      const img = card.querySelector('img[src]');
      const image = img ? (img.getAttribute('src') || '') : '';
      const ratingText = firstNonEmpty([
        getText(card.querySelector('[class*="rating"]')),
        getText(card.querySelector('[aria-label*="out of 5"]')),
        getText(card.querySelector('[aria-label*="stars"]')),
      ]);
      const reviewsText = firstNonEmpty([
        getText(card.querySelector('[class*="review"]')),
        getText(card.querySelector('[aria-label*="reviews"]')),
      ]);
      const rating = parseRating(ratingText);
      const reviewsCount = parseReviewsCount(reviewsText);
      const fullTitle = mergeTitleAndSubtitle(title, subtitle);
      let snippet = mergeSnippet(subtitle, null);
      if (rating != null) snippet = appendSnippet(snippet, 'rating ' + rating + '/5');
      if (reviewsCount != null) snippet = appendSnippet(snippet, String(reviewsCount) + ' reviews');
      items.push({
        id: stableId(url, title),
        title: fullTitle,
        price: price,
        url: url || null,
        image: image || null,
        snippet: snippet,
        subtitle: subtitle || null,
        rating: rating,
        reviews_count: reviewsCount
      });
    }
    return items;
  }

  function run() {
    const host = window.location.hostname || '';
    let items = [];
    if (/amazon\./.test(host)) items = extractAmazon();
    if (/grainger\./.test(host)) items = extractGrainger();
    if (items.length === 0) items = extractGeneric();
    return { items: items.slice(0, MAX_ITEMS) };
  }

  chrome.runtime.onMessage.addListener(function (request, _sender, sendResponse) {
    if (request.action === 'extractProducts') {
      try {
        sendResponse(run());
      } catch (e) {
        sendResponse({ items: [], error: (e && e.message) || 'Extraction failed' });
      }
    }
    return true;
  });
})();
