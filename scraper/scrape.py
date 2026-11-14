"""FoodMeta UAE scraper.

Collects real restaurant + menu data from talabat.com (UAE) and writes it to
data/meals.json in the exact shape the Android app expects (MealDto).

Talabat pages are Next.js: each page embeds its data as JSON in
<script id="__NEXT_DATA__">. We read that instead of rendering the page.

Keeta has no public web menu in the UAE (it is app-only), so it is not scraped.

Run:  python scraper/scrape.py            (writes data/meals.json + data/meta.json)
Scheduled hourly by .github/workflows/scrape.yml.
Env:  AREAS="dubai-marina,jumeirah-lake-towers"  (area slugs from /uae/all-areas)
      MAX_RESTAURANTS_PER_AREA=12  MAX_ITEMS_PER_RESTAURANT=15  DEBUG=1
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://www.talabat.com"
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data"

DEFAULT_AREAS = [
    "dubai-marina",
    "jumeirah-lake-towers",
    "downtown-dubai",
    "business-bay",
    "al-barsha-1",
    "jumeirah-village-circle",
    "al-nahda",
    "bur-dubai",
    "al-reem-island",          # Abu Dhabi
    "al-majaz",                # Sharjah
]
AREAS = [a.strip() for a in os.getenv("AREAS", ",".join(DEFAULT_AREAS)).split(",") if a.strip()]
MAX_RESTAURANTS = int(os.getenv("MAX_RESTAURANTS_PER_AREA", "10"))
MAX_ITEMS = int(os.getenv("MAX_ITEMS_PER_RESTAURANT", "15"))
DEBUG = os.getenv("DEBUG") == "1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)
AREA_LINK_RE = re.compile(r'/uae/restaurants/(\d+)/([a-z0-9\-]+)')
MENU_LINK_RE = re.compile(r'/uae/restaurant/(\d+)/([a-z0-9\-]+)')

session = requests.Session()
session.headers.update(HEADERS)
stats = {"requests": 0, "errors": []}


def log(*a):
    print(*a, flush=True)


def get(url: str) -> str | None:
    time.sleep(random.uniform(0.8, 1.8))  # be polite
    stats["requests"] += 1
    try:
        r = session.get(url, timeout=30)
    except requests.RequestException as e:
        stats["errors"].append(f"{url}: {e}")
        log(f"  ! {url} -> {e}")
        return None
    if r.status_code != 200:
        stats["errors"].append(f"{url}: HTTP {r.status_code}")
        log(f"  ! {url} -> HTTP {r.status_code} ({len(r.text)} bytes)")
        if DEBUG:
            log("    body:", r.text[:300].replace("\n", " "))
        return None
    return r.text


def next_data(html: str) -> dict | None:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def walk(obj):
    """Yield every dict nested anywhere inside obj."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def first(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


def to_float(v) -> float | None:
    try:
        f = float(str(v).replace("AED", "").replace(",", "").strip())
        return f
    except (TypeError, ValueError):
        return None


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def cuisine_of(d: dict) -> str:
    c = first(d, "cuisineString", "cuisinesString", "cuisine", "cuisines", "cuisineNames")
    if isinstance(c, list):
        names = [x.get("name") if isinstance(x, dict) else str(x) for x in c]
        c = ", ".join(n for n in names if n)
    if not isinstance(c, str):
        return ""
    return c.split(",")[0].strip()


def describe(obj, depth=0, max_depth=3) -> str:
    """Short structural summary of JSON for debugging."""
    pad = "  " * depth
    if isinstance(obj, dict):
        if depth >= max_depth:
            return f"{pad}{{{', '.join(list(obj)[:15])}}}"
        lines = []
        for k, v in list(obj.items())[:25]:
            kind = type(v).__name__ + (f"[{len(v)}]" if isinstance(v, (list, dict)) else "")
            lines.append(f"{pad}{k}: {kind}")
            if isinstance(v, (dict, list)) and v:
                lines.append(describe(v if isinstance(v, dict) else v[0], depth + 1, max_depth))
        return "\n".join(lines)
    return f"{pad}{type(obj).__name__}"


# ---------------------------------------------------------------- areas ---

def resolve_areas() -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for page in ("/uae/all-areas/restaurants", "/uae/all-areas"):
        html = get(BASE + page)
        if html:
            for aid, slug in AREA_LINK_RE.findall(html):
                found.setdefault(slug, aid)
        if len(found) > 50:
            break
    log(f"all-areas: {len(found)} area links found")
    out = []
    for want in AREAS:
        if want in found:
            out.append((found[want], want))
            continue
        # loose match, e.g. "jumeirah-lake-towers" vs "jumeirah-lake-towers-jlt"
        match = (next((s for s in found if s.startswith(want)), None)
                 or next((s for s in found if want in s), None))
        if match:
            out.append((found[match], match))
        else:
            log(f"  area not found: {want}")
    if not out and found:
        out = [(aid, s) for s, aid in list(found.items())[:5]]
    return out


# ---------------------------------------------------------- restaurants ---

def restaurants_in_area(aid: str, slug: str) -> list[dict]:
    html = get(f"{BASE}/uae/restaurants/{aid}/{slug}")
    if not html:
        return []
    links = []
    for bid, rslug in MENU_LINK_RE.findall(html):
        if (bid, rslug) not in links:
            links.append((bid, rslug))

    info: dict[str, dict] = {}
    nd = next_data(html)
    if nd:
        if DEBUG:
            log(f"--- listing pageProps for {slug} ---")
            log(describe(nd.get("props", {}).get("pageProps", {})))
        for d in walk(nd):
            name = d.get("name")
            bid = first(d, "branchId", "id")
            if isinstance(name, str) and bid and any(
                k in d for k in ("rate", "rating", "avgRating", "deliveryFee", "deliveryCharges")
            ):
                info[str(bid)] = d
                rslug = first(d, "branchSlug", "slug", "uname")
                if isinstance(rslug, str) and (str(bid), rslug) not in links:
                    links.append((str(bid), rslug))
    log(f"area {slug} ({aid}): {len(links)} restaurant links, {len(info)} JSON records")

    out = []
    for bid, rslug in links[:MAX_RESTAURANTS]:
        d = info.get(bid, {})
        out.append({
            "branchId": bid,
            "slug": rslug,
            "aid": aid,
            "name": d.get("name"),
            "rating": to_float(first(d, "rate", "rating", "avgRating")),
            "deliveryFee": to_float(first(d, "deliveryFee", "deliveryCharges", "deliveryCost")),
            "cuisine": cuisine_of(d),
        })
    return out


# ----------------------------------------------------------------- menu ---

def menu_for(r: dict, debug_once: list) -> list[dict]:
    url = f"{BASE}/uae/restaurant/{r['branchId']}/{r['slug']}?aid={r['aid']}"
    html = get(url)
    if not html:
        return []
    nd = next_data(html)
    if not nd:
        log(f"  no __NEXT_DATA__ on {url}")
        return []
    pp = nd.get("props", {}).get("pageProps", {})
    if DEBUG and not debug_once:
        debug_once.append(1)
        log(f"--- menu pageProps for {r['slug']} ---")
        log(describe(pp))

    items = (
        pp.get("initialMenuState", {}).get("menuData", {}).get("items")
        or []
    )
    if not items:  # structure changed: look for any list of dicts with name+price
        for d in walk(pp):
            for v in d.values():
                if (isinstance(v, list) and v and isinstance(v[0], dict)
                        and "name" in v[0] and "price" in v[0]):
                    items = v
                    break
            if items:
                break

    # Restaurant details from the menu page (more reliable than the listing).
    rest = pp.get("initialMenuState", {}).get("restaurant") or {}
    if not rest:
        for d in walk(pp):
            if isinstance(d.get("name"), str) and any(k in d for k in ("rate", "rating", "avgRating")):
                rest = d
                break
    name = r["name"] or rest.get("name") or r["slug"].replace("-", " ").title()
    rating = r["rating"] if r["rating"] is not None else to_float(first(rest, "rate", "rating", "avgRating"))
    fee = r["deliveryFee"] if r["deliveryFee"] is not None else to_float(
        first(rest, "deliveryFee", "deliveryCharges", "deliveryCost"))
    cuisine = r["cuisine"] or cuisine_of(rest)

    meals = []
    for it in items:
        price = to_float(it.get("price"))
        iname = it.get("name")
        if not iname or not price or price <= 0:
            continue
        img = first(it, "originalImage", "image", "imageUrl", default="") or ""
        if img and not img.startswith("http"):
            img = "https://images.deliveryhero.io/image/talabat/MenuItems/" + img.lstrip("/")
        meals.append({
            "id": f"talabat-{r['branchId']}-{it.get('id')}",
            "name": iname.strip(),
            "restaurant": name.strip(),
            "provider": "TALABAT",
            "priceAed": round(price, 2),
            "deliveryFeeAed": fee,
            "rating": rating,
            "cuisine": cuisine or (it.get("originalSection") or ""),
            "imageUrl": img,
            "deepLink": url,
            "canonicalId": f"{norm(name.split(',')[0])}::{norm(iname)}",
            "area": r["aid"],
        })
        if len(meals) >= MAX_ITEMS:
            break
    return meals


def main() -> int:
    started = datetime.now(timezone.utc)
    areas = resolve_areas()
    log(f"areas: {areas}")
    seen_branches: set[str] = set()
    all_meals: list[dict] = []
    debug_once: list = []
    for aid, slug in areas:
        for r in restaurants_in_area(aid, slug):
            if r["branchId"] in seen_branches:
                continue
            seen_branches.add(r["branchId"])
            meals = menu_for(r, debug_once)
            log(f"  {r['slug']}: {len(meals)} items")
            all_meals.extend(meals)

    log(f"TOTAL: {len(all_meals)} meals from {len(seen_branches)} restaurants, "
        f"{stats['requests']} requests, {len(stats['errors'])} errors")

    OUT_DIR.mkdir(exist_ok=True)
    meta = {
        "updatedAt": started.isoformat(timespec="seconds"),
        "meals": len(all_meals),
        "restaurants": len(seen_branches),
        "areas": [s for _, s in areas],
        "providers": {"TALABAT": len(all_meals), "KEETA": 0},
        "errors": stats["errors"][:20],
    }
    if not all_meals:
        log("No meals scraped — keeping the previous data/meals.json untouched.")
        (OUT_DIR / "last-failed-run.json").write_text(json.dumps(meta, indent=2))
        return 1
    all_meals.sort(key=lambda m: m["id"])
    (OUT_DIR / "meals.json").write_text(json.dumps(all_meals, ensure_ascii=False, indent=1))
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
