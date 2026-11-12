# Layover Ledger

An English (US) static directory of travel offers that are still on sale at the airline or cruise line that sells them. Current sources: Southwest Airlines, American Airlines, Carnival Cruise Line.

## What gets published

A price is published only when the provider's own page prints it. The scraper opens the configured official offer page in headless Chrome, lets the page's scripts run, and reads the price from either JSON-LD `Offer` data or the visible price element inside one offer card. Every published offer keeps the exact price text that was on the page plus the page URL, and the offer page shows that text next to the price.

Nothing is estimated. Discount claims ("up to $100 onboard credit"), financing text ("or from $36/mo"), navigation prices and prose are rejected. If a price cannot be read, the offer is dropped and the provider page shows an honest empty state. A scheduled run that reads zero priced offers fails instead of publishing an empty site.

`robots.txt` is fetched and respected for every URL before it is requested, including deal pages discovered from the configured page or from the provider's own sitemap.

## Data and update behavior

`data/offers.json` is the only observation store; no offer is written by hand. GitHub Actions refreshes it every six hours, rebuilds `site/`, and commits only when an observation changed. The workflow needs a public repository for hosted runners to stay on the free tier, and it uses no paid API, no secret and no runtime server.

## Local build

```
python scraper.py   # reads the configured official pages, needs Chrome or Edge installed
python build.py     # renders site/
```

Set `CHROME_BIN` if the browser is not in a standard location. Only the Python standard library is used.

## Release status

The custom domain and the production Pages URL are not configured yet, so canonical links and sitemap entries are omitted until the real URL is recorded in `.ilang/site.ilang`. No affiliate relationship is active; affiliate parameters are added only after a program is approved and its public terms are reviewed. The repository makes no revenue or traffic promise.

Site rules live in `.ilang/site.ilang` (I-Lang). Protocol overview: [ilang.ai](https://ilang.ai).
