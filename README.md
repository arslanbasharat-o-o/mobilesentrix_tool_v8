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
