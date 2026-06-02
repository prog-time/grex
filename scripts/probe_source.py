#!/usr/bin/env python3
"""Feasibility probe for a candidate marketplace — run BEFORE writing a crawler.

Given a product URL, it reports how (and whether) the page can be parsed, so you
know the crawler's difficulty and strategy up front:

  * HTTP reachability from the local IP and (optionally) via the proxy pool
  * anti-bot / CAPTCHA detection
  * structured data already present in the raw HTML:
        - JSON-LD  <script type="application/ld+json"> with @type Product
        - OpenGraph meta (og:title / og:price …)
        - SSR state (__NUXT__, __NEXT_DATA__, __INITIAL_STATE__)
  * whether title/price are in the raw HTML (HTTP-parseable) or need JS
  * (with --browser) the internal JSON API endpoints the page calls — often the
    fastest, richest way to parse (as with mm.ru → api.kazanexpress.ru)

It ends with a VERDICT (API / HTTP / BROWSER / BLOCKED) and a recommendation.

Usage:
    python scripts/probe_source.py <product_url>            # fast HTTP-only probe
    python scripts/probe_source.py <product_url> --proxy    # also try via proxy pool
    python scripts/probe_source.py <product_url> --browser  # also render + sniff APIs
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

# Allow running from anywhere. config.py derives data_path from
# os.path.dirname(sys.argv[0]); point it at the project root (as the app does)
# so `data/settings.yml` resolves regardless of the current directory.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.argv[0] = str(_ROOT / "api.py")

from bs4 import BeautifulSoup, Tag  # noqa: E402
from curl_cffi.requests.session import AsyncSession  # noqa: E402

from crawlers.base import BaseCrawler  # noqa: E402

ANTIBOT_MARKERS = [
    "доступ ограничен", "сопоставьте пазл", "двигая ползунок", "captcha",
    "не робот", "подтвердите, что вы", "attention required", "cloudflare",
    "ddos-guard", "qrator", "проверка безопасности", "just a moment",
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def _find_jsonld_product(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for sc in soup.find_all("script", {"type": "application/ld+json"}):
        if not isinstance(sc, Tag):
            continue
        try:
            data = json.loads(sc.get_text(strip=True) or "")
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                t = node.get("@type")
                if t == "Product" or (isinstance(t, list) and "Product" in t):
                    return node
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return None


def _scan_html(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    og = {}
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop.startswith("og:") or "price" in prop:
            content = meta.get("content")
            if content:
                og[prop] = content

    h1 = soup.find("h1")
    h1_text = h1.get_text(strip=True) if h1 else ""
    title_el = soup.find("title")
    title_text = title_el.get_text(strip=True) if title_el else ""

    # Detect anti-bot from VISIBLE text only (<title> + <h1>). Matching the whole
    # HTML body gives false positives — words like "captcha" live in JS bundles
    # (recaptcha widgets, analytics) on perfectly parseable pages.
    visible = f"{title_text} {h1_text}".lower()
    antibot = [m for m in ANTIBOT_MARKERS if m in visible]

    return {
        "jsonld_product": _find_jsonld_product(html),
        "og": og,
        "h1": h1_text,
        "title": title_text,
        "has_price_in_html": bool(re.search(r"\d[\d\s.,]*\s*(₽|руб)", html)),
        "ssr": {
            "__NUXT__": "__NUXT__" in html,
            "__NEXT_DATA__": "__NEXT_DATA__" in html,
            "__INITIAL_STATE__": "INITIAL_STATE" in html,
        },
        "antibot": antibot,
    }


async def _http_fetch(url: str, proxy: str | None) -> dict:
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "ru-RU,ru;q=0.9",
        "user-agent": UA,
    }
    s = AsyncSession(impersonate="chrome136")
    try:
        r = await s.get(url, headers=headers, proxy=proxy, timeout=30)
        return {"ok": True, "status": r.status_code, "len": len(r.text), "html": r.text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def _browser_probe(url: str, proxy: dict | None) -> dict:
    """Render the page, list JSON API endpoints it calls, and read the DOM title."""
    from patchright.sync_api import sync_playwright

    hits: list[tuple[int, str]] = []
    title = None
    try:
        with sync_playwright() as p:
            kw = {"user_data_dir": "", "headless": True}
            if proxy:
                kw["proxy"] = proxy
            ctx = p.chromium.launch_persistent_context(**kw)
            page = ctx.new_page()

            def on_resp(r):
                ct = r.headers.get("content-type", "")
                if "json" in ct and not any(
                    x in r.url for x in ("mc.yandex", "sentry", "google", "firebase", "mindbox")
                ):
                    hits.append((r.status, r.url))

            page.on("response", on_resp)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("h1", timeout=15000)
            except Exception:
                pass
            for y in range(0, 5000, 1000):
                page.evaluate(f"() => window.scrollTo(0,{y})")
                page.wait_for_timeout(300)
            title = page.evaluate("() => { const h=document.querySelector('h1'); return h?h.textContent.trim():null; }")
            ctx.close()
        # dedup, keep order
        seen, uniq = set(), []
        for st, u in hits:
            base = u.split("?")[0]
            if base not in seen:
                seen.add(base)
                uniq.append((st, u))
        return {"ok": True, "title": title, "endpoints": uniq}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}


def _verdict(local: dict, scan: dict | None, browser: dict | None) -> tuple[str, str]:
    if scan is None:
        if browser and browser.get("ok") and browser.get("endpoints"):
            return "BROWSER/API", "HTTP failed but the page renders; check intercepted API endpoints below."
        return "BLOCKED", "Could not fetch the page over HTTP from this IP — try --proxy / --browser."

    # Internal PRODUCT JSON API discovered while rendering — the best target.
    # Require a product-looking endpoint (or graphql) and exclude obvious noise
    # (cart, wishlist, search, autocomplete, recommendations, etc.) so a generic
    # cart/search API doesn't masquerade as a product data source.
    _noise = (
        "autocomplete", "search", "wishlist", "cart", "health", "popup", "promo",
        "categor", "cities", "builds", "last-viewed", "similar", "recommend",
        "review", "favorite", "favourite", "banner",
    )
    if browser and browser.get("ok") and browser.get("endpoints"):
        api_like = [
            u for _, u in browser["endpoints"]
            if ("product" in u.lower() or "graphql" in u.lower())
            and not any(n in u.lower() for n in _noise)
        ]
        if api_like:
            return "API", (
                "An internal product JSON API is available — fastest & richest. "
                "Build an API-first crawler (like mm.ru → kazanexpress)."
            )

    # Structured data already in the raw HTML → HTTP parse (wins over anti-bot
    # substrings, since real data means the page wasn't actually blocked).
    if scan["jsonld_product"]:
        return "HTTP", "Raw HTML contains JSON-LD Product — parse it directly over HTTP (like bigam)."
    if scan["og"].get("og:title") and scan["has_price_in_html"]:
        return "HTTP", "OpenGraph title + price present in raw HTML — HTTP parse is feasible."
    if any(scan["ssr"].values()):
        return "HTTP", "SSR state (Nuxt/Next/InitialState) embedded — parse the JSON blob over HTTP."
    if scan["h1"] and scan["has_price_in_html"]:
        return "HTTP", "Title + price found in raw HTML — HTTP parse likely works."

    # No usable data in the raw HTML — now anti-bot / render verdicts apply.
    if scan["antibot"]:
        return "BLOCKED", (
            f"Anti-bot challenge ({', '.join(scan['antibot'])}) and no data in raw HTML — "
            "needs a residential/RU proxy and/or browser; may require CAPTCHA handling."
        )
    if browser and browser.get("ok") and browser.get("title"):
        return "BROWSER", "Data appears only after JS rendering — a browser-based crawler is needed."
    return "BROWSER?", "No structured data in raw HTML — run with --browser to confirm rendering/API."


async def main() -> None:
    ap = argparse.ArgumentParser(description="Probe whether a product URL can be parsed.")
    ap.add_argument("url")
    ap.add_argument("--proxy", action="store_true", help="also fetch via the proxy pool")
    ap.add_argument("--browser", action="store_true", help="render in a browser & sniff JSON APIs")
    args = ap.parse_args()

    print(f"\n  Probing: {args.url}\n  {'─' * 60}")

    # 1) HTTP from local IP
    local = await _http_fetch(args.url, None)
    if local["ok"]:
        print(f"  HTTP (local IP):  {local['status']}  ({local['len']} bytes)")
    else:
        print(f"  HTTP (local IP):  ERROR — {local['error']}")

    # 2) HTTP via proxy
    proxy_str = None
    if args.proxy:
        proxy_str = await BaseCrawler.get_proxy("probe")
        if proxy_str:
            r = await _http_fetch(args.url, proxy_str)
            masked = re.sub(r"(://[^:]+):([^@]+)@", r"\1:***@", proxy_str)
            if r["ok"]:
                print(f"  HTTP (proxy):     {r['status']}  ({r['len']} bytes)  via {masked.split('@')[-1]}")
                if not local["ok"] or local.get("status") != 200:
                    local = r  # prefer the proxied response for analysis
            else:
                print(f"  HTTP (proxy):     ERROR — {r['error']}")
        else:
            print("  HTTP (proxy):     skipped (proxy disabled or pool empty)")

    # 3) Analyse the best HTML we have
    scan = _scan_html(local["html"]) if local.get("ok") and local.get("html") else None
    if scan:
        print(f"\n  Structured data in raw HTML:")
        print(f"    JSON-LD Product:  {'YES' if scan['jsonld_product'] else 'no'}"
              + (f"  (name: {scan['jsonld_product'].get('name','')[:50]!r})" if scan['jsonld_product'] else ""))
        print(f"    OpenGraph:        {', '.join(scan['og'].keys()) or 'none'}")
        print(f"    SSR state:        {', '.join(k for k,v in scan['ssr'].items() if v) or 'none'}")
        print(f"    <h1>:             {scan['h1'][:60]!r}" if scan['h1'] else "    <h1>:             none")
        print(f"    price in HTML:    {'YES' if scan['has_price_in_html'] else 'no'}")
        if scan["antibot"]:
            print(f"    ⚠ anti-bot:       {', '.join(scan['antibot'])}")

    # 4) Browser render + API sniff
    browser = None
    if args.browser:
        print(f"\n  Browser render (sniffing JSON APIs)…")
        proxy_dict = BaseCrawler._playwright_proxy(proxy_str) if proxy_str else None
        browser = await asyncio.get_event_loop().run_in_executor(
            None, _browser_probe, args.url, proxy_dict
        )
        if browser["ok"]:
            print(f"    rendered <h1>:    {(browser['title'] or '')[:60]!r}")
            print(f"    JSON endpoints called ({len(browser['endpoints'])}):")
            for st, u in browser["endpoints"][:15]:
                print(f"      {st}  {u[:110]}")
        else:
            print(f"    browser ERROR — {browser['error']}")

    # 5) Verdict
    verdict, advice = _verdict(local, scan, browser)
    print(f"\n  {'─' * 60}\n  VERDICT: {verdict}\n  {advice}\n")


if __name__ == "__main__":
    asyncio.run(main())
