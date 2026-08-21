# Live Amazon crawl — how to run it

Two scripts, run from `backend/`. Both hit Amazon for real: no mocks, no
fixtures. For the offline tests run `uv run --group dev pytest -q` instead.

| Script | Phase | Input | Output |
| --- | --- | --- | --- |
| `crawl_amazon_list.py` | 1 — discovery | keyword(s) | `result/list_data.json` |
| `crawl_amazon_e2e.py` | 2 — details | keyword, ASINs, or phase-1 JSON | `result/detail_data.json` |

They are split so you can crawl links once and re-run the (slow, expensive)
detail pass as often as you like without touching search again.

---

## Setup

Once per machine:

```bash
cd backend
uv sync
uv run python -m camoufox fetch     # downloads the anti-detect browser, ~150 MB
```

Check it works before blaming the crawler:

```bash
uv run python scripts/test_camoufox.py smoke
```

No `.env` key is required for crawling — the `CRAWLER_*` settings all have
defaults. See [Tuning](#tuning) if you want to change them.

---

## Delivery location — read this before anything else

**Amazon only renders a price if the configured delivery address can receive
the item.** From a Vietnamese IP most US listings come back as "This item
cannot be shipped to your selected delivery location" with *no price element
in the DOM at all*. The selector is not broken; the price was never rendered.

Both scripts therefore take a location, verify Amazon actually applied it, and
abort if it did not:

```bash
# default: the storefront's default metro (US -> New York 10001)
uv run python scripts/crawl_amazon_list.py "coffee mug"

# pick a preset
uv run python scripts/crawl_amazon_e2e.py "coffee mug" --location los-angeles

# or any postcode inside that storefront's country
uv run python scripts/crawl_amazon_e2e.py "coffee mug" --location 98101

# another storefront (its own default location applies)
uv run python scripts/crawl_amazon_list.py "coffee mug" --region uk

# see what is available
uv run python scripts/crawl_amazon_list.py --list-locations --region uk
```

### Rules

| Rule | Why |
| --- | --- |
| `--location` must match `--region` | A UK postcode on amazon.com is *accepted by the endpoint and then ignored*. Every price silently disappears. The scripts reject the mismatch before launching a browser. |
| The location is verified, not assumed | After setting it, the crawler re-reads the `#glow-ingress-line2` widget and compares. Amazon reports `isAddressUpdated: 1` even for postcodes it discards. |
| A failure aborts the run | Otherwise you get a JSON full of `price: null` that looks like a parser bug. Pass `--allow-missing-location` to continue anyway. |

### Flags

| Flag | Meaning |
| --- | --- |
| `--region {us,uk,de,ca,au}` | which storefront |
| `--location ZIP\|PRESET` | postcode (`90210`, `SW1A 1AA`) or preset (`chicago`) |
| `--location none` | do not set one; accept whatever your IP implies |
| `--allow-missing-location` | warn instead of aborting |
| `--list-locations` | print presets for `--region` and exit |

Presets: US `new-york` `los-angeles` `chicago` `houston` `seattle` ·
UK `london` `manchester` · DE `berlin` `munich` · CA `toronto` · AU `sydney`.

### Known limitation

From a Vietnamese IP only **amazon.com** is reachable. `amazon.co.uk` and
`amazon.de` return a block page before any crawling starts, so `--region uk|de`
needs a proxy in that country (`--proxy`). US crawling works fine from VN as
long as the delivery location is set.

---

## Phase 1 — collect product links

```bash
uv run python scripts/crawl_amazon_list.py "coffee mug"
```

```
--- 'coffee mug'
    30 links | 1 page(s) | stopped: max_products
      #  1 B0DF472VMZ  Owala SmoothSip Slider Insulated Stainless Steel...
      ...
DONE
keywords : 1 (0 failed)
links    : 30
elapsed  : 15.4s
saved    : backend/result/list_data.json
```

Common variations:

```bash
# several keywords in one run (all land in the same file)
uv run python scripts/crawl_amazon_list.py "coffee mug" "travel tumbler" "water bottle"

# cap the result set; default is 500 per keyword
uv run python scripts/crawl_amazon_list.py "phone case" --max-products 100

# newest first, 4★ and up, $10-50, Prime only
uv run python scripts/crawl_amazon_list.py "phone case" \
    --sort newest --min-rating 4 --min-price 10 --max-price 50 --prime

# include ads (excluded by default)
uv run python scripts/crawl_amazon_list.py "mug" --include-sponsored

# write somewhere else
uv run python scripts/crawl_amazon_list.py "mug" -o result/mugs.json
```

### Reading `stopped_reason`

| Value | Meaning |
| --- | --- |
| `max_products` | hit your cap — there were more results available |
| `no_next_page` | Amazon ran out of organic results |
| `no_new_results` | the last page repeated products already seen |
| `max_pages` | hit the page cap (7 by default) |
| `error_on_page_N: BlockedError` | blocked mid-crawl; earlier pages were kept |

`no_next_page` on a broad keyword is normal, not a failure. Amazon dries up
around 150–200 unique products (~4 pages) even when it advertises more. To go
wider, use several related keywords rather than raising `--max-products`.

---

## Phase 2 — fetch product details

Three ways to feed it.

```bash
# A. reuse phase-1 links (recommended while iterating)
uv run python scripts/crawl_amazon_e2e.py --from-file result/list_data.json --limit 20

# B. both phases in one go
uv run python scripts/crawl_amazon_e2e.py "coffee mug" --max-products 20

# C. specific products
uv run python scripts/crawl_amazon_e2e.py --asin B0721C21RJ B073WJMKHN
```

With `--from-file` and several keywords in the file, narrow it down:

```bash
uv run python scripts/crawl_amazon_e2e.py --from-file result/list_data.json \
    --keyword-filter "coffee mug" --limit 50
```

Output:

```
ASIN              PRICE  RATE   REVIEWS   BOUGHT     BSR  TITLE
--------------------------------------------------------------------------
B0DF472VMZ     USD24.98   4.6     14392     9000       1  Owala SmoothSip...
B073WJMKHN     USD31.99   4.8    147439     3000       4  YETI Rambler 20 oz...

DATA QUALITY
products      : 6  (failed: 0)
success rate  : 100%
confidence    : 1.00
with BSR      : 100%
currencies    : {'USD': 6}
field coverage:
  title                  100%
  price                  100%
  bought_past_month      100%
  ...
```

### Read the quality block, not the success rate

This is the whole point of the script. A run can report **100 % success** and
still be broken — during development one did exactly that while returning
prices for only 38 % of products. Anything under 80 % is flagged `<-- low`.

| Signal | What it means |
| --- | --- |
| `currencies` ≠ the region's currency | the delivery location did not stick; prices are not comparable across marketplaces |
| more than one currency | do **not** aggregate this batch |
| `unshippable: N` | N products have no buybox at this address — their price is *unknown*, not zero. Try another `--location`. |
| `price` coverage < 80 % with `unshippable: 0` | genuine selector drift |
| `bought_past_month` low | often genuine — Amazon hides it for low-volume listings |
| `confidence` < 0.8 | pages parsed but came back mostly empty; treat as suspect |

---

## Output files

### `result/list_data.json`

```jsonc
{
  "crawled_at": "2026-08-21T...", "region": "us", "max_products": 500,
  "keywords": [
    {
      "keyword": "coffee mug",
      "pages_fetched": 3,
      "stopped_reason": "max_products",
      "links": [
        { "asin": "B0DF472VMZ", "url": "https://www.amazon.com/dp/B0DF472VMZ",
          "title": "...", "position": 1, "page": 1, "sponsored": false,
          "keyword": "coffee mug" }
      ]
    }
  ],
  "total_links": 30, "elapsed_seconds": 15.4
}
```

`url` is canonical (`/dp/<ASIN>`, no tracking) so it is a stable dedupe key
across runs.

### `result/detail_data.json`

```jsonc
{
  "crawled_at": "...", "region": "us",
  "quality":  { "success_rate": 1.0, "coverage": {...}, "currencies": {"USD": 6} },
  "warnings": ["..."],
  "products": [
    { "asin": "B0DF472VMZ", "title": "...", "brand": "Owala",
      "price": 24.98, "currency": "USD", "list_price": null,
      "rating": 4.6, "review_count": 14392, "bought_past_month": 9000,
      "unshippable": false,
      "best_seller_ranks": [{ "rank": 1, "category": "Travel Mugs" }],
      "categories": [...], "bullets": [...], "attributes": {...},
      "image_url": "...", "parent_asin": null, "variation_count": 12,
      "keyword": "coffee mug", "position": 1, "parse_confidence": 1.0 }
  ],
  "failed": { "B0XXXXXXXX": "BlockedError: ..." },
  "elapsed_seconds": 70.3
}
```

Two fields that are easy to misread:

* **`bought_past_month` is a floor, and `null` means unknown.** Amazon renders
  "500+ bought in past month" and omits the widget entirely below a threshold.
  `null` is *not* zero — treating it as zero invents demand data.
* **`price` is meaningless without `currency`.** Always read them together.
* **`price: null` with `unshippable: true`** means Amazon refused to show a
  price at that delivery address — the product may well be cheap and in stock.
  Do not average it in as a missing value.

---

## How long it takes

At the default rate limit (0.5 req/s + jitter):

| Work | Time |
| --- | --- |
| 1 search page (~48 links) | ~5 s |
| 1 detail page | ~12 s |
| 100 products, end to end | ~20 min |

Detail pages dominate. Crawl once, save the JSON, and iterate on the JSON.

Raising `--concurrency` past 3–4 does not help much: the rate limiter is the
bottleneck, and going faster is what gets you blocked. If you must go faster,
raise `--rps` **and** add proxies, not one without the other.

---

## Tuning

Flags are shared by both scripts:

| Flag | Use it when |
| --- | --- |
| `--headful` | you want to watch the browser (debugging selectors) |
| `-v` | verbose logs, including delivery-location confirmation and every retry |
| `--pool N` | more browsers in parallel (default 2) |
| `--concurrency N` | parallel detail fetches (default 2, e2e only) |
| `--rps N` | requests per second (default 0.5) |
| `--proxy "http://user:pass@host:port,..."` | rotate exit IPs |
| `--region uk` | non-US storefront |

Persistent settings go in `backend/.env` as `CRAWLER_*`:

```
CRAWLER_HEADLESS=false
CRAWLER_BROWSER_POOL_SIZE=2
CRAWLER_REQUESTS_PER_SECOND=0.5
CRAWLER_PROXIES=http://user:pass@host:port
```

---

## Troubleshooting

**`BlockedError: HTTP 200 ... bm-verify`**
Akamai interstitial. Amazon returns HTTP 200 for these, which is why the
status code is not used as the block signal. The crawler retries with a fresh
fingerprint. Frequent blocks from one IP mean you need residential proxies —
expect roughly 1 in 4 cold requests to be challenged from a bare home IP.

**`AmazonLocationError: Delivery location did not stick`**
Amazon accepted the postcode but the glow widget still shows something else
(often your real country). Usually the postcode does not belong to the
storefront — check `--location` against `--region`. Re-run with `-v` to see
what the widget actually reads.

**Prices in the wrong currency, or `price: null` everywhere**
The delivery location is not applied. Confirm with `-v`:
`Delivery location 10001 (New York, NY) confirmed on slot 0`. If instead you
see `Amazon rejected ...`, the glow endpoint changed — see
`marketplaces/amazon/PLAN.md`.

**`unshippable` count is high**
The address is real but sellers do not ship there. Rural postcodes do this;
that is why the presets are all large metros. Switch with
`--location los-angeles` or similar.

**`ParseError: No result cards matched`**
Amazon changed its search layout. Capture the page and inspect it:

```bash
uv run python scripts/test_camoufox.py fetch "https://www.amazon.com/s?k=mug" --save result/debug.html
uv run python scripts/test_camoufox.py fetch "https://www.amazon.com/s?k=mug" \
    --selector '[data-component-type="s-search-result"]'
```

Then fix `RESULT_CARD_SELECTORS` in `marketplaces/amazon/constants.py`.

**`BrowserLaunchError: No WebGL data found for vendor ...`**
An unlucky fingerprint draw. The launcher already retries three times; if it
persists, pin the OS with `CRAWLER_ALLOWED_OS=["windows"]`.

**Empty result / 0 links**
Check the keyword actually returns products in a normal browser. A genuinely
empty SERP is reported as 0 links; a *drifted selector* raises `ParseError`
instead, so the two cases are distinguishable.

---

## Related

* `marketplaces/amazon/PLAN.md` — verified selectors, measured numbers, and
  the reasoning behind every workaround. Read this before changing a parser.
* `scripts/test_camoufox.py` — lower-level browser harness (`fetch`, `smoke`,
  `fingerprint`, `bench`) for debugging a single page.
* `uv run --group dev pytest -q` — 206 offline tests over saved fixtures.
