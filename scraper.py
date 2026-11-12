# ::ILANG [TYPE:source-code][ROLE:robots-aware official-source scraper]
# ::BOUNDARY{never:guess a price or publish a price the source page does not print}
# ::RULE{configuration_source:.ilang/site.ilang}
# ::RULE{source⇒only the configured official offer page and same-host deal pages found from it}
# ::RULE{render⇒read the page as a browser does, then keep only prices printed on it}
# ::RULE{evidence⇒every offer keeps the visible price text and the exact page that showed it}
"""Collect priced travel offers from official provider pages, with the source text kept as evidence."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

from extract import extract, normalize_title, same_host

ROOT = Path(__file__).resolve().parent
USER_AGENT = "LayoverLedgerBot/0.1 (+https://github.com/layover-ledger)"
TIMEOUT_SECONDS = 25
MAX_PAGE_BYTES = 8_000_000
RENDER_TIMEOUT_SECONDS = 90
MAX_OFFERS_PER_PROVIDER = 24
MAX_EXTRA_PAGES = 2
MAX_SITEMAP_PAGES = 3
DEAL_HINT = ("deal", "offer", "sale", "fare", "promo", "ticket", "special", "save", "discount")
CHROME_CANDIDATES = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def parse_site_config(path: Path = ROOT / ".ilang" / "site.ilang") -> dict:
    """Parse the small, documented I-Lang config shape used by this repository."""
    lines = path.read_text(encoding="utf-8").splitlines()
    state_line = next((line.strip() for line in lines if line.strip().startswith("::STATE{@SITE,")), None)
    if not state_line or not state_line.endswith("}"):
        raise ValueError(".ilang/site.ilang needs one ::STATE{@SITE,...} record")
    state = {}
    for item in state_line[len("::STATE{@SITE,"):-1].split(","):
        key, separator, value = item.partition(":")
        if not separator:
            raise ValueError(f"Invalid @SITE field: {item}")
        state[key.strip()] = value.strip()

    providers = []
    in_providers = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("::MODULE{PROVIDERS|"):
            in_providers = True
            continue
        if in_providers and stripped.startswith("::MODULE{"):
            break
        if not in_providers or not stripped or stripped.startswith("::") or stripped.startswith("["):
            continue
        fields = [part.strip() for part in stripped.split("|")]
        if len(fields) != 4 or not all(fields[:3]):
            raise ValueError(f"Provider rows need name, official URL, offer URL, affiliate URL: {line}")
        provider = {"name": fields[0], "url": fields[1], "source_url": fields[2], "affiliate_url": fields[3]}
        for key in ("url", "source_url"):
            parsed = urlparse(provider[key])
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(f"Provider {key} must be an HTTPS URL: {provider[key]}")
        providers.append(provider)
    if not providers:
        raise ValueError("No providers configured in .ilang/site.ilang")
    return {"site": state, "providers": providers}


def fetch(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.1"})
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read(MAX_PAGE_BYTES + 1)
        if len(body) > MAX_PAGE_BYTES:
            raise ValueError("Response exceeds the configured size limit")
        return body, response.geturl()


def allowed_by_robots(source_url: str, cache: dict[str, tuple[RobotFileParser | None, str]]) -> tuple[bool, str]:
    parsed = urlparse(source_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in cache:
        robots_url = origin + "/robots.txt"
        parser = RobotFileParser(robots_url)
        try:
            body, final_url = fetch(robots_url)
            parser.set_url(final_url)
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
            cache[origin] = (parser, "robots.txt retrieved")
        except HTTPError as error:
            # RFC 9309: 4xx means no robots rules published; authorization, rate limiting
            # and server errors stay closed so no policy is bypassed.
            if error.code in (401, 403, 429) or error.code >= 500:
                cache[origin] = (None, f"robots.txt returned HTTP {error.code}; skipped")
            elif 400 <= error.code < 500:
                empty = RobotFileParser(robots_url)
                empty.parse([])
                cache[origin] = (empty, f"robots.txt unavailable (HTTP {error.code}); no rules published")
            else:
                cache[origin] = (None, f"robots.txt returned HTTP {error.code}; skipped")
        except (URLError, TimeoutError, OSError, ValueError) as error:
            cache[origin] = (None, f"robots.txt could not be checked ({type(error).__name__}); skipped")
    parser, note = cache[origin]
    if parser is None:
        return False, note
    return parser.can_fetch(USER_AGENT, source_url), note


def chrome_binary() -> str | None:
    configured = os.environ.get("CHROME_BIN", "").strip()
    if configured and Path(configured).exists():
        return configured
    for candidate in CHROME_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
        if Path(candidate).exists():
            return candidate
    return None


def render(url: str) -> tuple[str, str]:
    """Return (html, how) after letting the page's own scripts run, like a visitor would."""
    binary = chrome_binary()
    if binary:
        command = [binary, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                   "--hide-scrollbars", "--window-size=1400,2400", "--virtual-time-budget=15000",
                   "--dump-dom", url]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=RENDER_TIMEOUT_SECONDS)
            html = completed.stdout.decode("utf-8", errors="replace")
            if len(html) > 2000:
                return html, "rendered"
        except (subprocess.TimeoutExpired, OSError):
            pass
    body, _ = fetch(url)
    return body.decode("utf-8", errors="replace"), "raw-html"


def deal_links(html: str, page_url: str, provider: dict, limit: int) -> list[str]:
    from extract import parse  # local import keeps module import cost low

    tree = parse(html)
    found: list[str] = []
    for node in tree.root.descendants():
        if node.tag != "a":
            continue
        href = node.attr("href").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(page_url, href).split("#")[0]
        if not same_host(absolute, provider["url"]) or absolute.rstrip("/") == page_url.rstrip("/"):
            continue
        lowered = absolute.lower()
        if not any(hint in lowered for hint in DEAL_HINT):
            continue
        if absolute not in found:
            found.append(absolute)
        if len(found) >= limit * 4:
            break
    return found[:limit]


def sitemap_candidates(provider: dict, robots_cache: dict) -> list[str]:
    """Fallback discovery: same-host deal URLs listed in the provider's own sitemap."""
    origin = f"{urlparse(provider['url']).scheme}://{urlparse(provider['url']).netloc}"
    sitemap_urls: list[str] = []
    parser, _ = robots_cache.get(origin, (None, ""))
    if parser is not None:
        sitemap_urls = [entry for entry in getattr(parser, "site_maps", lambda: [])() if entry]
    for candidate in [origin + "/sitemap.xml"] + sitemap_urls:
        try:
            body, _ = fetch(candidate)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            continue
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError:
            continue
        locations = [element.text.strip() for element in root.iter() if element.tag.endswith("loc") and element.text]
        nested = [url for url in locations if url.endswith(".xml")][:3]
        pages = [url for url in locations if not url.endswith(".xml")]
        for extra in nested:
            try:
                nested_body, _ = fetch(extra)
                nested_root = ElementTree.fromstring(nested_body)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, ElementTree.ParseError):
                continue
            pages.extend(element.text.strip() for element in nested_root.iter()
                         if element.tag.endswith("loc") and element.text and not element.text.endswith(".xml"))
        picked = [url for url in pages if same_host(url, provider["url"])
                  and any(hint in url.lower() for hint in DEAL_HINT)]
        if picked:
            return picked[:MAX_SITEMAP_PAGES]
    return []


def scrape(config: dict) -> dict:
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    robots_cache: dict[str, tuple[RobotFileParser | None, str]] = {}
    providers_out = []
    collected: dict[tuple, dict] = {}

    for provider in config["providers"]:
        allowed, robots_note = allowed_by_robots(provider["source_url"], robots_cache)
        status = {"name": provider["name"], "source_url": provider["source_url"],
                  "checked_at": fetched_at, "offers_found": 0}
        if not allowed:
            status["status"] = robots_note if "robots.txt" in robots_note else "Disallowed by robots.txt"
            providers_out.append(status)
            continue

        pages: list[tuple[str, str]] = []
        try:
            html, mode = render(provider["source_url"])
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            status["status"] = f"source unavailable or rejected ({type(error).__name__})"
            providers_out.append(status)
            continue
        pages.append((provider["source_url"], html))

        offers: list[dict] = []
        seen: set[tuple] = set()
        notes: list[str] = []

        def take(page_url: str, page_html: str) -> int:
            added = 0
            for offer in extract(page_html, provider, page_url, fetched_at):
                key = (normalize_title(offer["title"]), offer["price"], offer["currency"],
                       "" if offer["offer_url"] == page_url else offer["offer_url"])
                if key in seen or len(seen) >= MAX_OFFERS_PER_PROVIDER:
                    continue
                seen.add(key)
                offers.append(offer)
                added += 1
            return added

        take(provider["source_url"], html)
        mode_note = "rendered with headless Chrome" if mode == "rendered" else "read as static HTML (no browser available)"

        extra_pages: list[str] = []
        if len(offers) < 6:
            extra_pages = deal_links(html, provider["source_url"], provider, MAX_EXTRA_PAGES)
        if not offers:
            extra_pages = sitemap_candidates(provider, robots_cache) or extra_pages

        for page_url in extra_pages:
            if len(seen) >= MAX_OFFERS_PER_PROVIDER:
                break
            permitted, _ = allowed_by_robots(page_url, robots_cache)
            if not permitted:
                notes.append("one deal page disallowed by robots.txt")
                continue
            try:
                page_html, _ = render(page_url)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                notes.append("one deal page could not be read")
                continue
            take(page_url, page_html)

        for offer in offers:
            collected[(offer["provider"].casefold(), normalize_title(offer["title"]), offer["price"],
                       offer["currency"])] = offer
        status["offers_found"] = len(offers)
        status["read_mode"] = mode_note
        if offers:
            status["status"] = f"checked; {len(offers)} priced offers read from {len(pages) + len(extra_pages)} page(s)"
        else:
            status["status"] = "checked; no price printed on the source page during this scan"
        if notes:
            status["notes"] = "; ".join(sorted(set(notes)))
        providers_out.append(status)

    return {
        "last_checked_at": fetched_at,
        "providers": providers_out,
        "offers": sorted(collected.values(), key=lambda item: (item["provider"].casefold(), item["title"].casefold())),
    }


def main() -> int:
    try:
        config = parse_site_config()
        result = scrape(config)
        output = ROOT / "data" / "offers.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Checked {len(config['providers'])} official sources; recorded {len(result['offers'])} priced offers.")
        for provider in result["providers"]:
            print(f"- {provider['name']}: {provider['status']} ({provider['offers_found']} offers)")
        return 0
    except Exception as error:  # Fail the scheduled run for config errors, not for one vendor outage.
        print(f"Scrape run failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
