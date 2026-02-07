# BuySmart

This is a Chrome extension (built with Manifest V3) that hooks up to a FastAPI backend to suggest products from a store’s catalog.

Demo Kit (SparkHacks)

- The backend runs on Railway.
- All API keys live in Railway environment variables—nowhere else.
- No secrets ever go to the client or end up in the repository.

The extension never touches or stores your API keys. It just sends requests to whatever backend URL you give it. If you run the backend locally without cloud keys, it falls back to the deterministic recommender (no LLM involved).

How to Demo (5 steps)

1. Install the Chrome extension  
   Go to chrome://extensions, switch on Developer mode, pick “Load unpacked,” and grab the extension/ folder from this repo.

2. 1. **Open Extension popup.** 
    Click the BuySmart Chrome toolbar icon.

2. **Set base API URL in popup.** 
    Set the **Base API URL** to: 
    `https://buysmart-production-1506.up.railway.app` in the popup window.

3. **Test Connection.** 
    Click the **Test Connection** button, you will see a successful connection message, returning a successful status code for the GET /health API call.

4. **Get recommendation.**
    Select **Amazon** or **Grainger**, enter a search term for purchasing (i.e. *office chair under $200*) and click on the **Recommend** button. The recommendation will appear in content card format with the title of the item, price of the item, category of the item, and an explanation.

5. Page Catalog Mode (recommendation based on current page)
     - Recommendation can be limited to only products that have been scanned by BuySmart from the **open shopping page** (e.g. Amazon search results or Granger's category page).
    
     1. Open the Amazon or Grainger **Category or Search Results page** in your current tab. 
     2. Open the BuySmart popup and **Scan this page**. After successfully scanning, you will see the number of products scanned (i.e. *37 Products Scanned*).
     3. Select **Store: Page (use after scan)** if you want to use the above options out of the scanned product list.
     4. Enter search criteria (i.e. *chair under $150*) and click **Recommend**.
     5. You will only receive product recommendations from the scanned products and all titles that include URLs will open in new tabs. To start over, click on the **Clear Scanned Catalog** button. 
     
    
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
**Installing the Extension**:
1. To Install the Extension, go to: `chrome://extensions`, select **Developer Mode** in the top right corner, then click on **Load Unpacked Extension**, and select the extension/ folder.
2. Open the extension's popup, enter your Railway URL in the **API Base URL** textbox (for local, use: `http://localhost:8000`).
3. Click on **Test Connection** (this calls the "/health" endpoint). You can now use either the **Demo Preset**, or enter a query and click on **Recommend**.

---

## Monorepo Layout

- **backend/** - Contains a FastAPI server that runs the recommender, TCO, and explain services.
- **extension/** - Contains the Chrome MV3 extension popup UI code.
- **shared/** - Optional folder containing any shared types we might reuse (optional in minimal scaffolding).

## Prerequisites

- **Python version 3.9 or greater** (this is required for the backend; both sentence-transformers and scikit-learn require a version of Python 3.6 or newer)
- Chrome (you'll need this to run the extension).

---

## Quick Demo (3 minutes) - Local Backend

When running a local backend, we are not using the LLM (Large Language Model) and will be using a **deterministic** catalog-only recommender system. First install all dependencies from the repository root (after activating your virtual environment): `pip install -r backend/requirements.txt`. If you want to run tests locally, you'll also need to install development dependencies: `pip install -r backend/requirements-dev.txt`.

To run the demo, you will need to do the following steps:

1. Start the backend service from the repository root:
   ```bash
   ./scripts/run_backend.sh
   ```
   Wait for the Uvicorn server to start and display the following in the console: `Uvicorn running on http://127.0.0.1:8000`. (Note: the first time you run this, it might take 1-2 minutes to start because of the time it takes to download the embeddings model.)

2. Install the extension in Chrome:
   - Click "Settings" in Chrome and select the Extensions option from the left sidebar. Under Extensions, select **Manage Extensions** and click on the **Load Unpacked** link.
   - Locate and select the `extension` folder from this repository.
   
   
3. **Try a recommendation**
   - Leave **API Base URL** as `http://localhost:8000` (default). Click the BuySmart icon.
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

## Chrome Extension

1. Open Chrome →  Extensions →  Manage Extensions →  Load Unpacked
2. Select the Extension Folder from this Repository
3. Make sure Your Backend Is Running then Click on Extension Icon and Use the Pop-Up

## Demo Flow

1. Start The Backend (See Above)
2. Load the Extension Unpacked
3. Select Amazon or Grainger From The Drop Down And Type In your Search (for example, "office chair(take the first one)"> click RECOMMEND
4. Your Output will be displayed as a Card including Title, Price, Category, and Why.
## API

- **POST /recommend**
  - Body: `{ "user_text": string, "store": "amazon"|"grainger", "k": number }`
  - Response: list of `{ id, title, price, category, score, why }`

- **GET /api/price-history**
  - Query: `productId=XXX&weeks=13` (optional: `currentPrice=123.45`; `days=90` still supported)
  - Response: `{ productId, currency, weeks, points[13], min, max, current, lastUpdated, source }`

- **GET /api/buy-timing**
  - Query: `productId=XXX` (optional: `currentPrice`, `title`, `category`)
  - Response: `{ productId, currency, bestWindow, worstWindow, nextBestWindowThisYear, explanation, confidence }`

- **GET /api/value-chart**
  - Query: `productId=XXX` (optional: `currentPrice`, `title`, `category`, `rating`, `reviewCount`)
  - Response: `{ productId, currency, points[], optimalId, frontierIds[], explanation[] }`

Price history is mock-generated & cached for demo; consistent for 24 hours.
Buy-timing is heuristic and category-calendar based for demo purposes; it is not a guarantee of future pricing.
Price-vs-quality chart uses page/catalog comparables and an explainable heuristic (rating x log10(reviews+1), normalized); it is also not a guarantee.

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
### Railway 502 Error (Port Mismatch)

When a **502** response is returned from the **/health** endpoint and the Railway logs show that the `Uvicorn` is running at `http://0.0.0.0:8080` while the **Public Networking** option is routing traffic to port `8000`, you are currently using the wrong port due to an edge proxy server.

### Steps to Fix (Recommended):

1. Go to your Service in Railway, click on the 'Variables' section.
2. Add a new variable for **PORT** and set it to **8000**. This will change it so that your app listens on `8000`, the same port that the Railway default proxy routes to.
3. Redeploy your service.
4. Check the logs to verify that the app started with the same port you set in Step 2. "Starting on PORT=8000" and "Uvicorn running at http://0.0.0.0:8000" will confirm.
5. Verify your health endpoint with `curl -i https://your-app.up.railway.app/health`. You should receive a response with a `200 OK` status and payload `{"status":"ok"}`.

### Alternative:

If you do not want to set the PORT variable, you can also change the target port under **Networking** to the same port number shown in the logs. In our example, you will adjust the Networking Target port to `8080`, although we suggest setting the PORT variable so that the app and proxy will operate on the same port.

**If you still receive a 502 error:**

Check the logs for an error and set the **DISABLE_EMBEDDINGS=1** environment variable (recommended for hackathon demo if you want to see better cold start performance, and consistent recommendations) and redeploy.

### 5. How to use the extension with Railway:

1. Click on the BuySmart popup.
2. Input your Railway URL as your **API Base URL** (e.g. `https://your-app.up.railway.app`).
3. Click on Test Connection, you should see a "Success:..." message returned.
4. This connection will be saved in your localStorage for the next request you make.
5. You can continue to use **Recommend** as you always have, however, all of these requests will now be directed to your Railway backend.
