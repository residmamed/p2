# Deploying p2 to p2.paraphoria.com

## Shape of it

```
paraphoria.com            Fluid (the art site) — completely untouched
p2.paraphoria.com         this app's UI        — Netlify, its own site
api.paraphoria.com        this app's API       — Fly.io container
```

Three decisions worth knowing before you follow the steps:

**Its own subdomain, not `paraphoria.com/p/2`.** The subpath would have required
a rewrite inside the Fluid site, and Fluid's local `dist/` is from 25 May and
does not match what is live — so redeploying it to add that rule risked
reverting the art site. A subdomain needs two DNS records and no change to Fluid
at all. (If you later want the `/p/2` path as well, the app supports it: set
`VITE_BASE_PATH=/p/2/` and it builds into a matching directory.)

**The API is on its own subdomain rather than proxied through Netlify.** A
single-site product search measures ~19 seconds and the sourcing pipelines run
several times longer. A CDN proxy cuts requests off well before that, so the
frontend calls the backend directly.

**That API subdomain is `api.paraphoria.com`, not the raw `*.fly.dev` host.**
Browsers decide whether a cookie is first-party by registrable domain, so
`p2.paraphoria.com` and `api.paraphoria.com` share a site and the session cookie
stays first-party — surviving Safari's tracking protection and the end of
third-party cookies. A cookie set on `fly.dev` would be third-party and
increasingly blocked.

---

## 0. Rotate the API keys first

`backend/.env` is committed to this repo, with live keys for Zyte, Apify,
SerpApi, Oxylabs, Anthropic, OpenAI, Gemini, Rainforest, Browserbase, CapSolver
and 2Captcha. While everything ran on your laptop that was untidy. Publishing
makes it urgent — and if this repo ever gains a remote, those keys are exposed
to anyone who can read it.

**`backend/.env.example` too.** Despite the name it is not a template: it holds
a real Apify token and the real Oxylabs username and password. Rotate those with
the rest, and don't treat the file as safe to share because of its extension.

Rotate them in each vendor's dashboard, then put the new values in Fly's secret
store (step 2). They never go in the image: `.dockerignore` excludes `.env` so
it cannot be baked into a layer.

## 1. Backend on Fly.io

Fly account creation and adding a card are yours to do — I can't do either.

```bash
brew install flyctl && fly auth login
```

Then, from `backend/`:

```bash
fly launch --no-deploy --copy-config --name p2-backend --region fra
```

`fly.toml` is already written; `--copy-config` keeps it instead of generating a
new one. Change `--region` to whatever is nearest you.

### Secrets before the first deploy

`app/config.py` declares `zyte_api_key` with no default and builds `Settings()`
at import time, so a machine with no secrets crash-loops rather than starting
degraded. Set them first:

```bash
fly secrets set \
  APP_PASSWORD='pick-a-long-one' \
  COOKIE_DOMAIN='.paraphoria.com' \
  ALLOWED_ORIGINS='https://p2.paraphoria.com' \
  RATE_LIMIT_PER_HOUR='60' \
  ZYTE_API_KEY='...' \
  ANTHROPIC_API_KEY='...' \
  SERPAPI_KEY='...' \
  OXYLABS_USERNAME='...' \
  OXYLABS_PASSWORD='...' \
  APIFY_TOKEN='...' \
  RAINFOREST_API_KEY='...' \
  BROWSERBASE_API_KEY='...' \
  BROWSERBASE_PROJECT_ID='...'
```

`APP_PASSWORD` is the gate. `COOKIE_DOMAIN` is what makes the session work
across the two hostnames — omit it and sign-in silently fails to stick.

Note the three that carry meaning of their own:

| Variable | Effect |
|---|---|
| `APP_PASSWORD` | Unset ⇒ **no gate at all**. Never leave it unset here. |
| `RATE_LIMIT_PER_HOUR` | Per-IP ceiling on money-spending routes. `0`/unset ⇒ unlimited. |
| `COOKIE_DOMAIN` | `.paraphoria.com` ⇒ session shared by site and API. |

Changing `APP_PASSWORD` later immediately signs everyone out, by design — the
cookie is signed with the password, so rotating it revokes every live session.

### Deploy

```bash
fly deploy
```

The build runs on Fly's remote builder, so Docker isn't needed locally. It takes
a while: torch and a headless Chromium are both large. Then check it:

```bash
curl https://p2-backend.fly.dev/api/health
```

Expect `{"status":"ok","accounts":{...}}`. Anything else — read `fly logs`; a
missing required key shows up as a startup crash, not a 500.

### Point the subdomain at it

```bash
fly certs add api.paraphoria.com
```

That prints the DNS records to create. In GoDaddy (paraphoria.com's nameservers
are `ns53/ns54.domaincontrol.com`), add:

- `CNAME` — host `api`, value `p2-backend.fly.dev`
- plus the `_acme-challenge` record `fly certs add` asks for

Then wait for the cert and verify against the real hostname:

```bash
fly certs show api.paraphoria.com
curl https://api.paraphoria.com/api/health
```

## 2. Frontend on Netlify

`frontend/netlify.toml` is already written. Create a **new** Netlify site — not
the one serving Fluid — from this repo, with base directory `frontend`. Netlify
reads the rest from the file: build command, publish directory, `VITE_BASE_PATH`
and `VITE_API_BASE`.

Try it as a draft first. This gives a preview URL, touches nothing live, and is
also what confirms the config resolves the way it reads:

```bash
npm i -g netlify-cli && netlify deploy --build
```

Open the preview URL. You should get the password screen; sign in and the app
should load. If assets 404, the `base`/`publish` pair resolved differently than
expected — that is precisely what this step is for.

Then publish:

```bash
netlify deploy --build --prod
```

## 3. Point p2.paraphoria.com at that site

In the Netlify site: **Domain management → Add a domain →**
`p2.paraphoria.com`. Netlify gives you a target hostname.

In GoDaddy, add one record:

- `CNAME` — host `p2`, value `<your-site>.netlify.app`

Netlify issues the certificate once DNS resolves. Then open
`https://p2.paraphoria.com` — password screen, sign in, app.

Fluid is untouched by all of this; `paraphoria.com` keeps serving exactly what
it serves today.

## Running it locally afterwards

Unchanged. With no `APP_PASSWORD` set the gate disables itself and
`VITE_API_BASE` defaults back to `http://127.0.0.1:8000`:

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000
cd frontend && npm run dev
```

## Known limits of this tier

- **One machine.** The rate limiter counts in memory, so `max_machines_running`
  is 1 in `fly.toml`. Raising it doubles the effective ceiling per instance;
  move the counter to shared storage first.
- **Cold starts.** `min_machines_running = 0` keeps idle cost near zero, so the
  first request after a quiet spell waits for a boot. The UI retries four times
  with increasing waits before reporting the backend as down.
- **The Lens cache is ephemeral.** `.cache/lens` lives in the container, so a
  restart clears it and the next lookups pay full price. Attach a Fly volume if
  that repetition starts to cost real money.
- **This is a shared password, not accounts.** Everyone with it draws on the
  same API budget and no usage is attributable to a person. Per-user quotas or
  billing would be a different piece of work.
