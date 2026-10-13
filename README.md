# FoodLens

FoodLens turns packaged food labels into a quick, plain-language read: a nutrition score, likely allergens, a processing estimate, and a few products to compare. It runs without accounts.

## Run locally

Requires Python 3.11+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r Backend/requirements.txt
uvicorn Backend.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). The API docs are at `/docs`; the health check is `/api/health`. SQLite creates its database and starter catalog on first launch. Set `DATABASE_PATH` to choose another location.

## Run with Docker

```sh
docker compose up --build
```

The compose setup persists the SQLite file in a named volume. The included `render.yaml` deploys a single Docker web service with a persistent disk. SQLite needs persistent storage in production; do not deploy it on an ephemeral filesystem.

## What the app does

- Looks up the local catalog by barcode and requests available ingredient, nutrition, allergen, and label-image fields from Open Food Facts. A sourced catalog fallback fills the known barcode `8901719133886` with its matching [Tesco product listing](https://www.tesco.com/shop/en-GB/products/321050864) when Open Food Facts has no ingredient panel. When a product has only a usable label image, the browser can read and save recognized ingredients to SQLite for future scans.
- Analyzes pasted ingredients and optional nutrition values without requiring an account. The photo flow runs English text recognition in the browser, then lets the user review extracted text before analysis.
- Highlights common allergens and processing-related ingredients, estimates a NOVA group, and suggests catalog items to compare.
- Supports catalog search and selecting a product card to analyze it.

## Important limits

The starter catalog is sample data, and public barcode coverage depends on Open Food Facts plus the small set of curated fallbacks in the database. If a barcode has no ingredient record or readable ingredients image in any configured source, FoodLens reports missing data rather than inventing a list or score. Photo recognition uses Tesseract.js from jsDelivr and works best with clear English label photos; an internet connection is needed for its initial library/model download. Ingredient matching, processing classification, and alternative ranking are heuristic; nutrition scoring is a simple comparison aid rather than medical advice. Always check the package, especially for allergies. Personalized allergy profiles are not saved; they are sent only with the single analysis request.

## Configuration

- `DATABASE_PATH`: SQLite database file path (default `Backend/foodlens.db`).
- `CORS_ORIGINS`: comma-separated allowed origins for a separately hosted frontend; defaults to `*` for local/single-service deployment.
