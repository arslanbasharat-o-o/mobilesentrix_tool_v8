# MobileSentrix Extractor (v8)

A Flask-based tool with a modern UI to fetch product listings, filter by price/keywords, and export results (CSV/XLSX). Frontend is a single-page app in Bootstrap 5 with local watchlist, sorting, and pagination.

## Features
- Paste one or more category/product URLs; auto-crawls up to 20 pages
- Client-side filters: price min/max, include/exclude keywords, sorting
- Watchlist with localStorage (★), price-drop memory & alerts
- Export visible rows to CSV/XLSX; copy table
- Dark/Light toggle and responsive UI

## Tech Stack
- Python (Flask)
- Requests, BeautifulSoup, curl_cffi
- OpenPyXL for Excel export
- Frontend: Bootstrap 5 + vanilla JS

## Project Structure
```

.
├─ app.py
├─ requirements.txt
├─ .gitignore
└─ templates/
└─ index.html

````

## Getting Started

### Prerequisites
- Python 3.10+ recommended
- pip (comes with modern Python)
- (Optional) virtualenv

### 1) Clone & enter the project
```bash
git clone https://github.com/arslanbasharat-o-o/mobilesentrix_tool_v8.git
cd mobilesentrix_tool_v8
````

### 2) Create & activate a virtual environment

```bash
python3 -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate
```

### 3) Install dependencies

```bash
pip install -r requirements.txt
```

## Run Locally

### Option A — Run the script directly

```bash
python app.py
```

### Option B — Use Flask CLI

```bash
export FLASK_APP=app.py
export FLASK_ENV=development   # optional auto-reload
flask run                      # usually http://127.0.0.1:5000
```

Open your browser to the printed URL (typically `http://127.0.0.1:5000`) and use the UI.

## How to Use (UI)

1. Paste one or more category/product URLs (one per line).
2. Set optional discount rules, price guardrails, and keyword filters.
3. Click **Fetch**.
4. Sort/filter in the table, star (★) items to watch, and export CSV/XLSX.
5. Use **Show watchlist only** to focus on tracked items.
6. **Price drop alerts** are shown when previously seen prices drop by your configured threshold.

## API

### `POST /api/scrape`

The frontend posts to this endpoint with JSON (example below):

```json
{
  "urls": "https://www.mobilesentrix.ca/...\nhttps://www.mobilesentrix.com/...",
  "percent_off": 0,
  "absolute_off": 0,
  "crawl_pagination": true,
  "max_pages": 20
}
```

**Response (example shape)**

```json
{
  "items": [
    {
      "url": "https://www.example.com/product/123",
      "site": "mobilesentrix",
      "image_url": "https://...",
      "title": "LCD Assembly for Galaxy A01 A015",
      "original_formatted": "CA$16.51",
      "discounted_formatted": "CA$15.03"
      // ...other fields your scraper returns
    }
  ]
}
```

> The UI derives per-row values (final price, model key, deltas vs. comparison CSV, watchlist state) client-side.

## CSV / XLSX Export

* **CSV** export includes visible rows with columns like:

  * `#`, `image_url`, `title`, `original`, `percent_off`, `absolute_off`, `final`,
  * `url`, `source`, `clean_title`, `model`, `compare_price`, `delta`, `delta_pct`, `watchlisted`
* **XLSX** export mirrors CSV content (OpenPyXL under the hood).

## Comparison CSV (Optional)

Upload a CSV (with headers like `title` and `price`) to compare your current scraped prices against previous/baseline prices. The UI calculates `Δ` and `Δ%` for quick change detection.

## Watchlist & Price Memory

* **Watchlist** (★) is stored in `localStorage` per browser.
* **Price memory** stores last-seen prices per URL locally to trigger **drop alerts** when the current price dips by your configured `%`.

## Environment & Config

* Keep secrets outside the repo (environment variables or a private config).
* For local dev, you can export environment variables before running.
* Ensure your `.gitignore` excludes OS/editor junk (e.g., `.DS_Store`).

## Troubleshooting

* **Nothing happens on Fetch**

  * Check that `app.py` is running and serving `/api/scrape`.
  * Verify the URLs are reachable and valid.
* **403/blocked requests**

  * Some sites block scraping. Consider adjusting headers, using `curl_cffi`, backoff, or caching.
* **XLSX export issues**

  * Make sure `openpyxl` is installed per `requirements.txt`.

## Deployment (quick ideas)

* **Render.com / Railway.app**: Deploy Flask as a web service, add required build & start commands.
* **Gunicorn + Reverse Proxy**: `gunicorn "app:app" --bind 0.0.0.0:${PORT:-5000}` behind Nginx/Apache.
* Add a `Procfile` if targeting platforms that use it.

## Contributing

PRs welcome. Please:

1. Fork the repo
2. Create a feature branch
3. Commit with clear messages
4. Open a Pull Request

## License

Choose a license (e.g., MIT). Add it as `LICENSE` in the repo.


