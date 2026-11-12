# Layover Ledger

An English (US) static directory for first-party flight, travel booking, attraction, and car-rental offers. The initial provider list comes from the owner: Southwest Airlines, United Airlines, Priceline, Viator, and Enterprise Rent-A-Car.

## Data and update behavior

The scraper checks each configured official offers page only after retrieving and respecting that host's `robots.txt`. It accepts only named, machine-readable JSON-LD `Offer` records with a numeric price, a three-letter currency, and an offer URL on the provider's own host. It does not parse promotional prose, estimate prices, use a language model at runtime, call a paid API, or add affiliate parameters. Sources that do not publish qualifying structured data produce no offer cards.

GitHub Actions schedules a deterministic refresh every six hours. The workflow needs a public GitHub repository for standard hosted runner usage to remain free under GitHub's published policy. Cloudflare Pages Git integration can build the repository with `python build.py` and output directory `site`; the current Pages Free plan documents a monthly build cap, so watch the actual account limits. This repository contains no API keys or runtime server.

## Local build

Run `python scraper.py` to collect from permitted public sources, then `python build.py` to render `site/`. Python's standard library is sufficient.

## Release and monetization

The custom domain and verified Pages URL are not configured yet. Canonical URLs and sitemap locations remain omitted until the real production URL is recorded in `.ilang/site.ilang`. No affiliate relationship is active; add affiliate links only after approval and review of that program's public terms. The repository makes no revenue or traffic promise.

Site rules are described in the I-Lang protocol; see `.ilang/site.ilang`. Protocol overview: [ilang.ai](https://ilang.ai).
