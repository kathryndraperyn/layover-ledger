# ::ILANG [TYPE:source-code][ROLE:evidence-only price extractor]
# ::BOUNDARY{never:guess a price from prose or infer one from a discount claim}
# ::RULE{accept only a price printed on the source page or published in machine-readable markup}
"""Extract only prices that the source page itself displays or publishes."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe"}
BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "section", "article", "header",
              "footer", "nav", "ul", "ol", "dl", "dt", "dd", "figcaption", "blockquote", "span", "a", "strong", "em"}

NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
PRICE_EXACT = re.compile(rf"^(?:US)?\$\s?({NUM})\s*\*?$")
PREFIX_WORDS = (
    "one-way starting at", "round trip starting at", "starting at", "starting from", "fares from",
    "prices from", "per person from", "from", "as low as", "only", "now", "tickets from",
    "rooms from", "one way from", "round trip from", "deal from", "cruises from",
)
PRICE_PREFIXED = re.compile(rf"^([A-Za-z][A-Za-z\s\-]{{2,28}}?)\s*:?\s*(?:US)?\$\s?({NUM})\s*\*?$")
DISCOUNT_WORDS = re.compile(
    r"\b(off|credit|save|saving|savings|bonus|under|over|value|deposit|waiver|per day|points|miles|fee|taxes?|"
    r"cash|voucher|gift card|certificate|discount of|extra)\b", re.I)
GENERIC_TITLES = {"book now", "learn more", "shop now", "see details", "view deal", "details", "more", "read more",
                  "check offer", "select", "see offer", "view offer"}
PRICE_ATTR = re.compile(r"(?:^|[|_\-\s])(?:price|amount|fare)(?:[|_\-\s]|$)", re.I)
TITLE_ATTR = re.compile(r"(?:deal|offer|fare|card)[-_]?(?:title|name|headline)|(?:^|[|_\-\s])(?:title|headline|name)(?:$|[|_\-\s])", re.I)
DEST_ATTR = re.compile(r"(?:^|[|_\-\s])(?:dest|destination|route|location|city|market)(?:$|[|_\-\s])", re.I)
ARIA_PREFIXES = ("see latest fare ", "see fare ", "see ", "shop now: ", "shop now ", "shop ", "view ", "book ",
                 "check ", "explore ", "learn about ")


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list["Node | str"] = []
        self.parent = parent

    def attr(self, name: str) -> str:
        return self.attrs.get(name, "")

    def descendants(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag not in SKIP_TAGS:
                parts.append(child.text())
        return " ".join(parts)


class TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {}, None)
        self.stack = [self.root]
        self.jsonld: list[str] = []
        self._capture: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag == "script" and (node.attr("type") or "").split(";")[0].strip().lower() == "application/ld+json":
            self._capture = []
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "script" and self._capture is not None:
            self.jsonld.append("".join(self._capture))
            self._capture = None
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        if self._capture is not None:
            self._capture.append(data)
        if data.strip():
            self.stack[-1].children.append(data)


def parse(html: str) -> TreeBuilder:
    builder = TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def price_from_text(text: str, leading: bool = False) -> tuple[str, str] | None:
    """Return (numeric price, currency) only when the source prints that price itself."""
    value = clean(text).replace("\u00a0", " ")
    if not value or len(value) > 120:
        return None
    match = PRICE_EXACT.match(value)
    if match and not DISCOUNT_WORDS.search(value):
        return match.group(1), "USD"
    match = PRICE_PREFIXED.match(value)
    if match and not DISCOUNT_WORDS.search(value):
        prefix = clean(match.group(1)).lower().rstrip(":").strip()
        if prefix in PREFIX_WORDS:
            return match.group(2), "USD"
    if leading:
        # A price container may append terms text, e.g. "$597 * Fare subject to changes".
        match = re.match(rf"^(?:US)?\$\s?({NUM})\s*(?:\*|\bfrom\b|\bper\b|\bUSD\b|\bpp\b)?", value)
        if match:
            head = value[: match.end()]
            tail = value[match.end():].strip()
            if not DISCOUNT_WORDS.search(head) and "$" not in tail:
                return match.group(1), "USD"
    return None


def blocked_region(node: Node) -> bool:
    """Skip chrome and elements the page itself hides from rendering."""
    current = node
    depth = 0
    while current is not None and depth < 5:
        if current.tag in {"nav", "footer"}:
            return True
        if current.attr("role").lower() in {"navigation", "banner", "contentinfo"}:
            return True
        if current.attr("hidden"):
            return True
        if re.search(r"display\s*:\s*none", current.attr("style"), re.I):
            return True
        current = current.parent
        depth += 1
    return False


def is_price_node(node: Node) -> bool:
    markers = " ".join(filter(None, (node.attr("class"), node.attr("id"), node.attr("data-testid"),
                                     node.attr("data-test"), node.attr("data-qa"), node.attr("data-cy"),
                                     node.attr("itemprop"), node.attr("aria-label"))))
    return bool(PRICE_ATTR.search(markers)) or node.attr("itemprop").lower() == "price"


CARD_CLASS = re.compile(r"(?:^|[-_\s])(?:card|slide|tile|result|offer|deal|fare|item)(?:$|[-_\s])", re.I)
JUNK_TITLES = GENERIC_TITLES | {"skip to main content", "skip to content", "menu", "search", "sign in",
                                "log in", "home", "back", "close", "previous", "next"}
PRICE_TOKEN = re.compile(rf"(?:US)?\$\s?{NUM}")
JUNK_LABEL = re.compile(r"^(?:more information|learn more|click|opens? |read |view more|see more|show |hide |close |dismiss|skip )", re.I)


def distinct_prices(text: str) -> set[str]:
    return set(PRICE_TOKEN.findall(text))


def clean_title(title: str) -> str:
    text = clean(title)
    text = re.sub(r"\b(round trip|one way)\s+[A-Z]\s+(economy|business|first|premium)\b", r"\1 \2", text, flags=re.I)
    text = re.sub(r"\s*\|\s*$", "", text)
    text = re.sub(r"\s+(?:one-way|one way|round trip|starting at|from)\s*$", "", text, flags=re.I)
    return text.strip(" -·|,")


def normalize_title(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", title.casefold())
    text = re.sub(r"\b(?:one way|oneway|round trip|starting at|from|departing)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def good_title(title: str) -> bool:
    cleaned = clean(title)
    if not 4 <= len(cleaned) <= 160:
        return False
    if cleaned.lower() in JUNK_TITLES:
        return False
    if PRICE_EXACT.match(cleaned) or cleaned.startswith("$"):
        return False
    if JUNK_LABEL.match(cleaned):
        return False
    return bool(re.search(r"[A-Za-z]{2}", cleaned))


def find_card(price_node: Node, limit: int = 12) -> tuple[Node | None, str, str]:
    """Walk up until the ancestor yields a usable offer name without swallowing other prices."""
    current = price_node.parent
    depth = 0
    while current is not None and depth < limit:
        text = clean(current.text())
        if len(text) > 2200 or len(distinct_prices(text)) > 4:
            break
        title, description = resolve_title(price_node, current)
        title = clean_title(title)
        if good_title(title):
            return current, title, description
        current = current.parent
        depth += 1
    return None, "", ""


def first_href(card: Node, source_url: str) -> str:
    for anchor in card.descendants():
        if anchor.tag != "a":
            continue
        href = anchor.attr("href").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(source_url, href)
        if urlparse(absolute).scheme in {"http", "https"}:
            return absolute
    return ""


def strip_prefix(text: str) -> str:
    lowered = text.lower()
    for prefix in ARIA_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix):].strip()
    return text.strip()


def resolve_title(price_node: Node, card: Node) -> tuple[str, str]:
    """Return (title, description) built only from text the card actually shows."""
    description = ""

    # 1. Route-style cards: destination plus departure date, e.g. airline fare lists.
    destination = ""
    departure = ""
    for candidate in card.descendants():
        markers = f"{candidate.attr('class')} {candidate.attr('data-testid')} {candidate.attr('id')}"
        text = clean(candidate.text())
        if not text or len(text) > 80 or PRICE_EXACT.match(text):
            continue
        if DEST_ATTR.search(markers) and 4 <= len(text):
            text = re.sub(r"^(?:to|from)\s+", "", text)
            if len(text) > len(destination):
                destination = text
        elif destination and not departure and re.search(r"(?:date|depart)", markers, re.I) and len(text) <= 40:
            departure = text
    if destination:
        departure = re.sub(r"^(?:departing|departs|on)\s+(?:on\s+)?", "", departure, flags=re.I)
        title = destination if not departure else f"{destination}, departing {departure}"
        return title[:160], description

    # 2. Card text that opens with the offer name and then prints a price.
    card_text = clean(card.text())
    match = re.match(rf"^(.{{4,120}}?)\s+(?:from|starting at|starting from|as low as|only)\s+(?:US)?\$\s?{NUM}\b", card_text, re.I)
    if match:
        title = clean(match.group(1)).strip(" -·|,")
        if title and "$" not in title and " " in title and title.lower() not in PREFIX_WORDS:
            return title[:160], description

    # 3. Accessible labels on the card's own controls.
    for anchor in card.descendants():
        label = clean(anchor.attr("aria-label"))
        if not label:
            continue
        label = strip_prefix(label)
        match = re.match(rf"^(.*?)\s+from\s+\$\s?{NUM}\b(.*)$", label, re.I)
        if match:
            label = clean(match.group(1))
            tail = clean(match.group(2))
            tail = re.sub(r"^updated:?\s*\d+\s*(?:hours?|minutes?|days?|weeks?)\s*ago", "", tail, flags=re.I)
            tail = tail.strip(" -·|")
            if re.search(r"\b(round trip|one way|one-way|economy|business|first|premium)\b", tail, re.I):
                description = tail[:120]
        described = label != clean(anchor.attr("aria-label"))
        named = re.search(r"fare|deal|offer|sale|save|book|ticket|cruise|package|stay|rental", label, re.I)
        if 6 <= len(label) <= 160 and label.lower() not in JUNK_TITLES and "$" not in label and (described or named):
            return label, description

    # 4. Headings, but only inside a card-shaped container.
    card_shaped = card.tag in {"li", "article"} or bool(CARD_CLASS.search(card.attr("class")))
    if card_shaped:
        for heading in card.descendants():
            if heading.tag in {"h1", "h2", "h3", "h4", "h5"}:
                text = clean(heading.text())
                if 4 <= len(text) <= 160 and text.lower() not in JUNK_TITLES:
                    return text, description

    # 5. Elements whose class marks them as the card title.
    for candidate in card.descendants():
        markers = f"{candidate.attr('class')} {candidate.attr('data-testid')} {candidate.attr('id')}"
        if TITLE_ATTR.search(markers):
            text = clean(candidate.text())
            if 4 <= len(text) <= 160 and text.lower() not in GENERIC_TITLES and "$" not in text:
                return text, description

    # 6. Link text with the printed price and "starting at" phrasing removed.
    for anchor in card.descendants():
        if anchor.tag != "a":
            continue
        text = clean(anchor.text())
        if not 6 <= len(text) <= 200:
            continue
        text = re.sub(rf"(?:US)?\$\s?{NUM}\s*\*?", " ", text)
        text = re.sub(r"\b(?:one-way|round trip)?\s*(?:starting at|from)\b", " ", text, flags=re.I)
        text = re.sub(r"\b(?:book now|book|select|shop now|see details)\b", " ", text, flags=re.I)
        text = clean(text).strip(" -·|,")
        if len(text) >= 6 and text.lower() not in GENERIC_TITLES:
            return text[:160], description

    return "", description


def same_host(url: str, provider_url: str) -> bool:
    target = (urlparse(url).hostname or "").lower().rstrip(".")
    base = (urlparse(provider_url).hostname or "").lower().rstrip(".")
    return bool(base and (target == base or target.endswith("." + base)))


def walk_jsonld(obj, provider: dict, source_url: str, fetched_at: str, parent_name: str = "") -> list[dict]:
    found: list[dict] = []
    if isinstance(obj, list):
        for item in obj:
            found.extend(walk_jsonld(item, provider, source_url, fetched_at, parent_name))
        return found
    if not isinstance(obj, dict):
        return found
    types = obj.get("@type", [])
    if isinstance(types, str):
        types = [types]
    types = {str(item).rsplit("/", 1)[-1] for item in types if item}
    name = obj.get("name") if isinstance(obj.get("name"), str) else parent_name
    if "Offer" in types and "AggregateOffer" not in types:
        price = obj.get("price")
        if isinstance(price, dict):
            price = price.get("value")
        currency = str(obj.get("priceCurrency") or "").strip().upper()
        url = urljoin(source_url, obj["url"]) if isinstance(obj.get("url"), str) else source_url
        if price is not None and re.fullmatch(NUM, str(price).strip()) and re.fullmatch(r"[A-Z]{3}", currency) and same_host(url, provider["url"]):
            found.append({
                "provider": provider["name"], "title": clean(str(name)) or provider["name"],
                "price": str(price).strip(), "currency": currency, "offer_url": url,
                "source_url": source_url, "fetched_at": fetched_at, "price_source": "json-ld",
                "evidence": f"JSON-LD Offer price {price} {currency}",
            })
        return found
    for key, value in obj.items():
        if key.startswith("@") and key != "@graph":
            continue
        found.extend(walk_jsonld(value, provider, source_url, fetched_at, name))
    return found


def extract(html: str, provider: dict, source_url: str, fetched_at: str) -> list[dict]:
    builder = parse(html)
    offers = []
    for document in builder.jsonld:
        try:
            offers.extend(walk_jsonld(json.loads(document), provider, source_url, fetched_at))
        except (json.JSONDecodeError, RecursionError):
            continue

    for node in builder.root.descendants():
        text = clean(node.text())
        if not text or len(text) > 120:
            continue
        priced = is_price_node(node)
        parsed = price_from_text(text, leading=priced)
        if not parsed:
            continue
        if blocked_region(node):
            continue
        if not priced:
            # A bare price in page text is only trusted inside a card that links to a booking action.
            probe = node.parent
            depth = 0
            trusted = False
            while probe is not None and depth < 6:
                marker = " ".join(a.attr("aria-label") + " " + a.attr("href")
                                  for a in probe.descendants() if a.tag == "a")
                if re.search(r"fare|deal|offer|price|book|ticket|cruise|flight", marker, re.I):
                    trusted = True
                    break
                probe = probe.parent
                depth += 1
            if not trusted:
                continue
        card, title, description = find_card(node)
        if card is None:
            continue
        link = first_href(card, source_url)
        if not link or not same_host(link, provider["url"]):
            link = source_url
        price, currency = parsed
        record = {
            "provider": provider["name"], "title": title, "price": price, "currency": currency,
            "offer_url": link, "source_url": source_url, "fetched_at": fetched_at,
            "price_source": "rendered-dom", "evidence": text[:80],
        }
        if description:
            record["description"] = description
        offers.append(record)
    return offers
