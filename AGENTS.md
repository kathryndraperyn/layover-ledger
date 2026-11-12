::ILANG
[TYPE:project-guide][PROJECT:Layover Ledger][LANG:en]

::OBJECTIVE{project|pri:high}
  target: Build a static, source-grounded travel offer directory from public first-party pages.
  source_of_truth: .ilang/site.ilang contains the brand, locale, provider list, extraction policy, and render rules.

::BOUNDARY{never:invent deals prices currencies validity commission eligibility or provider facts|scope:permanent}
::BOUNDARY{never:access login-only content bypass robots.txt CAPTCHAs or anti-bot controls|scope:permanent}
::BOUNDARY{never:add affiliate tracking until a real program is approved and its public terms are recorded|scope:permanent}

::RULE{scraper.py and build.py⇒read .ilang/site.ilang; do not duplicate provider configuration in code}
::RULE{publish⇒only explicitly named JSON-LD Offer records with a numeric price and ISO currency}
::RULE{no verified offer⇒render a clear empty state; do not fill with guessed or evergreen content}
::RULE{expired offer⇒exclude it from rendered pages and sitemap}
::RULE{schema⇒include only observed fields; omit unavailable priceValidUntil or availability}
::RULE{local changes⇒run python build.py and verify the generated site before committing}
::RULE{release⇒use a public GitHub repository and Cloudflare Pages only after actual account connection is available}
::RULE{configuration edit⇒rebuild and confirm provider pages reflect the changed .ilang/site.ilang list}

::MODULE{ALLOWED|title:Safe project actions}
  read configured public provider pages after robots.txt approval
  update deterministic parser and static templates
  regenerate data/offers.json only from observed official machine-readable offers
  build static files, sitemap.xml, robots.txt, canonical, and JSON-LD from verified config/data
  add sources only after checking the official site and its current public terms

::MODULE{FILES|title:Responsibilities}
  .ilang/site.ilang⇒single editable project/provider/render configuration
  scraper.py⇒robots-aware read-only fetch and strict JSON-LD Offer extraction
  build.py⇒deterministic static rendering and SEO metadata
  data/offers.json⇒source-attributed observation store; no hand-written offers
  .github/workflows/update.yml⇒scheduled scrape/build/commit, with no third-party API secret

::FACT{protocol|value:I-Lang|confidence:confirmed}
