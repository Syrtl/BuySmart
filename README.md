# ProcureWise (BuySmart)

A Chrome extension (Manifest V3) that calls a FastAPI backend to recommend products from a store catalog.

---

## Demo Kit (UIC Hackathon)

- **The backend is deployed on Railway.**  
- **All API keys are stored exclusively in Railway environment variables.**  
- **No secrets are shipped to the client or committed to the repository.**

The extension never sees or stores API keys; it only sends requests to the backend URL you enter. If the backend runs locally without cloud keys, it uses the **deterministic recommender** (no LLM).

### Demo steps (5)

1. **Install the Chrome extension**  
   Open `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → select the **`extension/`** folder from this repo.

2. **Open the extension popup**  
   Click the ProcureWise icon in the Chrome toolbar.

3. **Set API Base URL**  
   In the popup, set **API Base URL** to:  
   `https://buysmart-production-1506.up.railway.app`

4. **Test connection**  
   Click **Test Connection**. You should see a success message (backend `/health` responds).

5. **Get a recommendation**  
   Choose **Amazon** or **Grainger**, enter a task-based shopping request (e.g. *office chair under $200*), then click **Recommend**. Results appear as cards with title, price, category, and explanation.

### Page Catalog mode (recommend from current page)

Recommendations can be limited to products scanned from the **currently open** shopping page (e.g. Amazon search results, Grainger category).

1. Open an Amazon or Grainger **search results or category page** in the current tab.
2. Open the ProcureWise popup → click **Scan this page**. You should see e.g. *Scanned 37 products*.
3. Optionally choose **Store: Page (use after Scan)** so it’s clear you’re using the scanned list.
4. Enter a query (e.g. *chair under $150*) and click **Recommend**.
5. Results are **only** from the scanned products; titles with URLs open in a new tab. Use **Clear scanned catalog** to reset.

The backend receives the scanned list as `catalog_override` and recommends strictly from those items (no invented products). When `catalog_override` is present, the backend uses the **deterministic** recommender only (no LLM, no embeddings), so responses are fast and reliable on Railway.

**Page Catalog demo checklist**
- [ ] Open an Amazon (or Grainger) **search results** page in the browser.
- [ ] In the extension popup, click **Scan this page** — you should see e.g. *Scanned N products* (up to 60).
- [ ] Set **Store** to **Page (use after Scan)**.
- [ ] Enter a query (e.g. *chair under $150*) and click **Recommend**.
- [ ] Results are from the scanned page only; response is deterministic and does not use the LLM for override mode.

### Manual demo checklist (Page Catalog)

1. Open an **Amazon search results** page in Chrome.
2. In ProcureWise popup, click **Scan this page**.
3. Set **Store** = **Page (use after Scan)**.
4. Enter a query (for example: `office chair under $200`) and click **Recommend**.
5. Click a result title to open the product URL in a new tab.

### Verify backend (no keys required)

```bash
curl https://buysmart-production-1506.up.railway.app/health
```

Expect: `{"status":"ok"}`

### Live Demo Checklist

- [ ] Railway service is running
- [ ] `/health` responds (e.g. `curl` above)
- [ ] Extension **API Base URL** is set to the Railway URL
- [ ] No secrets in the client or in the repo
- [ ] Fallback recommender works when the LLM is unavailable (backend uses deterministic logic)

---

## Quick reference — copy-paste commands

**Local run (once per machine):**
```bash
cd /path/to/repo
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
./scripts/run_backend.sh
```

**Railway health check and smoke test:**
```bash
# Set your Railway URL
export RAILWAY_URL=https://your-app.up.railway.app

# Health check
curl -s "$RAILWAY_URL/health"

# Full smoke test (health + /recommend + /assistant/recommend)
python3 scripts/smoke_test.py "$RAILWAY_URL"
# or
RAILWAY_URL=https://your-app.up.railway.app ./scripts/smoke_test_railway.sh
```

**Reproduce Page Catalog request (same payload shape as extension):**
```bash
./scripts/repro_page_catalog_override.sh http://127.0.0.1:8000
# or
./scripts/repro_page_catalog_override.sh https://your-app.up.railway.app
```

**Extension setup:**
1. Chrome → `chrome://extensions` → **Developer mode** → **Load unpacked** → select the **`extension/`** folder.
2. Open the popup → set **API Base URL** to your Railway URL (or `http://localhost:8000` for local).
3. Click **Test Connection** (calls `/health`). Then use **Demo Preset** or type a query and click **Recommend**.

---

## Monorepo structure

- **backend/** — FastAPI server with recommender, TCO, and explain services
- **extension/** — Chrome extension (MV3) popup UI
- **shared/** — Optional shared types (not used in minimal scaffold)

## Requirements

- **Python 3.9+** (for backend; sentence-transformers and scikit-learn need a recent Python)
- Chrome (for the extension)

---

## Quick Demo (3 minutes) — local backend

Local backend runs **without** the LLM and uses the **deterministic** catalog-only recommender. Install dependencies once: `pip install -r backend/requirements.txt` (from repo root, with venv activated).
For local tests, also install dev deps: `pip install -r backend/requirements-dev.txt`.

1. **Start the backend** (from the repo root):
   ```bash
   ./scripts/run_backend.sh
   ```
   Wait until you see `Uvicorn running on http://127.0.0.1:8000`. (First run may take 1–2 minutes while the embedding model downloads.)

2. **Load the Chrome extension**
   - Open Chrome → **Extensions** → **Manage extensions** → **Load unpacked**
   - Select the **`extension`** folder inside this repo.

3. **Try a recommendation**
   - Leave **API Base URL** as `http://localhost:8000` (default). Click the ProcureWise icon.
   - Choose **Amazon** or **Grainger**, type e.g. *durable office chair under $200*, click **Recommend**.
   - Results appear as cards. If the backend is not running, the popup shows a friendly error.

4. **Optional: smoke test**
   ```bash
   python3 scripts/smoke_test.py
   ```
   Expect: `GET /health` and `POST /recommend` succeed and a short sample response is printed.

---

## Quick start (detailed)

### 1. Backend

From the **repo root** (directory containing `backend/` and `extension/`):

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

Server runs at **http://localhost:8000**. Docs: http://localhost:8000/docs

### 2. Chrome extension

1. Open Chrome → **Extensions** → **Manage extensions** → **Load unpacked**
2. Select the **extension** folder from this repo
3. Ensure the backend is running, then click the extension icon and use the popup

### 3. Demo flow

1. Start backend (see above)
2. Load extension unpacked
3. In popup: choose store (Amazon or Grainger), type a request (e.g. "durable office chair under $200"), click **Recommend**
4. Results show as cards with title, price, category, and "Why"

## API

- **POST /recommend**
  - Body: `{ "user_text": string, "store": "amazon"|"grainger", "k": number }`
  - Response: list of `{ id, title, price, category, score, why }`

- **GET /api/price-history**
  - Query: `productId=XXX&weeks=13` (optional: `currentPrice=123.45`; `days=90` still supported)
  - Response: `{ productId, currency, weeks, points[13], min, max, current, lastUpdated, source }`

Price history is mock-generated & cached for demo; consistent for 24 hours.

## Tech stack

- Backend: FastAPI, sentence-transformers, scikit-learn, Pydantic
- Extension: Manifest V3, vanilla JS, minimal CSS
- All runs locally; no external APIs required for the demo.

---

## Deploy to Railway

Deploy the **backend only** so the extension can call it from anywhere. No code edits needed after setup.  
**Note:** The first Railway deploy can be slow; subsequent deploys are faster due to Docker layer caching (dependencies are cached until `requirements.txt` changes).

### Railway Settings (recommended)

Set this Railway variable for small instances and fast cold starts:

- `DISABLE_EMBEDDINGS=1`

Embeddings are optional. With `DISABLE_EMBEDDINGS=1`, `POST /recommend` uses a fast deterministic catalog fallback and keeps the same response schema.
To keep Railway builds faster, heavy embedding dependencies are separated into `backend/requirements-embeddings.txt` and are not installed by default. Dev-only packages are in `backend/requirements-dev.txt` and are also excluded from Railway image installs.

### 1. Create project and connect repo

1. Go to [railway.app](https://railway.app) and sign in.
2. **New Project** → **Deploy from GitHub repo**.
3. Select this repository and connect (authorize if prompted).

### 2. Configure the service

- **Root Directory:** leave empty (use repo root so `COPY backend/` in the Dockerfile works).
- **Build:** Railway should detect `railway.json` and use **Dockerfile** at `backend/Dockerfile`.  
  If not: **Settings** → **Build** → Builder: **Dockerfile** → Dockerfile path: `backend/Dockerfile`.
- **Deploy:** Set **PORT=8000** in **Railway → Variables** (recommended) so the app listens on 8000 and matches Railway’s default proxy. Other API keys and secrets go in Variables only; never committed or sent to the client.
- **If Railway returns 502:** first fix port mismatch (see **Railway 502 Fix (Port mismatch)** below). If needed, set `DISABLE_EMBEDDINGS=1` to avoid heavy model load and use deterministic recommendations.
- **Start command:** do **not** set a custom start command in Railway. The image uses an **ENTRYPOINT** that expands `PORT` in a shell; overriding it can cause `'$PORT' is not a valid integer`. If you must set one, use:  
  `sh -c "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"`

### 3. Deploy and get the URL

1. Trigger a deploy (push to `main` or **Deploy** in the dashboard).
2. In the service, open **Settings** → **Networking** → **Generate Domain** (or use the default public URL).
3. Copy the public URL (e.g. `https://your-app.up.railway.app`). No trailing slash.

### 4. Verify deployment

Logs should show:

```text
Uvicorn running on http://0.0.0.0:XXXX
```

Then test from your machine:

```bash
# Replace with your Railway URL
export RAILWAY_URL=https://your-app.up.railway.app

curl -s "$RAILWAY_URL/health"
# Expect: {"status":"ok"}

curl -s -X POST "$RAILWAY_URL/recommend" \
  -H "Content-Type: application/json" \
  -d '{"user_text":"office chair","store":"amazon","k":2}'
# Expect: JSON array of recommendation objects
```

Or use the smoke test:

```bash
python3 scripts/smoke_test.py https://your-app.up.railway.app
# or
RAILWAY_URL=https://your-app.up.railway.app ./scripts/smoke_test_railway.sh
```

### Railway 502 Fix (Port mismatch)

If **/health** returns **502** and Railway logs show e.g. `Uvicorn running on http://0.0.0.0:8080` while **Public Networking** targets port **8000**, the edge proxy is hitting the wrong port.

**Fix (recommended):**

1. In Railway: open your service → **Variables**.
2. Add **PORT** = **8000** (so the app listens on 8000 and matches the default proxy).
3. **Redeploy** the service.
4. Check logs for `Starting on PORT=8000` and `Uvicorn running on http://0.0.0.0:8000`.
5. Verify: `curl -i https://your-app.up.railway.app/health` → expect **200** and `{"status":"ok"}`.

**Alternative:** Change Railway **Networking** target port to **8080** (or whatever port the logs show) instead of setting PORT. We recommend setting **PORT=8000** so the app and proxy both use 8000.

**If you still see 502:** Check Railway logs for errors. Set **DISABLE_EMBEDDINGS=1** in **Variables** (recommended for hackathon demo — fast cold starts, deterministic recommendations) and redeploy.

### 5. Use the extension with Railway

1. Open the ProcureWise popup.
2. In **API Base URL**, enter your Railway URL (e.g. `https://your-app.up.railway.app`).
3. Click **Test Connection**. You should see "Connected: …".
4. The value is saved in the browser (localStorage) for the next time.
5. Use **Recommend** as usual; requests go to the Railway backend.
