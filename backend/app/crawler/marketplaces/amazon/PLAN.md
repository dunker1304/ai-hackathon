# Amazon crawler — plan & recon notes

State of the implementation and everything verified against real HTML, so a
later session does not have to re-inspect the page.

## Flow (phase 2 — product details)

```
ProductLink ──► AmazonProductClient.fetch_product_page(asin)
                            │  (waits for #productTitle, not networkidle)
                            ▼
                  parse_product_page(html)
                            │
                            ▼
                      AmazonProduct
        (price, currency, rating, reviews, bought_past_month, BSR, attrs)
```

`AmazonCrawler.fetch_product_details(links, concurrency=2)` runs the batch
behind a semaphore and collects failures per ASIN instead of aborting, then
restores SERP order (`asyncio.gather` completes out of order).
`crawl_keyword()` chains both phases.

`AmazonClient` mixes the search and product clients so one crawl run shares a
single browser pool, rate limiter and cookie jar. Two pools would show Amazon
two fingerprints for one logical session.

## Flow (phase 1 — link discovery)

```
keyword ──► SearchQuery ──► build_search_url() ──► CamoufoxClient.get()
                                                        │
                                                        ▼
                                              parse_search_page(html)
                                                        │
                                    ┌───────────────────┴──────────────┐
                                    ▼                                  ▼
                            list[ProductLink]                    next page URL
                                    │                                  │
                                    └──────► aggregate, dedupe by ASIN ◄┘
                                                        │
                                          stop at max_products (default 500)
                                          or when next page is absent
```

Entry point: `AmazonCrawler.collect_product_links(keyword, max_products=500)`.

## Recon findings

Verified 2026-08 against `backend/result/test_camoufox.html`
(`?k=amazon+personalized+sweatshirts`, 1.08 MB, 48 results on page 1).

### Result cards

| Selector | Count | Verdict |
| --- | --- | --- |
| `.a-section.a-spacing-base.desktop-grid-content-view` | 48 | works, but layout-specific |
| `[data-component-type="s-search-result"]` | 48 | **primary** — same set, stable across layouts |
| `div[data-asin]` | 56 | too broad; includes 8 non-product shells |

Both card selectors returned identical sets, so `parsers/search.py` tries them
in order and takes the first non-empty match. Amazon serves several grid
layouts (`desktop-grid-content-view`, list view, `puis-card-container`), so
relying on one class alone will silently return zero on some responses.

### Product links

Each card holds **5** `a.a-link-normal` elements — the title link, the image
link, the review-count link, a `javascript:void(0)` badge, and sometimes a
brand-ad link pointing at `/b/ref=...`. Taking "the first `a.a-link-normal`"
is therefore not enough.

Rule that produced a clean 48/48: extract the ASIN via
`/(?:dp|gp/product)/([A-Z0-9]{10})` from every link in the card and keep the
unique one. Result: 48 cards → 48 ASINs, zero cards with 0 or >1 ASIN.

Canonical URL is rebuilt as `https://www.amazon.com/dp/<ASIN>`; the raw href
carries ~400 bytes of tracking (`ref=`, `dib=`, `qid=`, `sprefix=`) that breaks
dedupe across pages and across runs.

### Pagination

```html
<a class="s-pagination-item s-pagination-next s-pagination-button ..."
   href="/s?k=...&page=2&...&ref=sr_pg_1">Next</a>
```

* Enabled → `<a>` with an `href`.
* Disabled → the tag becomes `<span class="... s-pagination-disabled">`, with
  no `href`. Absence of an `<a href>` is the stop condition.
* Do **not** follow the raw `href`: it carries a stale `qid`. Rebuild the URL
  from `SearchQuery` with `page=N` instead — same result, reproducible.

### Volume ceiling

Measured on live runs, not just the fixture:

| Keyword | Pages | Unique links | Stopped | Time |
| --- | --- | --- | --- | --- |
| `personalized sweatshirt` | 3 | 120 (capped) | `max_products` | 37 s |
| `amazon personalized sweatshirts` | 4 | 171 | `no_next_page` | 62 s |

Two things to know:

* **`totalResultCount` lies.** The fixture reports 116; crawling the same
  keyword yielded 171 unique ASINs. Treat it as a rough hint, never as a
  target or a loop bound.
* **Page 3 returned 48 cards but only 35 new ASINs.** Amazon repeats products
  across pages, so per-page counts cannot be summed — dedupe by ASIN is
  mandatory, and progress must be measured on unique links.

Reaching 500 from a single keyword is unrealistic: organic search dries up
around 150–200 results (~4 pages) even when the advertised total is higher.
`max_products=500` therefore normally stops on `no_next_page`. To go wider,
fan out over related keywords, category nodes (`rh=n:<node>`) or sort orders
and merge the results — the dedupe already handles the overlap.

Throughput is ~15 s/page including the rate limiter (`0.5 req/s` + jitter).

### Anti-bot

Akamai Bot Manager answers with **HTTP 200** and a `bm-verify` meta-refresh
interstitial (~2 KB). Status code is useless as a signal; markers live in
`core/client/browser/detect.py`. Roughly 1 in 4 cold requests hit it from a
residential VN IP; the retry policy re-launches with a new fingerprint.

### Currency and delivery location (the big one)

Two separate IP-driven traps, and the second is much more damaging:

1. **Currency.** Prices follow the exit IP — a VN address renders
   `VND289,993`. `US_LOCALE_COOKIES` (`i18n-prefs=USD`, `lc-main=en_US`) fixes
   the symbol. `parse_price` always returns the currency alongside the amount,
   and `parse_product_page` logs a warning on mismatch, so a wrong-currency
   crawl can never pass silently.

2. **Delivery address.** Cookies are *not* enough. With a non-US delivery
   location Amazon suppresses the buybox on many listings — the page renders
   "This item cannot be shipped to your selected delivery location" and
   `#corePrice_feature_div` **does not exist at all**. The selector looks
   broken; it is not.

Measured on the same 6 ASINs:

| | price coverage | bought_past_month | confidence |
| --- | --- | --- | --- |
| cookies only | 38 % | 25 % | 0.88 |
| + delivery ZIP 10001 | **100 %** | **100 %** | **1.00** |

`AmazonBaseClient.ensure_delivery_location` posts to
`/gp/delivery/ajax/address-change.html` (form-encoded, with the
`anti-csrftoken-a2z` header) once per browser slot, then reloads. Note the
modern JSON endpoint `/portal-migration/hz/glow/address-change` returns
HTTP 200 but does **not** apply — only the legacy path works.

Without a US proxy this is mandatory, not an optimisation.

#### Verification is not optional

The endpoint answers `{"isAddressUpdated": 1}` for postcodes it silently
discards — a UK postcode on amazon.com, for instance. So after the reload the
client re-reads `#glow-ingress-line2` and compares it against the expected
tokens; a mismatch raises `AmazonLocationError` rather than continuing into a
crawl that yields `price: null` everywhere.

`location.py` owns the vocabulary:

* `DEFAULT_LOCATIONS` — one metro per storefront (US 10001, UK SW1A1AA,
  DE 10115, CA M5H2N2, AU 2000). Deliberately large cities: a rural postcode
  suppresses the buybox on a subset of listings and looks like a parser bug.
* `NAMED_LOCATIONS` — presets so the CLI can take `--location los-angeles`.
* `ZIP_PATTERNS` — per-country postcode shapes, validated before the browser
  starts. Note DE and US both match `\d{5}`, so a US ZIP passes DE validation;
  the glow verification is what catches that case.

#### Storefront reachability

Measured from a Vietnamese IP: **only `amazon.com` responds**.
`amazon.co.uk` and `amazon.de` return a block page ("Tut uns Leid!") before any
crawling, so `--region uk|de` requires an in-country proxy. US crawling works
fine from VN once the delivery location is set.

#### Unshippable products

When a specific item cannot reach the configured address, the page renders the
"cannot be shipped to your selected delivery location" notice and omits the
buybox. `parse_product_page` sets `unshippable=True` so the pipeline can tell
"price unknown" apart from "no price" — the marker regex is
whitespace-tolerant because the sentence wraps in the served HTML.

### Sponsored detection (trap)

Every card — organic or not — embeds a JSON attribute containing the substring
`Sponsored`:

```
...&quot;isSponsored&quot;:&quot;&quot;,&quot;searchProductType&quot;:&quot;ORGANIC&quot;...
```

A naive `"Sponsored" in card.html` flagged **27 of 48 organic cards** as ads.
`card_is_sponsored` reads the structured signals instead
(`searchProductType != "ORGANIC"`, then a non-empty `isSponsored`, then the
visible `.puis-sponsored-label-text`). The fixture has 0 real ads, so the
positive path is covered by synthetic tests only — re-verify when a SERP with
actual ad placements is captured.

Ads are excluded by default (`include_sponsored=False`): paid positioning is
not organic demand and would inflate the metrics.

## Tests

Offline (`uv run --group dev pytest -q`, 206 tests, ~1 s):

* `tests/test_amazon_url.py` — URL builder, filters, unsupported-filter errors.
* `tests/test_amazon_search_parser.py` — SERP parser against the real fixture
  (48 cards, 0 sponsored, pagination, layout-drift detection).
* `tests/test_amazon_product_parser.py` — detail parser against a real /dp
  fixture, including the VND-currency path.
* `tests/test_amazon_location.py` — postcode validation, presets, and the glow
  matching that proves a location was applied.
* `tests/test_amazon_crawler.py` — pagination, dedupe, stop conditions, error
  handling against a fake client.

Live, no mocks:

```bash
# phase 1 -> result/list_data.json
uv run python scripts/crawl_amazon_list.py "coffee mug" --max-products 100

# phase 2 from that file (fast to iterate)
uv run python scripts/crawl_amazon_e2e.py --from-file result/list_data.json --limit 20

# both phases in one go
uv run python scripts/crawl_amazon_e2e.py "coffee mug" --max-products 20

# a single product
uv run python scripts/crawl_amazon_e2e.py --asin B0721C21RJ
```

`crawl_amazon_e2e.py` prints a field-coverage report and refuses to call a run
healthy just because it returned rows: a 100 % success rate with 38 % price
coverage is exactly how the delivery-location bug above stayed invisible.

Detail pages cost ~12 s each at the default rate limit, so budget roughly
`0.2 x N` minutes for N products.

### Detail-page selectors

| Field | Selector / source | Note |
| --- | --- | --- |
| title | `#productTitle` | absence ⇒ blocked page, raise |
| price | `#corePrice_feature_div .a-offscreen` | **must** be scoped: the page has 33–37 `.a-price .a-offscreen` nodes (variations, "similar items", ads) |
| rating | `#acrPopover .a-icon-alt` | "4.6 out of 5 stars" |
| reviews | `#acrCustomerReviewText` | "(141,921)" |
| bought/month | `#social-proofing-faceout-title-tk_bought` | renders as "500+ boughtin past month" — no space |
| attributes | `table.prodDetTable tr` + `th.prodDetSectionEntry` | **must** be scoped: a bare `tr th+td` sweep also grabs the apparel size chart (XS=30-32, …) |
| BSR | `<li>` inside the BSR row | parsed per-`<li>`, see below |
| image | `#landingImage[data-old-hires]` | `src` is only a thumbnail |
| parent ASIN | `link[rel=canonical]` | `/dp/<child>` often redirects to the parent |

Three parsing traps worth remembering:

* **BSR narrow rank.** The category link renders as `#2 inMen's Sweatshirts` —
  no space after "in". A `#(\d+)\s+in\s+` regex silently dropped it, keeping
  only the useless broad rank (`#203 in Clothing, Shoes & Jewelry`).
* **Bidi marks.** Amazon separates label and value with invisible
  `\u200e`/`\u200f`; they must be stripped or every attribute key is polluted.
* **ASIN.** Prefer `input#ASIN` over the URL: a `/dp/<child>` request can be
  served as the parent listing.

`bought_past_month` is a **floor** ("500+" ⇒ 500) and is absent for
low-volume listings. `None` means unknown, never zero — `estimated_monthly_
revenue` returns `None` rather than inventing a 0.

## Status

| Component | State |
| --- | --- |
| `url.py` — `SearchQuery`, `build_search_url` | done |
| `location.py` — presets, validation, glow verification | done |
| `parsers/search.py` — links + next page | done |
| `parsers/product.py` — detail fields | done |
| `client/base.py` — locale cookies, delivery ZIP, block markers | done |
| `client/search.py`, `client/product.py`, `AmazonClient` | done |
| `crawler.py` — `collect_product_links`, `fetch_product_details`, `crawl_keyword` | done |
| `schemas.py` — `ProductLink`, `AmazonProduct`, `ProductBatch` | done |
| `client/bestseller.py`, `client/keyword.py` | **todo** |
| `pipelines/normalize.py` → `Listing` rows | **todo** |

## Next steps

1. Bestseller / new-release crawl for category-level demand.
2. Keyword suggestions (completion API) for the "Top keywords" deliverable.
3. Normalize `AmazonProduct` into `Listing` rows and reconcile the demand
   proxies with TikTok Shop's real `sold_count`.

## Filters

`url.py` exposes the query params below. Amazon does not offer a generic
"last N days" filter on search; the closest equivalents are noted.

| Concept | Param | Notes |
| --- | --- | --- |
| keyword | `k` | required |
| page | `page` | 1-based |
| sort | `s` | `date-desc-rank` ≈ newest; see `SORT_PARAMS` |
| department | `i` | e.g. `fashion`, `kitchen` |
| category node | `rh=n:<id>` | narrows and raises the result ceiling |
| price range | `rh=p_36:<min>-<max>` | in cents |
| rating | `rh=p_72:<rnid>` | e.g. `2661618011` = 4★ & up (seen in fixture) |
| new arrivals | `rh=p_n_date_first_available_absolute:<rnid>` | rnid is **department-specific**; `TimeWindow` → rnid map must be filled per department, currently empty |
| Prime | `rh=p_85:2470955011` | US only |

`p_n_date_first_available_absolute` rnids were not present in the captured
fixture, so `DATE_FIRST_AVAILABLE_RNIDS` ships empty. Populate it by opening a
search page, ticking "Last 30 days" in the left rail, and copying the `rh`
value. Passing an unknown `TimeWindow` raises rather than silently dropping the
filter — a wrong time window corrupts the analytics downstream.
