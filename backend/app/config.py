from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    zyte_api_key: str
    apify_token: str = ""
    pinterest_actor: str = "fetch_cat~pinterest-search-scraper"
    google_lens_actor: str = "borderline~google-lens"
    capsolver_api_key: str = ""
    twocaptcha_api_key: str = ""

    # Amazon's secondary source, behind SerpApi (app/serpapi_retail.py). Kept
    # because it reports exact review counts where SerpApi rounds them, and
    # because it covers Amazon when the SerpApi quota runs out. Unset => Amazon
    # falls back to the Zyte path with a weaker signal. See app/rainforest.py.
    rainforest_api_key: str = ""

    # Powers three features off one quota: Amazon and Walmart best-seller
    # results (app/serpapi_retail.py) and Google Lens picture search
    # (app/serp_lens.py). A product search costs 1 per site; a Lens search
    # costs 2 (exact + visual matches are separate calls).
    serpapi_key: str = ""

    # Oxylabs Web Scraper API — step 2 of the Lens sourcing pipeline
    # (app/lens_suppliers.py). Turns an Alibaba/1688 product URL that Google
    # Lens matched into supplier name, price and MOQ, with no browser involved.
    # Unset => that pipeline still answers, on SerpApi's own inline title/price
    # /thumbnail, and says so per row rather than returning an empty grid.
    oxylabs_username: str = ""
    oxylabs_password: str = ""
    # JavaScript rendering roughly triples the per-page cost and latency. The
    # fields this app reads (the embedded supplier record, og: tags, JSON-LD)
    # are all server-rendered, so it is off by default — flip it if a target
    # starts serving them client-side.
    oxylabs_render: bool = False
    # Read each supplier's own site — text *and* the pictures on it — for a
    # published email or phone (app/supplier_contacts.py). Off by default per
    # request, not per deployment: it costs several page fetches and a vision
    # call per supplier, against a pipeline built to answer in under 5s. This
    # is the kill switch for when it is requested; unset it to refuse globally.
    claude_supplier_contacts: bool = True
    # Where the 30-day Lens cache lives. Relative paths resolve against the
    # backend/ directory (the working directory uvicorn is started from).
    lens_cache_dir: str = ".cache/lens"

    # Browserbase drives the supplier sites' image-upload widgets from a cloud
    # browser instead of a local Chromium (see scrapers/image_discovery.py).
    # Unset => the sourcing pipeline degrades to local Playwright, the same way
    # an unset apify_token degrades the Trending tab.
    browserbase_api_key: str = ""
    browserbase_project_id: str = ""
    browserbase_proxies: bool = True
    # Browserbase's stealth tier is plan-gated; leave off on Free/Developer.
    browserbase_advanced_stealth: bool = False
    # A Browserbase Context seeded by a one-time interactive Made-in-China
    # login. Reusing it is the supported way to reach the login-gated supplier
    # fields; automating the multi-step SPA login per run risks the account.
    browserbase_context_id_made_in_china: str = ""

    # Vision models available for future match verification. Unused today —
    # carried over with the credentials so they're configured when needed.
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # Claude powers the two judgement calls no scraped field can answer: whether
    # a search result is actually the product that was searched for, and whether
    # a supplier's catalogue photo is the same product as the buyer's photo.
    # See app/claude_agent.py. Unset => both degrade to the pre-Claude behaviour
    # (unscreened results, perceptual-hash tiers only) with a warning, never to
    # an empty grid.
    # Open each top listing's product page to find the company behind it
    # (app/supplier_resolve.py). Without it the sourcing grid returns products
    # with no suppliers, because search-results cards name the product only.
    # Costs one Zyte page fetch per resolved listing, capped at RESOLVE_TOP_N.
    resolve_suppliers: bool = True

    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5"
    # Kill switches, so a Claude outage or a cost decision doesn't need a code
    # change. Off => that agent is skipped entirely, as if no key were set.
    claude_relevance_filter: bool = True
    claude_visual_matching: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
