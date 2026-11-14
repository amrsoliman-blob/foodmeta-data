"""One-off discovery: how do Deliveroo UAE and Noon Food expose restaurants and menus?

Prints, for each URL: status, size, page title, embedded JSON (Next.js) structure,
interesting links and API-looking URLs. Run from GitHub Actions (Probe workflow).
"""
import json
import re
import sys

import requests

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

URLS = sys.argv[1:] or [
    "https://deliveroo.ae/robots.txt",
    "https://deliveroo.ae/en/",
    "https://deliveroo.ae/en/restaurants/dubai/dubai-marina",
    "https://deliveroo.ae/en/menu/dubai/meydan/gerbou",
    "https://food.noon.com/robots.txt",
    "https://food.noon.com/uae-en/",
    "https://food.noon.com/sitemap.xml",
]

LINK_RE = re.compile(r'href="([^"]+)"')
API_RE = re.compile(r'https?://[a-zA-Z0-9.\-]+/[^"\'\s<>]*(?:api|_svc|graphql|catalog|menu)[^"\'\s<>]*', re.I)
ND_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def describe(obj, depth=0, max_depth=4):
    pad = "  " * depth
    out = []
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:30]:
            kind = type(v).__name__ + (f"[{len(v)}]" if isinstance(v, (list, dict)) else "")
            sample = ""
            if isinstance(v, (str, int, float, bool)) and v is not None:
                sample = " = " + repr(v)[:60]
            out.append(f"{pad}{k}: {kind}{sample}")
            if depth < max_depth and isinstance(v, (dict, list)) and v:
                out.append(describe(v if isinstance(v, dict) else v[0], depth + 1, max_depth))
    elif isinstance(obj, list) and obj:
        out.append(describe(obj[0], depth, max_depth))
    return "\n".join(x for x in out if x)


for url in URLS:
    print("=" * 100)
    print("URL", url)
    try:
        r = requests.get(url, headers=H, timeout=30, allow_redirects=True)
    except Exception as e:
        print("  ERROR", e)
        continue
    body = r.text
    print(f"  status={r.status_code} final={r.url} bytes={len(body)} type={r.headers.get('content-type')}")
    print("  server=", r.headers.get("server"), " cf-ray=", r.headers.get("cf-ray"))
    t = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
    print("  title:", (t.group(1).strip()[:120] if t else None))
    if url.endswith(".txt") or url.endswith(".xml") or "xml" in (r.headers.get("content-type") or ""):
        print("  --- body head ---")
        print("\n".join("  " + l for l in body[:3000].splitlines()[:60]))
        continue
    m = ND_RE.search(body)
    if m:
        try:
            nd = json.loads(m.group(1))
            print("  __NEXT_DATA__ found, page:", nd.get("page"))
            print(describe(nd.get("props", {}), 1, 5)[:8000])
        except Exception as e:
            print("  __NEXT_DATA__ parse error", e)
    else:
        print("  no __NEXT_DATA__; script ids:", re.findall(r'<script[^>]+id="([^"]+)"', body)[:15])
        # Other common embedded-state patterns
        for pat in (r"window\.__INITIAL_STATE__", r"window\.__APOLLO_STATE__", r"self\.__next_f\.push", r"__NUXT__"):
            if re.search(pat, body):
                print("  found pattern:", pat)
    links = sorted({l for l in LINK_RE.findall(body) if not l.startswith("#")})
    interesting = [l for l in links if re.search(r"menu|restaurant|outlet|area|zone|/en/|uae-en", l)]
    print(f"  links: {len(links)} total; interesting ({len(interesting)}):")
    for l in interesting[:40]:
        print("    ", l)
    apis = sorted(set(API_RE.findall(body)))
    print(f"  api-looking urls ({len(apis)}):")
    for a in apis[:25]:
        print("    ", a[:160])
    # A window of text around a price to see how prices are embedded
    pm = re.search(r".{0,200}AED.{0,200}", body)
    if pm:
        print("  price context:", re.sub(r"\s+", " ", pm.group(0))[:400])
