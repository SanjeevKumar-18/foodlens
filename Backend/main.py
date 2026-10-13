from __future__ import annotations

import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATABASE_PATH", ROOT / "foodlens.db"))

# Open Food Facts has a product record for this barcode, but its record only
# contains a front-of-pack image. This exact product is also listed by Tesco
# with its ingredient and nutrition panel, so keep that sourced catalog entry
# available when the upstream record is incomplete. Source: Tesco, "Parle Full
# Toss Baked Masala Masti Flavour Snacks 52g" (product 321050864).
CURATED_PRODUCTS = {
    "8901719133886": {
        "name": "Full Toss Masala Masti",
        "brand": "Parle",
        "category": "Snacks",
        "ingredients": (
            "Rice meal (55%), corn meal (27.5%), gram meal (3.5%), vegetable oil (palmolein), "
            "masala masti seasoning [maltodextrin, sugar, salt, coriander, onion, dehydrated "
            "mango powder, hydrolysed vegetable protein (contains soya), yeast extract powder, "
            "citric acid, flavour enhancers (disodium inosinate, disodium guanylate, glutamic "
            "acid), colour (sulphite ammonia caramel), flavouring]."
        ),
        "allergens": "Soya; may contain peanuts, tree nuts, milk and mustard",
        "calories": 477,
        "sugar": 1.7,
        "sodium": 1040,
        "saturated_fat": 7.8,
        "protein": 8.6,
        "fiber": 0,
        "nova_group": 4,
        "source": "Tesco product listing (curated fallback)",
    },
}


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, barcode TEXT UNIQUE, name TEXT NOT NULL,
            brand TEXT DEFAULT '', category TEXT DEFAULT 'Packaged food', image_url TEXT DEFAULT '',
            ingredients TEXT DEFAULT '', allergens TEXT DEFAULT '',
            calories REAL DEFAULT 0, sugar REAL DEFAULT 0, sodium REAL DEFAULT 0,
            saturated_fat REAL DEFAULT 0, protein REAL DEFAULT 0, fiber REAL DEFAULT 0,
            nutriscore TEXT DEFAULT '', nova_group INTEGER DEFAULT 0, source TEXT DEFAULT 'FoodLens'
        )""")
        count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if not count:
            seed = [
                ('8901058001234','Cocoa Oat Crunch','Good Day','Cereal', 'Whole grain oats, sugar, cocoa powder, sunflower oil, salt', 'May contain milk, soy', 410, 18, 390, 2.4, 9, 7, 'C', 4),
                ('8901725182345','Classic Tomato Soup','Harvest Table','Soup', 'Tomatoes, water, onion, sugar, salt, modified corn starch, natural flavours', '', 62, 6, 540, .3, 1.8, 1.2, 'B', 3),
                ('8901063103456','Peanut Butter Smooth','Nut House','Spreads', 'Roasted peanuts (92%), sugar, palm oil, salt', 'Peanuts', 588, 9, 420, 10, 25, 8, 'C', 3),
                ('8901725194567','Plain Greek Yogurt','Farm & Field','Dairy', 'Pasteurised milk, live cultures', 'Milk', 97, 4, 36, 2, 9, 0, 'A', 1),
                ('8901058112345','Sparkling Lemon Drink','Brightside','Drinks', 'Carbonated water, sugar, citric acid, natural lemon flavour, sucralose', '', 42, 10, 8, 0, 0, 0, 'D', 4),
                ('8906001234567','Roasted Chana','Everyday Pantry','Snacks', 'Roasted chickpeas, sunflower oil, cumin, black pepper, salt', '', 365, 2, 310, .6, 20, 16, 'A', 2),
            ]
            db.executemany("INSERT INTO products (barcode,name,brand,category,ingredients,allergens,calories,sugar,sodium,saturated_fat,protein,fiber,nutriscore,nova_group) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", seed)
        for barcode, item in CURATED_PRODUCTS.items():
            # Preserve an existing Open Food Facts image and Nutri-Score while
            # filling missing label data from the curated listing.
            db.execute("""INSERT INTO products
                (barcode,name,brand,category,ingredients,allergens,calories,sugar,sodium,saturated_fat,protein,fiber,nova_group,source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(barcode) DO UPDATE SET
                    name=excluded.name, brand=excluded.brand, category=excluded.category,
                    ingredients=excluded.ingredients, allergens=excluded.allergens,
                    calories=excluded.calories, sugar=excluded.sugar, sodium=excluded.sodium,
                    saturated_fat=excluded.saturated_fat, protein=excluded.protein,
                    fiber=excluded.fiber, nova_group=excluded.nova_group,
                    source=excluded.source""",
                (barcode,item["name"],item["brand"],item["category"],item["ingredients"],item["allergens"],item["calories"],item["sugar"],item["sodium"],item["saturated_fat"],item["protein"],item["fiber"],item["nova_group"],item["source"]))


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="FoodLens API", version="1.0.0", description="Plain-language packaged food insights.", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_methods=["*"], allow_headers=["*"])


class AnalyzeRequest(BaseModel):
    name: str = "Your product"
    barcode: str = Field(default="", max_length=32)
    source: str = Field(default="", max_length=300)
    image_url: str = Field(default="", max_length=2000)
    ingredients: str = Field(default="", max_length=10000)
    ingredients_available: Optional[bool] = None
    calories: float = Field(default=0, ge=0, le=10000)
    sugar: float = Field(default=0, ge=0, le=1000)
    sodium: float = Field(default=0, ge=0, le=100000)
    saturated_fat: float = Field(default=0, ge=0, le=1000)
    protein: float = Field(default=0, ge=0, le=1000)
    fiber: float = Field(default=0, ge=0, le=1000)
    allergies: list[str] = Field(default_factory=list)
    goal: str = "balanced"


class ProductLabelUpdate(BaseModel):
    ingredients: str = Field(min_length=1, max_length=10000)
    calories: float = Field(default=0, ge=0, le=10000)
    sugar: float = Field(default=0, ge=0, le=1000)
    sodium: float = Field(default=0, ge=0, le=100000)
    saturated_fat: float = Field(default=0, ge=0, le=1000)
    protein: float = Field(default=0, ge=0, le=1000)
    fiber: float = Field(default=0, ge=0, le=1000)


ALLERGENS = {
    "milk": ["milk", "whey", "casein", "lactose", "butter", "cream", "cheese"],
    "peanuts": ["peanut", "groundnut"], "tree nuts": ["tree nut", "almond", "cashew", "hazelnut", "walnut", "pistachio", "pecan"],
    "soy": ["soy", "soya", "lecithin"], "gluten / wheat": ["wheat", "barley", "rye", "gluten", "malt"],
    "eggs": ["egg", "albumin"], "sesame": ["sesame", "tahini"], "fish": ["fish", "anchovy"], "shellfish": ["shrimp", "prawn", "crab", "lobster", "shellfish"],
    "mustard": ["mustard"]
}
ULTRA_MARKERS = ["maltodextrin", "modified starch", "modified corn starch", "high fructose", "hydrogenated", "emulsifier", "artificial colour", "artificial flavor", "artificial flavour", "sucralose", "acesulfame", "polysorbate", "preservative", "flavour enhancer", "isolated protein"]


def analyze(data: AnalyzeRequest):
    text = data.ingredients.lower()
    allergens = [name for name, tokens in ALLERGENS.items() if any(
        re.search(r"\bmalt\b", text) if token == "malt" else re.search(r"\b" + re.escape(token) + r"\w*\b", text)
        for token in tokens
    )]
    allergy_hits = sorted({a.lower() for a in data.allergies} & set(allergens))
    flags = [marker for marker in ULTRA_MARKERS if marker in text]
    has_ingredients = data.ingredients_available if data.ingredients_available is not None else bool(text.strip())
    nova = 0 if not has_ingredients else 4 if len(flags) >= 2 else 3 if flags or re.search(r"\b(sugar|oil|starch|flavour|flavor)\b", text) else 2 if len(text.split(',')) > 3 else 1
    score = 100
    score -= min(35, data.sugar * 1.1)
    score -= min(35, data.sodium / 18)
    score -= min(22, data.saturated_fat * 1.5)
    score += min(12, data.fiber * 1.5)
    score += min(8, data.protein * .35)
    score -= max(0, nova - 1) * 5
    score = max(0, min(100, round(score)))
    has_nutrition = any([data.sugar, data.sodium, data.saturated_fat, data.protein, data.fiber])
    rating = ("Great choice" if score >= 75 else "A fair choice" if score >= 55 else "Worth a second look") if has_ingredients or has_nutrition else "Not enough data"
    highlights = []
    if data.sugar >= 10: highlights.append({"type":"watch", "text":f"{data.sugar:g} g sugar per 100 g is on the high side."})
    elif data.sugar > 0: highlights.append({"type":"good", "text":f"{data.sugar:g} g sugar per 100 g."})
    if data.sodium >= 600: highlights.append({"type":"watch", "text":f"{data.sodium:g} mg sodium per 100 g is high."})
    elif data.sodium > 0: highlights.append({"type":"good", "text":f"{data.sodium:g} mg sodium per 100 g."})
    if data.fiber >= 5: highlights.append({"type":"good", "text":f"A useful {data.fiber:g} g of fiber per 100 g."})
    if flags: highlights.append({"type":"watch", "text":"Contains processing-related ingredients: " + ", ".join(flags[:3]) + "."})
    if not has_ingredients: highlights.append({"type":"watch", "text":"The barcode record has no ingredient list, so allergen and processing checks are incomplete."})
    if not highlights: highlights.append({"type":"neutral", "text":"Add nutrition values for a more complete assessment."})
    tips = {"lower_sugar":"For a lower-sugar choice, compare unsweetened versions and check sugar per 100 g.", "lower_sodium":"For a lower-sodium choice, compare the sodium line per 100 g across similar products.", "more_fiber":"For more fiber, look for whole grains, beans, nuts, or seeds near the start of the ingredient list.", "balanced":"Compare similar products per 100 g and choose shorter ingredient lists with more fiber and less added sugar and salt."}
    image = urlparse(data.image_url)
    safe_image_url = data.image_url if image.scheme == "https" and image.hostname == "images.openfoodfacts.org" else ""
    return {"name":data.name, "barcode":data.barcode, "source":data.source, "image_url":safe_image_url, "ingredients":data.ingredients, "score":score if has_ingredients or has_nutrition else None, "rating":rating, "nova_group":nova,
        "processing_label": {0:"Ingredients unavailable",1:"Minimally processed",2:"Processed culinary ingredients",3:"Processed",4:"Ultra-processed"}[nova],
        "allergens":allergens, "allergy_alerts":allergy_hits, "ingredients_to_notice":flags,
        "highlights":highlights, "tip":tips.get(data.goal, tips["balanced"]),
        "score_note":("No ingredient or nutrition details were available for this barcode. " if not has_ingredients and not has_nutrition else "Nutrition values were not provided, so this score is preliminary. " if not has_nutrition else "") + "A simple comparison aid based on the details provided, not a medical assessment."}


def product_dict(row):
    return dict(row)


@app.get("/api/health")
def health(): return {"status":"ok", "service":"FoodLens API"}


@app.get("/api/products")
def products(q: Optional[str] = None, limit: int = 12):
    with connect() as db:
        if q:
            rows = db.execute("SELECT * FROM products WHERE name LIKE ? OR brand LIKE ? OR category LIKE ? ORDER BY name LIMIT ?", (f"%{q}%",)*3 + (min(limit,50),)).fetchall()
        else: rows = db.execute("SELECT * FROM products ORDER BY name LIMIT ?", (min(limit,50),)).fetchall()
    return [product_dict(r) for r in rows]


@app.get("/api/products/{barcode}")
def product_by_barcode(barcode: str):
    with connect() as db:
        row = db.execute("SELECT * FROM products WHERE barcode=?", (barcode,)).fetchone()
    cached = row
    if row and row["ingredients"].strip():
        return product_dict(row)
    try:
        response = requests.get(f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json", params={"fields":"code,product_name,brands,categories,ingredients_text,ingredients_text_en,ingredients_text_with_allergens,ingredients_text_with_allergens_en,allergens,nutriments,nutriscore_grade,nova_group,image_ingredients_url,image_front_url"}, timeout=8, headers={"User-Agent":"FoodLens/1.0 (food label analyzer)"})
        payload = response.json()
        if payload.get("status") != 1:
            if cached: return product_dict(cached)
            raise HTTPException(404, "Open Food Facts has no record for that barcode yet.")
        p = payload["product"]
        nutr = p.get("nutriments", {})
        ingredients = p.get("ingredients_text") or p.get("ingredients_text_en") or p.get("ingredients_text_with_allergens") or ""
        image_url = p.get("image_ingredients_url") or p.get("image_front_url", "")
        sodium_g = nutr.get("sodium_100g")
        if sodium_g is None: sodium_g = (nutr.get("salt_100g", 0) or 0) / 2.5
        values = (barcode, p.get("product_name") or (cached["name"] if cached else "Scanned product"), p.get("brands", "") or (cached["brand"] if cached else ""), p.get("categories", "Packaged food").split(",")[0], image_url, ingredients, p.get("allergens", ""), nutr.get("energy-kcal_100g", 0) or 0, nutr.get("sugars_100g", 0) or 0, sodium_g * 1000, nutr.get("saturated-fat_100g", 0) or 0, nutr.get("proteins_100g", 0) or 0, nutr.get("fiber_100g", 0) or 0, p.get("nutriscore_grade", ""), p.get("nova_group", 0) or 0, "Open Food Facts")
        with connect() as db:
            db.execute("INSERT OR REPLACE INTO products (barcode,name,brand,category,image_url,ingredients,allergens,calories,sugar,sodium,saturated_fat,protein,fiber,nutriscore,nova_group,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            row = db.execute("SELECT * FROM products WHERE barcode=?", (barcode,)).fetchone()
        return product_dict(row)
    except HTTPException: raise
    except requests.RequestException:
        if cached: return product_dict(cached)
        raise HTTPException(503, "Open Food Facts is temporarily unavailable. Try the barcode again shortly.")


@app.get("/api/products/{barcode}/image")
def product_label_image(barcode: str):
    with connect() as db:
        row = db.execute("SELECT image_url FROM products WHERE barcode=?", (barcode,)).fetchone()
    if not row or not row["image_url"]:
        raise HTTPException(404, "No product label image is available for this barcode.")
    image_url = row["image_url"]
    parsed = urlparse(image_url)
    if parsed.scheme != "https" or parsed.hostname != "images.openfoodfacts.org":
        raise HTTPException(400, "The product image source is not supported.")
    try:
        image = requests.get(image_url, timeout=15, headers={"User-Agent":"FoodLens/1.0 (label reader)"})
        image.raise_for_status()
    except requests.RequestException:
        raise HTTPException(503, "The product image could not be loaded right now.")
    media_type = image.headers.get("content-type", "image/jpeg").split(";")[0]
    if not media_type.startswith("image/"):
        raise HTTPException(502, "The product source did not return an image.")
    return Response(content=image.content, media_type=media_type, headers={"Cache-Control":"public, max-age=3600"})


@app.put("/api/products/{barcode}/label")
def save_product_label(barcode: str, label: ProductLabelUpdate):
    with connect() as db:
        updated = db.execute("UPDATE products SET ingredients=?, calories=?, sugar=?, sodium=?, saturated_fat=?, protein=?, fiber=? WHERE barcode=?", (label.ingredients, label.calories, label.sugar, label.sodium, label.saturated_fat, label.protein, label.fiber, barcode)).rowcount
        if not updated:
            raise HTTPException(404, "Scan the barcode before saving its label details.")
    return {"status":"saved", "barcode":barcode}


@app.post("/api/analyze")
def analyze_product(data: AnalyzeRequest): return analyze(data)


@app.get("/api/alternatives/{category}")
def alternatives(category: str, exclude: Optional[str] = None):
    with connect() as db:
        rows = db.execute("SELECT * FROM products WHERE category LIKE ? AND barcode != COALESCE(?, '') ORDER BY (fiber - sugar/2.0 + protein/2.0 - sodium/1000.0) DESC LIMIT 4", (f"%{category}%", exclude)).fetchall()
    if not rows:
        with connect() as db: rows = db.execute("SELECT * FROM products WHERE barcode != COALESCE(?, '') ORDER BY (fiber - sugar/2.0 + protein/2.0 - sodium/1000.0) DESC LIMIT 4", (exclude,)).fetchall()
    return [product_dict(r) for r in rows]


FRONTEND = ROOT.parent / "Frontend"
if FRONTEND.exists(): app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
