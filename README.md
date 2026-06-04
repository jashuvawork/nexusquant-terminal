# NexusQuant Institutional Terminal

NexusQuant is a deployable institutional-style AI scalping terminal scaffold for Indian index options on NIFTY and SENSEX.

## AWS + Vercel deployment

For the current Railway backend deployment with Vercel frontend, see [`docs/RAILWAY_DEPLOYMENT.md`](docs/RAILWAY_DEPLOYMENT.md). For future AWS backend deployment, use ECS Fargate with an Application Load Balancer because the backend exposes WebSockets; see [`docs/AWS_DEPLOYMENT.md`](docs/AWS_DEPLOYMENT.md).

## Stack

- Frontend: React, TypeScript, Vite, TailwindCSS, Lightweight Charts, Recharts, Framer Motion, WebSocket client
- Backend: FastAPI, asyncio, WebSockets, Redis boundary, PostgreSQL boundary, XGBoost-ready AI scoring, Prometheus metrics, Docker
- Deployment: Vercel frontend, Railway backend now, AWS ECS/Fargate later, PostgreSQL, Redis, Prometheus/Grafana-ready metrics, GitHub Actions CI

## Modules

The terminal includes Execution HUD, Heatmap Terminal, Orderflow Analytics, AI Matrix, Greeks & IV, Strategy Router, Upstox Portfolio, Risk Engine, Infrastructure Telemetry, AI Analytics, Trade Journal, Session Intelligence, Backtesting, and Settings.

## Vercel build error: frontend directory

If Vercel fails with `cd: frontend: No such file or directory`, set Vercel Root Directory to `./` with output `frontend/dist`, or set Root Directory to `frontend` with build command `npm run build` and output `dist`.

## Local frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend connects to `VITE_WS_URL`. It does not show dummy prices; if the backend or Upstox is unavailable, it shows an explicit connection/configuration status.

## Local backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Useful endpoints:

- `GET /health`
- `GET /metrics`
- `GET /api/terminal/state`
- `GET /api/upstox/health`
- `WS /ws/market`

## Docker Compose

```bash
docker compose up --build
```

Services:

- Frontend: run separately with Vite or deploy to Vercel
- Backend: `http://localhost:8000`
- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`
- Prometheus: `http://localhost:9090`


## Simple deployment steps

### 1. Deploy the backend on Render

1. Push this repository to GitHub.
2. Open Render and choose **New +** -> **Blueprint**.
3. Select this repository. Render will read `render.yaml`.
4. Create the services:
   - `nexusquant-api` web service
   - `nexusquant-postgres` database
   - `nexusquant-redis` Redis service
5. Open the `nexusquant-api` service -> **Environment** and add:

```text
CORS_ORIGINS=https://your-vercel-domain.vercel.app,http://localhost:5173
UPSTOX_API_KEY=your_upstox_api_key
UPSTOX_API_SECRET=your_upstox_api_secret
UPSTOX_REDIRECT_URI=https://your-render-api.onrender.com/api/upstox/callback
AI_SCORE_THRESHOLD=76
SAFE_MODE_THRESHOLD=86
MAX_EXPOSURE_PCT=42
DAILY_DRAWDOWN_PCT=3
```

6. Replace `https://your-render-api.onrender.com` with the real Render backend URL.
7. In the Upstox developer console, set the same redirect URL:

```text
https://your-render-api.onrender.com/api/upstox/callback
```

8. Redeploy the Render backend.
9. Confirm the backend is live:

```text
https://your-render-api.onrender.com/health
```

### 2. Get the Upstox access token

Upstox requires a browser login and authorization-code exchange. The backend automates the exchange and stores the access token after you log in.

1. Open this URL in your browser:

```text
https://your-render-api.onrender.com/api/upstox/login-url
```

2. Copy the `loginUrl` value from the response and open it.
3. Log in to Upstox and approve access.
4. Upstox redirects to:

```text
https://your-render-api.onrender.com/api/upstox/callback?code=...
```

5. If successful, the page shows `Upstox access token stored successfully`.
6. Check token status:

```text
https://your-render-api.onrender.com/api/upstox/token/status
```

Expected response:

```json
{
  "configured": true,
  "hasToken": true,
  "expiresAt": "...",
  "tokenType": "Bearer"
}
```

### 3. Deploy the frontend on Vercel

1. Open Vercel and choose **Add New** -> **Project**.
2. Import the same GitHub repository.
3. Keep the root directory as the repository root. `vercel.json` already points to `frontend`.
4. Add environment variables in Vercel:

```text
VITE_API_URL=https://your-render-api.onrender.com
VITE_WS_URL=wss://your-render-api.onrender.com/ws/market
```

5. Deploy.
6. After Vercel gives you a frontend URL, go back to Render and update `CORS_ORIGINS`:

```text
CORS_ORIGINS=https://your-vercel-domain.vercel.app,http://localhost:5173
```

7. Redeploy Render once more.
8. Open the Vercel URL. The header should show **Backend stream live** when WebSocket is connected.

### 4. Important security note

Never commit broker secrets to GitHub. Add Upstox credentials only as Render environment variables. If credentials were shared in chat, rotate/regenerate them in the Upstox developer console before funding or live trading.

## Vercel

Use the repository root. `vercel.json` builds `frontend` and serves `frontend/dist`.

Set environment variables:

```text
VITE_API_URL=https://nexusquant-api.onrender.com
VITE_WS_URL=wss://nexusquant-api.onrender.com/ws/market
```

## Render

`render.yaml` defines the FastAPI Docker service plus PostgreSQL and Redis. Configure secrets in Render:

```text
CORS_ORIGINS=https://your-vercel-domain.vercel.app
UPSTOX_API_KEY=...
UPSTOX_API_SECRET=...
```


## Real Upstox data and trading setup

This version does not generate dummy market prices, fake heatmaps, random PnL, or local fallback ticks. The simulator module has been removed. If the backend cannot authenticate with Upstox, verify live funds, or fetch dynamic option expiry/chain data, the frontend blocks the snapshot and shows a setup/status panel instead of a simulated terminal.

### Required Render variables for real data

Set these on the Render backend service:

```text
UPSTOX_API_KEY=your_upstox_api_key
UPSTOX_API_SECRET=your_upstox_api_secret
UPSTOX_REDIRECT_URI=https://your-render-api.onrender.com/api/upstox/callback
PRIMARY_SYMBOL=NIFTY
NIFTY_INSTRUMENT_KEY=NSE_INDEX|Nifty 50
SENSEX_INSTRUMENT_KEY=BSE_INDEX|SENSEX
# Optional: leave blank to auto-select nearest expiry from Upstox option contracts
NIFTY_EXPIRY_DATE=
SENSEX_EXPIRY_DATE=
MARKET_POLL_SECONDS=1
ENABLE_LIVE_TRADING=false
AGGRESSIVE_MODE=false
```

`NIFTY_EXPIRY_DATE` and `SENSEX_EXPIRY_DATE` must be the active weekly/monthly option expiries you want to trade. Upstox option-chain API requires an expiry date, so the backend will not invent one.

### Upstox token flow

1. Deploy backend on Render.
2. Set the same redirect URL in the Upstox developer console:

```text
https://your-render-api.onrender.com/api/upstox/callback
```

3. Open:

```text
https://your-render-api.onrender.com/api/upstox/login-url
```

4. Open the returned `loginUrl`, log in to Upstox, and approve.
5. Verify token:

```text
https://your-render-api.onrender.com/api/upstox/token/status
```

Expected:

```json
{"configured": true, "hasToken": true}
```

### Market phases

The backend classifies Indian market time in IST:

- `PRE_MARKET_ANALYSIS`: 08:30-09:15, analysis only, no live scalps.
- `LIVE_MARKET`: 09:15-15:30, scalping can be considered if risk gates pass.
- `POST_MARKET_ANALYSIS`: 15:30-16:15, review only.
- `CLOSED_MARKET`: outside regular session or weekends, closed-market analysis from latest Upstox data only.

### Real data used

The terminal snapshot is built from:

- Upstox V3 LTP quotes
- Upstox V2 option chain, including market data and Greeks
- Upstox V3 intraday candles
- Upstox positions, orders, and funds where available
- Derived analytics from real bid/ask quantity, OI, volume, spreads, candles, and previous real poll state

REST snapshots cannot prove true exchange aggressor side like a colocated tick/order feed. Therefore orderflow metrics are labelled as Upstox-derived from real depth, LTP, volume and OI changes rather than fabricated institutional tape.

### Analysis-only mode

Default production mode is analysis only:

```text
ENABLE_LIVE_TRADING=false
AGGRESSIVE_MODE=false
```

The system streams real market analysis but blocks order placement.

### Aggressive scalping mode

Only after you confirm broker permissions, exchange approvals, lot size, freeze quantity, capital limits, and risk settings, enable:

```text
ENABLE_LIVE_TRADING=true
AGGRESSIVE_MODE=true
```

Live orders use:

```http
POST /api/execution/scalp-order
```

Example body:

```json
{
  "instrument_token": "NSE_FO|12345",
  "quantity": 75,
  "transaction_type": "BUY",
  "order_type": "LIMIT",
  "price": 150.5,
  "market_protection": 0,
  "tag": "nexusquant-scalp"
}
```

The backend blocks orders when:

- `ENABLE_LIVE_TRADING=false`
- market phase is not `LIVE_MARKET`
- Upstox token is missing
- risk gates fail
- a MARKET order has no market protection

Keep `ENABLE_LIVE_TRADING=false` until you have tested with small quantity and confirmed all Upstox responses in production.

## Production integration notes

The Upstox adapter is intentionally isolated in `backend/app/services/upstox_client.py`. It calls real Upstox endpoints for funds, positions, orders, option contracts, option chain, quotes, candles, and guarded order placement.

The execution pipeline is represented as:

1. Infrastructure telemetry and failsafe validation
2. Session intelligence and regime classification
3. Heatmap and liquidity sweep analysis
4. Multi-engine AI scoring
5. Option chain, Greeks, and gamma confirmation
6. Adaptive sizing and smart routing
7. Execution quality monitoring
8. Adaptive trailing engine
9. AI learning and analytics storage

This scaffold does not place live orders by default. Keep execution disabled until broker credentials, exchange approvals, audit logging, and risk limits are validated.


## Upstox token options

Upstox authorization codes cannot be generated from environment variables. Upstox requires browser login and approval to create an authorization code.

Recommended flow:

```text
/api/upstox/login-url -> Upstox login -> /api/upstox/callback -> token stored in Redis
```

Optional temporary override:

```text
UPSTOX_ACCESS_TOKEN=your_valid_access_token
```

Use this only if you already have a valid Upstox access token. When Upstox expires it, repeat login or replace the variable.

## Auto-trading stop controls

The backend includes a Redis-backed kill switch:

```text
GET  /api/execution/status
POST /api/execution/stop
POST /api/execution/resume
```

The frontend header has **Stop Auto** and **Resume** buttons. Live order placement is blocked whenever `autoTradingStopped=true`, even if `ENABLE_LIVE_TRADING=true`.


## Funds V3/V2 fallback and closed-market data

NexusQuant now fetches funds with Upstox V3 first:

```text
GET /v3/user/get-funds-and-margin
```

If V3 fails, it falls back to V2:

```text
GET /v2/user/get-funds-and-margin
```

If both funds calls fail, the terminal does not invent capital. It shows funds as unavailable and continues market analysis only from real Upstox market data.

Closed-market and pre-market analysis uses real Upstox data where available:

- `/v3/market-quote/ltp` for last price, last quantity, volume, and previous close (`cp`)
- `/v2/option/contract` for dynamic expiries
- `/v2/option/chain` for option market data and Greeks
- `/v3/historical-candle/intraday` for last intraday candles

Use these checks after deployment:

```text
/api/upstox/account-summary
/api/market/expiries/NIFTY
/api/market/snapshot/NIFTY
```


## Auto-off backtest and suggestion mode

When auto trading is off:

```text
ENABLE_LIVE_TRADING=false
```

NexusQuant does not place orders. It uses real Upstox data to:

- backtest recent intraday candles
- score the current option-chain setup
- generate suggested trades for tomorrow/pre-market planning
- show entry rules and invalidation rules

When auto trading is on:

```text
ENABLE_LIVE_TRADING=true
```

The same signal can become `AUTO_EXECUTION_READY` only if all gates pass:

- Indian market session is live
- Upstox token and market data are valid
- dynamic expiry and option-chain data are available
- TQS/risk/spread gates pass
- manual STOP is not active

The STOP button always overrides auto trading.


## Dual-symbol background analysis

The frontend has a NIFTY/SENSEX display selector. This selector only changes what you see on screen.

The backend analyzes both symbols every WebSocket cycle:

```text
NIFTY
SENSEX
```

Debug endpoint:

```text
/api/market/snapshots
```

The response includes:

- `snapshots.NIFTY`
- `snapshots.SENSEX`
- `executionCandidates` from both symbols
- per-symbol errors if one symbol temporarily fails

Auto execution gates continue to apply per candidate: live market, risk, spread, TQS, capital, and STOP state.


## Adaptive aggression profiles

Set the active profile in Railway:

```text
AGGRESSION_PROFILE=realistic_aggressive
```

Supported values:

- `safe_beginner`
- `balanced_pro`
- `aggressive_scalping`
- `extreme_prop`
- `realistic_aggressive`

The profile engine automatically adjusts minimum TQS, safe-mode TQS, max exposure and cooldown by market session:

- Open drive 09:15-10:30 IST: lower TQS and shorter cooldown for valid momentum
- Midday chop 11:30-13:30 IST: higher TQS, longer cooldown and lower exposure
- Closing momentum 14:30-15:15 IST: moderate TQS/cooldown for continuation
- Closed/pre-market: analysis/backtest only

Backtest targets used in the terminal:

- 300+ trades minimum meaningful test
- 500-1000 trades professional sample
- target win rate 58-68%
- target profit factor 1.8-2.5
- max drawdown goal under 8%


## Microstructure hardening

The terminal now handles zero-volume Upstox candle responses with a real-data fallback chain:

1. Upstox intraday candle volume
2. Upstox option-chain CE/PE volume
3. Upstox LTP quote volume

The snapshot exposes:

```text
qualityFilters.volumeState
qualityFilters.chopFilter
infra.upstoxLatencyMs
```

Chop filter blocks execution candidates when multiple weak conditions appear:

- breakout velocity weak
- delta velocity weak
- sweep absent
- spread quality weak
- volume confirmation weak
- reversal/chop regime

This keeps auto-trading off during weak market structure while still allowing backtesting and suggestions.
