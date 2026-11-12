# ::ILANG [TYPE:source-code][ROLE:robots-aware official-source scraper]
# ::BOUNDARY{never:guess fields bypass robots or fetch unconfigured hosts}
# ::RULE{configuration_source:.ilang/site.ilang}
# ::RULE{publish_only:explicit named JSON-LD Offer with price and currency}
"""Read approved public provider pages and collect only explicit JSON-LD offers."""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parent
USER_AGENT = "LayoverLedgerBot/0.1"
TIMEOUT_SECONDS = 20
MAX_PAGE_BYTES = 5_000_000


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


class StructuredDataParser(HTMLParser):
    """Collect JSON-LD script bodies without interpreting surrounding page text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_jsonld = False
        self._parts: list[str] = []
        self.documents: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        values = {key.lower(): value for key, value in attrs}
        if (values.get("type") or "").split(";")[0].strip().lower() == "application/ld+json":
            self._in_jsonld = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            self.documents.append("".join(self._parts))
            self._in_jsonld = False
            self._parts = []


def fetch(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/ld+json;q=0.9,*/*;q=0.1"})
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
            # Per RFC 9309, 4xx means the robots file is unavailable; fail closed on
            # authorization, rate limiting, or server errors to avoid bypassing policy.
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


def same_provider_host(url: str, provider_url: str) -> bool:
    target = (urlparse(url).hostname or "").lower().rstrip(".")
    provider = (urlparse(provider_url).hostname or "").lower().rstrip(".")
    return bool(provider and (target == provider or target.endswith("." + provider)))


def schema_types(obj: dict) -> set[str]:
    value = obj.get("@type", [])
    if isinstance(value, str):
        value = [value]
    return {str(item).rsplit("/", 1)[-1].rsplit("#", 1)[-1] for item in value if item}


def walk_offers(obj, provider: dict, source_url: str, fetched_at: str, parent_name: str = "") -> list[dict]:
    found: list[dict] = []
    if isinstance(obj, list):
        for item in obj:
            found.extend(walk_offers(item, provider, source_url, fetched_at, parent_name))
        return found
    if not isinstance(obj, dict):
        return found

    types = schema_types(obj)
    current_name = obj.get("name") if isinstance(obj.get("name"), str) else parent_name
    if "Offer" in types and "AggregateOffer" not in types:
        raw_name = obj.get("name") if isinstance(obj.get("name"), str) else current_name
        raw_price = obj.get("price")
        raw_currency = obj.get("priceCurrency")
        raw_url = urljoin(source_url, obj.get("url")) if isinstance(obj.get("url"), str) and obj.get("url") else source_url
        if isinstance(raw_price, dict):
            raw_price = raw_price.get("value")
        price_text = str(raw_price).strip() if raw_price is not None else ""
        currency = str(raw_currency).strip().upper() if raw_currency is not None else ""
        try:
            numeric_price = float(price_text)
        except (TypeError, ValueError):
            numeric_price = -1
        if (not raw_name or not price_text or not math.isfinite(numeric_price) or numeric_price < 0 or
                not re.fullmatch(r"[A-Z]{3}", currency) or
                not isinstance(raw_url, str) or not same_provider_host(raw_url, provider["url"])):
            return found

        name = str(raw_name).strip()
        if provider["name"].casefold() not in name.casefold():
            name = f"{provider['name']} — {name}"
        valid_until = obj.get("priceValidUntil") or obj.get("validThrough")
        if isinstance(valid_until, dict):
            valid_until = valid_until.get("@value")
        if isinstance(valid_until, str):
            date_part = valid_until[:10]
            try:
                expiry_date = datetime.fromisoformat(date_part).date()
            except ValueError:
                valid_until = None
            else:
                if expiry_date < datetime.now(timezone.utc).date():
                    return found
                valid_until = date_part
        else:
            valid_until = None

        availability = obj.get("availability")
        if isinstance(availability, dict):
            availability = availability.get("@id") or availability.get("name")
        availability_name = str(availability).rsplit("/", 1)[-1] if isinstance(availability, str) else ""
        if availability_name not in {"InStock", "LimitedAvailability", "OutOfStock", "SoldOut", "PreOrder", "Discontinued"}:
            availability_name = ""

        description = obj.get("description") if isinstance(obj.get("description"), str) else ""
        record = {
            "provider": provider["name"],
            "title": name,
            "price": price_text,
            "currency": currency,
            "offer_url": raw_url,
            "source_url": source_url,
            "fetched_at": fetched_at,
            "model": str(raw_name).strip(),
        }
        if valid_until:
            record["valid_until"] = valid_until
        if availability_name:
            record["availability"] = availability_name
        if description:
            record["description"] = " ".join(description.split())[:500]
        record["_dedupe_key"] = "::".join((provider["name"].casefold(), record["model"].casefold(), currency))
        found.append(record)
        return found

    for key, value in obj.items():
        if key.startswith("@"):  # @graph and @type references are still traversed by their values below.
            if key == "@graph":
                found.extend(walk_offers(value, provider, source_url, fetched_at, current_name))
            continue
        found.extend(walk_offers(value, provider, source_url, fetched_at, current_name))
    return found


def extract_offers(html: bytes, provider: dict, fetched_at: str) -> list[dict]:
    parser = StructuredDataParser()
    parser.feed(html.decode("utf-8", errors="replace"))
    results = []
    for document in parser.documents:
        try:
            parsed = json.loads(document)
        except (json.JSONDecodeError, RecursionError):
            continue
        results.extend(walk_offers(parsed, provider, provider["source_url"], fetched_at))
    return results


def scrape(config: dict) -> dict:
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    robots_cache: dict[str, tuple[RobotFileParser | None, str]] = {}
    providers_out = []
    all_offers: dict[str, dict] = {}
    for provider in config["providers"]:
        allowed, robots_note = allowed_by_robots(provider["source_url"], robots_cache)
        status = {"name": provider["name"], "source_url": provider["source_url"], "checked_at": fetched_at, "offers_found": 0}
        if not allowed:
            status["status"] = robots_note if "robots.txt" in robots_note else "Disallowed by robots.txt"
            providers_out.append(status)
            continue
        try:
            body, final_url = fetch(provider["source_url"])
            if not same_provider_host(final_url, provider["url"]):
                raise ValueError("redirect left the provider's own host; skipped")
            offers = extract_offers(body, provider, fetched_at)
            for offer in offers:
                key = offer.pop("_dedupe_key")
                all_offers[key] = offer
            status["status"] = "checked; no qualifying structured offers" if not offers else "checked"
            status["offers_found"] = len(offers)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            status["status"] = f"source unavailable or rejected ({type(error).__name__})"
        providers_out.append(status)
    return {
        "last_checked_at": fetched_at,
        "providers": providers_out,
        "offers": sorted(all_offers.values(), key=lambda item: (item["provider"].casefold(), item["title"].casefold())),
    }


def main() -> int:
    try:
        config = parse_site_config()
        result = scrape(config)
        output = ROOT / "data" / "offers.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Checked {len(config['providers'])} configured official pages; recorded {len(result['offers'])} qualifying priced offers.")
        for provider in result["providers"]:
            print(f"- {provider['name']}: {provider['status']} ({provider['offers_found']} offers)")
        return 0
    except Exception as error:  # Fail the scheduled run for config/programming errors, not for one vendor outage.
        print(f"Scrape run failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
