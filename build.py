# ::ILANG [TYPE:source-code][ROLE:deterministic static renderer]
# ::BOUNDARY{never:invent prices expiry dates offer availability or sources}
# ::RULE{all provider and brand settings:.ilang/site.ilang}
# ::RULE{SEO/schema fields:observed data only}
"""Build a dependency-free static directory from I-Lang configuration and verified data."""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from urllib.parse import quote

from scraper import parse_site_config

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
OUTPUT = ROOT / "site"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.casefold())).strip("-") or "offer"


def json_ld(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def read_data() -> dict:
    path = ROOT / "data" / "offers.json"
    if not path.exists():
        return {"last_checked_at": None, "providers": [], "offers": []}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document.get("offers", []), list):
        raise ValueError("data/offers.json offers must be a list")
    return document


def page(template_name: str, variables: dict) -> str:
    template = Template((TEMPLATES / template_name).read_text(encoding="utf-8"))
    return template.substitute(variables)


def offer_card(offer: dict, url: str) -> str:
    price = f"{esc(offer['currency'])} {esc(offer['price'])}"
    expiry = f" · Valid through {esc(offer['valid_until'])}" if offer.get("valid_until") else ""
    description = f"<p>{esc(offer['description'])}</p>" if offer.get("description") else ""
    return (f'<article class="offer-card"><p class="eyebrow">{esc(offer["provider"])}</p>'
            f'<h3><a href="{esc(url)}">{esc(offer["title"])}</a></h3>'
            f'<p class="price">{price}</p>{description}<p class="source">Source: '
            f'<a href="{esc(offer["source_url"])}" rel="nofollow noopener">official offer page</a>{expiry}</p></article>')


def current_status(offer: dict) -> str:
    until = offer.get("valid_until")
    if not until:
        return "No expiry date was provided in the source."
    try:
        expiry = datetime.fromisoformat(until[:10]).date()
    except ValueError:
        return "Expiry date not published."
    if expiry < datetime.now(timezone.utc).date():
        return "Expired at the source; not published."
    return f"Source lists validity through {until}."


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render() -> dict:
    config = parse_site_config()
    site = config["site"]
    providers = config["providers"]
    data = read_data()
    offers = data.get("offers", [])
    provider_status = {row.get("name"): row for row in data.get("providers", [])}
    base_url = site.get("base_url", "").rstrip("/")
    if base_url and not base_url.startswith("https://"):
        raise ValueError("@SITE base_url must be an observed HTTPS URL or empty")
    if base_url and ".pages.dev" not in base_url and not site.get("domain"):
        raise ValueError("Set domain when base_url is a custom domain")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for old in OUTPUT.iterdir():
        if old.is_dir():
            shutil.rmtree(old)
        else:
            old.unlink()
    shutil.copyfile(TEMPLATES / "style.css", OUTPUT / "style.css")

    provider_paths = {}
    for provider in providers:
        provider_slug = slug(provider["name"])
        path = f"providers/{provider_slug}/index.html"
        provider_paths[provider["name"]] = path
        page_offers = [item for item in offers if item.get("provider") == provider["name"]]
        cards = "\n".join(offer_card(item, f"/deals/{slug(item['provider'] + '-' + item['title'])}/") for item in page_offers)
        if not cards:
            status = provider_status.get(provider["name"], {})
            note = status.get("status", "No source scan has completed yet.")
            cards = f'<div class="empty"><h3>No verified priced offers yet</h3><p>{esc(note)} We publish only source offers with explicit machine-readable prices.</p></div>'
        canonical = f'<link rel="canonical" href="{esc(base_url + "/" + path)}">' if base_url else ""
        schema = {"@context": "https://schema.org", "@type": "Service", "name": f"{provider['name']} travel offers", "url": base_url + "/" + path if base_url else provider["url"]}
        if page_offers:
            schema["offers"] = [schema_offer(item) for item in page_offers]
        schema_script = f'<script type="application/ld+json">{json_ld(schema)}</script>'
        content = page("provider.html", {
            "site_name": esc(site.get("brand", "")), "provider": esc(provider["name"]),
            "provider_url": esc(provider["url"]), "source_url": esc(provider["source_url"]),
            "cards": cards, "canonical": canonical, "schema": schema_script,
        })
        write(OUTPUT / path, content)

    offer_rows = []
    sitemap_paths = ["/", "/compare/"]
    for provider in providers:
        sitemap_paths.append("/" + provider_paths[provider["name"]].removesuffix("index.html"))
    for index, offer in enumerate(offers, start=1):
        offer_slug = slug(offer["provider"] + "-" + offer["title"])
        path = f"deals/{offer_slug}/index.html"
        url_path = f"/deals/{offer_slug}/"
        canonical = f'<link rel="canonical" href="{esc(base_url + url_path)}">' if base_url else ""
        schema = schema_offer(offer)
        schema_script = f'<script type="application/ld+json">{json_ld(schema)}</script>'
        content = page("deal.html", {
            "site_name": esc(site.get("brand", "")), "title": esc(offer["title"]),
            "provider": esc(offer["provider"]), "price": esc(offer["price"]),
            "currency": esc(offer["currency"]), "url": esc(offer["offer_url"]),
            "source_url": esc(offer["source_url"]), "description": esc(offer.get("description", "")),
            "validity": esc(current_status(offer)), "availability": esc(offer.get("availability", "")),
            "canonical": canonical, "schema": schema_script,
        })
        write(OUTPUT / path, content)
        offer_rows.append({"@type": "ListItem", "position": index, "url": base_url + url_path if base_url else url_path, "name": offer["title"]})
        sitemap_paths.append(url_path)

    list_schema = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": offer_rows}
    scan_time = data.get("last_checked_at") or "No successful scan recorded yet"
    cards = "\n".join(offer_card(item, f"/deals/{slug(item['provider'] + '-' + item['title'])}/") for item in offers)
    if not cards:
        cards = '<div class="empty"><h3>No verified offers yet</h3><p>The source scan has not found a priced offer with the required official structured data. No deal or price is being guessed.</p></div>'
    canonical = f'<link rel="canonical" href="{esc(base_url + "/")}">' if base_url else ""
    content = page("index.html", {
        "site_name": esc(site.get("brand", "")), "niche": esc(site.get("niche", "")),
        "scan_time": esc(scan_time), "offer_count": str(len(offers)),
        "provider_count": str(len(providers)), "cards": cards, "canonical": canonical,
        "schema": f'<script type="application/ld+json">{json_ld(list_schema)}</script>',
    })
    write(OUTPUT / "index.html", content)

    compare_items = []
    compare_schema_items = []
    for index, provider in enumerate(providers, start=1):
        path = "/" + provider_paths[provider["name"]].removesuffix("index.html")
        amount = sum(1 for item in offers if item.get("provider") == provider["name"])
        compare_items.append(f'<tr><th scope="row"><a href="{esc(path)}">{esc(provider["name"])}</a></th><td>{amount}</td><td><a href="{esc(provider["url"])}" rel="nofollow noopener">Official site</a></td></tr>')
        compare_schema_items.append({"@type": "ListItem", "position": index, "url": base_url + path if base_url else path, "name": provider["name"]})
    compare_schema = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": compare_schema_items}
    compare_canonical = f'<link rel="canonical" href="{esc(base_url + "/compare/")}">' if base_url else ""
    compare_content = page("compare.html", {
        "site_name": esc(site.get("brand", "")), "rows": "\n".join(compare_items),
        "canonical": compare_canonical,
        "schema": f'<script type="application/ld+json">{json_ld(compare_schema)}</script>',
    })
    write(OUTPUT / "compare" / "index.html", compare_content)

    sitemap_entries = []
    if base_url:
        for path in sitemap_paths:
            loc = base_url + path
            lastmod = data.get("last_checked_at")
            mod = f"<lastmod>{esc(lastmod)}</lastmod>" if lastmod else ""
            sitemap_entries.append(f"<url><loc>{esc(loc)}</loc>{mod}</url>")
    write(OUTPUT / "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(sitemap_entries) + '\n</urlset>\n')
    sitemap_line = f"Sitemap: {base_url}/sitemap.xml\n" if base_url else ""
    write(OUTPUT / "robots.txt", "User-agent: *\nAllow: /\n" + sitemap_line)
    return {"providers": len(providers), "offers": len(offers), "sitemap_urls": len(sitemap_entries), "base_url_configured": bool(base_url)}


def schema_offer(offer: dict) -> dict:
    item = {
        "@context": "https://schema.org", "@type": "Offer",
        "name": offer["title"], "price": offer["price"], "priceCurrency": offer["currency"],
        "url": offer["offer_url"],
    }
    if offer.get("valid_until"):
        item["priceValidUntil"] = offer["valid_until"]
    availability_map = {
        "InStock": "https://schema.org/InStock", "LimitedAvailability": "https://schema.org/LimitedAvailability",
        "OutOfStock": "https://schema.org/OutOfStock", "SoldOut": "https://schema.org/SoldOut",
        "PreOrder": "https://schema.org/PreOrder", "Discontinued": "https://schema.org/Discontinued",
    }
    if offer.get("availability") in availability_map:
        item["availability"] = availability_map[offer["availability"]]
    return item


def main() -> int:
    try:
        summary = render()
        print("Built static site:", json.dumps(summary, ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"Build failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
