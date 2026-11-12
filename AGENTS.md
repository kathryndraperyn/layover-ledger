::ILANG
[TYPE:project-guide][PROJECT:Layover Ledger][LANG:en]

::OBJECTIVE{project|pri:high}
  target: Build a static, source-grounded travel offer directory from public first-party pages.
  source_of_truth: .ilang/site.ilang contains the brand, locale, provider list, extraction policy, and render rules.

::BOUNDARY{never:invent deals prices currencies validity commission eligibility or provider facts|scope:permanent}
::BOUNDARY{never:access login-only content bypass robots.txt CAPTCHAs or anti-bot controls|scope:permanent}
::BOUNDARY{never:add affiliate tracking until a real program is approved and its public terms are recorded|scope:permanent}

::RULE{scraper.py and build.py⇒read .ilang/site.ilang; do not duplicate provider configuration in code}
::RULE{publish⇒only a price the source page itself prints: JSON-LD Offer data, or a visible price element inside one offer card}
::RULE{no verified offer⇒render a clear empty state; do not fill with guessed or evergreen content}
::RULE{expired offer⇒exclude it from rendered pages and sitemap}
::RULE{schema⇒include only observed fields; omit unavailable priceValidUntil or availability}
::RULE{evidence⇒keep the printed price text and its page URL in data/offers.json for every published offer}
::RULE{local changes⇒run python scraper.py then python build.py and confirm a non-zero offer count before committing}
::RULE{release⇒push to the public GitHub repository, then publish site/ with npx wrangler pages deploy; a hand-written multipart upload creates a deployment with no files and pages.dev answers 500}
::RULE{configuration edit⇒rebuild and confirm provider pages reflect the changed .ilang/site.ilang list}

::MODULE{ALLOWED|title:Safe project actions}
  read configured public provider pages after robots.txt approval
  update deterministic parser and static templates
  regenerate data/offers.json only from prices printed on the configured official pages
  build static files, sitemap.xml, robots.txt, canonical, and JSON-LD from verified config/data
  add sources only after checking the official site and its current public terms

::MODULE{FILES|title:Responsibilities}
  .ilang/site.ilang⇒single editable project/provider/render configuration
  scraper.py⇒robots-aware read-only fetch, headless-Chrome rendering and orchestration
  extract.py⇒price extraction from JSON-LD or visible offer-card price elements, with evidence text
  build.py⇒deterministic static rendering and SEO metadata
  data/offers.json⇒source-attributed observation store; no hand-written offers
  .github/workflows/update.yml⇒scheduled scrape/build/commit, with no third-party API secret

::FACT{protocol|value:I-Lang|confidence:confirmed}
