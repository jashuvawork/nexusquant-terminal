# Deploy NexusQuant Backend on Railway and Frontend on Vercel

Use this for the current deployment target:

- Backend: Railway
- Frontend: Vercel
- Database: Railway PostgreSQL
- Redis/token cache: Railway Redis
- Future AWS migration: see `docs/AWS_DEPLOYMENT.md`

The backend uses WebSockets at `/ws/market`. Railway supports public web services and dynamic ports; the Dockerfile now binds to Railway's `$PORT`.

## 1. Deploy backend on Railway

### Step 1: Create Railway project

1. Open Railway.
2. Click **New Project**.
3. Choose **Deploy from GitHub repo**.
4. Select this repository.

### Step 2: Configure backend service root

Because this is a monorepo, set the backend service root directory to:

```text
/backend
```

Railway should detect:

```text
backend/Dockerfile
backend/railway.json
```

If Railway asks for config file path, use:

```text
/backend/railway.json
```

If Railway asks for Dockerfile path, use:

```text
Dockerfile
```

or from repo root:

```text
/backend/Dockerfile
```

### Step 3: Add PostgreSQL

In the same Railway project:

1. Click **New**.
2. Choose **Database**.
3. Choose **PostgreSQL**.
4. Name it something clear, for example:

```text
Postgres
```

Railway automatically creates a `DATABASE_URL` variable on the Postgres service.

### Step 4: Add Redis

In the same Railway project:

1. Click **New**.
2. Choose **Database**.
3. Choose **Redis**.
4. Name it something clear, for example:

```text
Redis
```

Railway automatically creates a `REDIS_URL` variable on the Redis service.

### Step 5: Add backend variables

Open the backend service -> **Variables**.

Add these variables:

```text
ENVIRONMENT=production
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
UPSTOX_API_KEY=your_upstox_api_key
UPSTOX_API_SECRET=your_upstox_api_secret
UPSTOX_REDIRECT_URI=https://${{RAILWAY_PUBLIC_DOMAIN}}/api/upstox/callback
# Optional temporary token override. Leave blank unless you manually paste a valid Upstox access token.
UPSTOX_ACCESS_TOKEN=
PRIMARY_SYMBOL=NIFTY
NIFTY_INSTRUMENT_KEY=NSE_INDEX|Nifty 50
SENSEX_INSTRUMENT_KEY=BSE_INDEX|SENSEX
# Optional: leave blank to auto-select nearest expiry from Upstox option contracts
NIFTY_EXPIRY_DATE=
SENSEX_EXPIRY_DATE=
MARKET_POLL_SECONDS=1
ENABLE_LIVE_TRADING=false
AGGRESSIVE_MODE=false
AI_SCORE_THRESHOLD=76
SAFE_MODE_THRESHOLD=86
MAX_EXPOSURE_PCT=42
DAILY_DRAWDOWN_PCT=3
```

If your Railway database service names are different, change references accordingly:

```text
DATABASE_URL=${{YourPostgresServiceName.DATABASE_URL}}
REDIS_URL=${{YourRedisServiceName.REDIS_URL}}
```

Important: keep broker secrets only in Railway backend variables. Do not add Upstox secrets to Vercel.

### Step 6: Generate public backend domain

Open backend service -> **Settings** -> **Networking**.

Click:

```text
Generate Domain
```

Railway gives a URL like:

```text
https://nexusquant-api-production.up.railway.app
```

This is your backend URL.

### Step 7: Update CORS variable

After you deploy Vercel, set this to your real Vercel domain. For first backend test, you can use:

```text
CORS_ORIGINS=http://localhost:5173
```

After Vercel deploys, update it to:

```text
CORS_ORIGINS=https://your-vercel-domain.vercel.app,http://localhost:5173
```

### Step 8: Deploy backend

Railway should deploy automatically after variable changes. If not, click:

```text
Deploy
```

Check backend health:

```text
https://your-railway-domain.up.railway.app/health
```

Expected:

```json
{
  "status": "ok",
  "upstoxConfigured": true,
  "upstoxTokenPresent": false
}
```

## 2. Configure Upstox token on Railway backend

### Step 1: Set Upstox redirect URL

In Upstox developer console, set redirect URL exactly:

```text
https://your-railway-domain.up.railway.app/api/upstox/callback
```

This must match `UPSTOX_REDIRECT_URI`.

### Step 2: Get login URL

Open:

```text
https://your-railway-domain.up.railway.app/api/upstox/login-url
```

Copy the `loginUrl` from the response and open it.

### Step 3: Login and approve

1. Login to Upstox.
2. Approve access.
3. Upstox redirects back to Railway.
4. Backend stores token in Redis.

Check token:

```text
https://your-railway-domain.up.railway.app/api/upstox/token/status
```

Expected:

```json
{
  "configured": true,
  "hasToken": true
}
```

## 3. Test real Upstox connection, funds and expiries

Check backend health:

```text
https://your-railway-domain.up.railway.app/health
```

Check token:

```text
https://your-railway-domain.up.railway.app/api/upstox/token/status
```

Check live Upstox account funds, positions and orders:

```text
https://your-railway-domain.up.railway.app/api/upstox/account-summary
```

Check dynamic expiry discovery from Upstox option contracts:

```text
https://your-railway-domain.up.railway.app/api/market/expiries/NIFTY
https://your-railway-domain.up.railway.app/api/market/expiries/SENSEX
```

Test full closed-market/live snapshot. This includes selected expiry, available funds, pre-market/closed-market analysis and tomorrow candidate plan:

```text
https://your-railway-domain.up.railway.app/api/market/snapshot/NIFTY
https://your-railway-domain.up.railway.app/api/market/snapshot/SENSEX
```

`NIFTY_EXPIRY_DATE` and `SENSEX_EXPIRY_DATE` are optional. If you leave them blank, NexusQuant selects the nearest available expiry returned by Upstox `/v2/option/contract`. If you set them and Upstox does not return that expiry, the backend warns and falls back to the nearest available expiry.

## 4. Deploy frontend on Vercel

### Step 1: Import project

1. Open Vercel.
2. Click **Add New** -> **Project**.
3. Import the same GitHub repository.
4. Use one of these two valid configurations:

**Option A, recommended: repo root**

```text
Root Directory: ./
Build Command: leave default or use npm --prefix frontend install && npm --prefix frontend run build
Output Directory: frontend/dist
```

**Option B: frontend folder**

```text
Root Directory: frontend
Build Command: npm run build
Output Directory: dist
```

Do not use `cd frontend && ...` when Root Directory is already `frontend`; that causes `cd: frontend: No such file or directory`.

### Step 2: Add Vercel environment variables

```text
VITE_API_URL=https://your-railway-domain.up.railway.app
VITE_WS_URL=wss://your-railway-domain.up.railway.app/ws/market
```

Use:

- `https://` for API URL
- `wss://` for WebSocket URL

### Step 3: Deploy

Click **Deploy**.

Vercel gives a URL like:

```text
https://nexusquant-terminal.vercel.app
```

## 5. Final backend CORS update

Go back to Railway backend service -> **Variables**.

Set:

```text
CORS_ORIGINS=https://your-vercel-domain.vercel.app,http://localhost:5173
```

Redeploy Railway backend.

## 6. Open frontend

Open:

```text
https://your-vercel-domain.vercel.app
```

If connected, the header should show:

```text
Real Upstox stream live
```

If not connected, frontend shows the exact status:

```text
UPSTOX_AUTH_REQUIRED
CONFIGURATION_REQUIRED
UPSTOX_DATA_ERROR
BACKEND_WS_ERROR
```

## 7. Live trading mode

Start safely:

```text
ENABLE_LIVE_TRADING=false
AGGRESSIVE_MODE=false
```

This gives real Upstox analysis only and blocks live order placement.

Only after verifying broker token, data, expiry, lot size, risk, and small quantity testing, change Railway variables:

```text
ENABLE_LIVE_TRADING=true
AGGRESSIVE_MODE=true
```

Then redeploy backend.

## 8. Future AWS migration

When you move to AWS later, keep frontend Vercel variables the same pattern, just replace Railway domain with AWS ALB/custom backend domain:

```text
VITE_API_URL=https://api.yourdomain.com
VITE_WS_URL=wss://api.yourdomain.com/ws/market
```

See:

```text
docs/AWS_DEPLOYMENT.md
```

## No dummy-data guard

The frontend blocks any snapshot that does not include:

```text
dataSource=UPSTOX_REALTIME_REST
upstoxConnection.connected=true
portfolio.fundsSource=upstox
expiryState.selectedExpiry
```

If you see `NON_UPSTOX_SNAPSHOT_BLOCKED`, Railway is probably running an old backend commit or a non-Upstox response. Redeploy Railway and Vercel using the latest commit.


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


## Paper trading, replay and learning layer

Recommended production variables before live auto execution:

```text
PAPER_TRADING=true
AI_LEARNING_ENABLED=true
PAPER_TARGET_POINTS=5
PAPER_STOP_POINTS=3
MAX_PAPER_TRADE_SECONDS=180
MIN_REQUIRED_MOVE_POINTS=5
```

When `PAPER_TRADING=true`, NexusQuant records every valid signal as a shadow trade instead of sending a broker order. It stores:

- signal generated
- risk checked
- paper opened
- exited
- entry/exit premium
- slippage estimate
- spread cost
- TQS
- reason for entry/exit
- profit/loss

The replay buffer stores compact market snapshots for debugging and backtesting.

Useful endpoints:

```text
/api/auto-trader/status
/api/auto-trader/replay
/api/auto-trader/daily-report
/api/market/snapshots
```

The online learning tracker updates every snapshot tick from real-data-derived signals and paper outcomes. It is an online calibration layer, not a fully persisted offline ML retrain yet.


## Final production health checklist

After each Railway deploy, verify:

```text
/api/deployment/status
/api/upstox/token/status
/api/execution/status
/api/capital
/api/auto-trader/status
/api/market/snapshots
```

If `hasToken=false`, run:

```text
/api/upstox/login-url
```

If paper/replay state looks stale before a new test session, reset it:

```text
POST /api/auto-trader/reset
```

or open in browser:

```text
/api/auto-trader/reset
```

A healthy market-data run requires:

- `upstoxTokenPresent=true`
- `runtimeValidation.ok=true`
- `/api/market/snapshots` returning both NIFTY and SENSEX or a clear per-symbol Upstox error
- STOP state intentionally set
- trading capital configured before sizing tests


## Precision execution engines

NexusQuant now emits these explainability and protection layers on every real-data snapshot:

```text
pressureMode
precisionChecklist
adaptiveExit
noTradeZones
tqsBreakdown
```

- `pressureMode`: NORMAL / ELEVATED / CRITICAL with operator actions.
- `precisionChecklist`: critical gate checklist for entry precision.
- `adaptiveExit`: target, stop, trailing, partial exit, and active exit rules.
- `noTradeZones`: explicit hard/soft blocks such as closed market, chop, latency, spread, volume and drawdown.
- `tqsBreakdown`: weighted engine contribution and weak components explaining the final score.

Live auto execution is blocked if:

- pressure is CRITICAL
- precision checklist fails
- any hard no-trade zone is active
- chop filter blocks
- STOP is active
- risk/capital/session gates fail


## Pretrained continuous AI learning

The backend starts with an institutional prior model:

```text
institutional-prior-v1
```

This prior is not a claim of historical profitability. It seeds sensible initial weights for:

- delta engine
- momentum engine
- heatmap engine
- volume engine
- regime engine
- spread analysis
- option-chain bias
- gamma positioning
- IV expansion
- market profile alignment

Then it continuously updates in the backend from:

- every real-data snapshot tick
- paper/shadow trade outcomes
- future live outcomes when live trading is enabled

Endpoints:

```text
/api/ai-learning/status
/api/ai-learning/export
/api/ai-learning/reset
```

Recommended variable:

```text
AI_LEARNING_ENABLED=true
```

Use `/api/ai-learning/export` before resetting if you want to inspect current calibration state.


## Historical 1000-trade AI training

After Upstox token is available, train from real historical index candles:

```text
/api/ai-learning/train-historical?symbol=NIFTY&target_trades=1000
/api/ai-learning/train-historical?symbol=SENSEX&target_trades=1000
```

Optional date range:

```text
/api/ai-learning/train-historical?symbol=NIFTY&target_trades=1000&from_date=2025-01-01&to_date=2025-06-01
```

This endpoint:

- fetches real Upstox historical candles
- generates deterministic breakout/momentum scalp samples
- labels sample outcomes from future candle movement
- updates the persistent continuous AI learner

If Upstox returns fewer candles/samples than requested, the response shows `generatedTrades` and `enoughSamples=false`. No fake historical trades are created.


## Profit target lock

Configure target tiers against your trading capital:

```text
PROFIT_TARGET_PRIMARY_PCT=33
PROFIT_TARGET_SECONDARY_PCT=22
PROFIT_TARGET_FALLBACK_PCT=11
PROFIT_LOCK_RETAIN_PCT=100
```

When a tier is achieved in paper/live tracked outcomes, NexusQuant reports the locked profit and blocks new live orders if the locked profit would be at risk. This is a risk-control guard, not a profit guarantee.

Endpoint:

```text
/api/auto-trader/profit-lock
```


## Production readiness gates

Full-capital live auto-trading is blocked unless these readiness checks pass:

- sample size >= 500 candles/trades
- profit factor >= 1.5
- win rate >= 58%
- TQS >= 68
- real/effective volume > 0
- drawdown < 5%

If checks are weak, NexusQuant remains in paper/shadow or small-live recommendation mode. For INR 5L capital, use paper/shadow until readiness improves.


## Immediate AI training trigger

After Upstox token is active, trigger both-index training immediately:

```text
/api/ai-learning/train-now?target_trades=1000
```

This attempts NIFTY and SENSEX historical candle training. If token/history is unavailable, the response returns a clear per-symbol error.


## Institutional event journal

The backend permanently records execution-framework events to PostgreSQL when available, with memory fallback:

- `SIGNAL`
- `ENTRY`
- `EXIT`
- `REJECTION`
- `RISK_GATE`
- `EXIT_RULE`
- `API_ERROR`
- `LATENCY_SPIKE`

Endpoint:

```text
/api/event-journal/recent
```

The WebSocket stream also includes recent events as `eventJournal`, which powers the frontend Trade Journal.


## HTTP polling stream fallback

The frontend now uses `/api/market/snapshots` as the primary continuous stream instead of relying on browser WebSockets. This avoids repeated Railway/Vercel/mobile WebSocket open/close loops.

Variables:

```text
VITE_API_URL=https://nexusquant-api-production.up.railway.app
VITE_POLL_MS=3000
```

`VITE_WS_URL` can remain set, but the current frontend stream uses HTTP polling for stability.


## WebSocket primary with polling fallback

For production scalping telemetry, frontend uses WebSocket as primary transport and sends client heartbeats. If WebSocket repeatedly fails, it falls back to HTTP polling.

Vercel variables:

```text
VITE_STREAM_MODE=polling
VITE_WS_URL=wss://nexusquant-api-production.up.railway.app/ws/market
VITE_WS_CLIENT_HEARTBEAT_MS=5000
VITE_POLL_MS=3000
```

Options:

- `VITE_STREAM_MODE=websocket`: primary WebSocket, fallback after repeated failures
- `VITE_STREAM_MODE=polling`: force HTTP polling only
- `VITE_STREAM_MODE=hybrid`: try WebSocket once, fallback quickly


## Railway transport rule

Railway is currently closing browser WebSockets quickly in this deployment. For Railway, the frontend forces HTTP polling by default when `VITE_API_URL` contains `.up.railway.app`.

Use these Vercel variables for Railway:

```text
VITE_STREAM_MODE=polling
VITE_FORCE_WEBSOCKET=false
VITE_POLL_MS=3000
```

WebSocket can be re-enabled later for AWS/ALB by setting:

```text
VITE_FORCE_WEBSOCKET=true
VITE_STREAM_MODE=websocket
```


## Strategy optimizer engine

Run a parameter grid search over real Upstox historical candles:

```text
/api/strategy-optimizer/run?symbol=NIFTY&target_samples=1000
/api/strategy-optimizer/run?symbol=SENSEX&target_samples=1000
/api/strategy-optimizer/run-both?target_samples=1000
```

It tests combinations of:

- minimum TQS
- breakout ATR strength
- volume multiplier
- target points
- stop points
- trailing ATR

It returns:

- best balanced settings
- best profit-factor settings
- best win-rate settings
- best drawdown settings
- top 10 parameter sets

Use optimizer output to set different NIFTY/SENSEX rules instead of relying on one profile for both indexes.


## Optimizer objective modes

Use objective modes to search different strategy styles:

```text
/api/strategy-optimizer/run-both?target_samples=1000&objective=balanced
/api/strategy-optimizer/run-both?target_samples=1000&objective=profit_factor
/api/strategy-optimizer/run-both?target_samples=1000&objective=win_rate
/api/strategy-optimizer/run-both?target_samples=1000&objective=low_drawdown
/api/strategy-optimizer/run-both?target_samples=1000&objective=high_win_scalp
```

`high_win_scalp` prioritizes win rate but penalizes profit factor below 1.5, too few trades, and high drawdown.

The optimizer now returns:

- `recommendedProfiles.runnerProfile`
- `recommendedProfiles.highWinScalpProfile`
- `recommendedProfiles.lowDrawdownProfile`


## Retest confirmation entry model

NexusQuant now evaluates:

- breakout-now
- breakout → retest → hold
- opening-range breakout/retest
- failed-breakout rejection

The optimizer includes `entry_model` in parameters:

```text
breakout
retest
orb_retest
```

Use retest/ORB profiles to reduce fake breakouts and improve win rate.


## Stored optimized profiles

Current stored optimizer profiles:

```text
NIFTY_OPT_MIN_TQS=72
NIFTY_OPT_BREAKOUT_ATR=0.35
NIFTY_OPT_VOLUME_MULTIPLIER=2.0
NIFTY_OPT_TARGET_POINTS=4
NIFTY_OPT_STOP_POINTS=2.5
NIFTY_OPT_TRAIL_ATR=0.75
NIFTY_OPT_ENTRY_MODEL=breakout

SENSEX_OPT_MIN_TQS=68
SENSEX_OPT_BREAKOUT_ATR=0.35
SENSEX_OPT_VOLUME_MULTIPLIER=1.3
SENSEX_OPT_TARGET_POINTS=6
SENSEX_OPT_STOP_POINTS=2.5
SENSEX_OPT_TRAIL_ATR=0.75
SENSEX_OPT_ENTRY_MODEL=breakout
```

Endpoint:

```text
/api/risk/optimized-profiles
```


## Symbol-specific execution styles

Stored optimized profiles now drive different execution behavior per index:

- `NIFTY`: `HIGH_WIN_SCALP` with faster capture, larger partial exit, smaller runner.
- `SENSEX`: `RUNNER_BREAKOUT` with bigger target, longer trail, larger runner portion.

These styles are included in `optimizedProfile` and `adaptiveExit` inside market snapshots.


## Paper execution independent of live mode

Paper/shadow trading can run regardless of live auto-trading flags:

```text
PAPER_TRADING=true
SHADOW_TRADE_ALL_SIGNALS=true
PAPER_TRADING_RESPECTS_STOP=false
```

This means:

- live broker orders still require all execution gates
- paper trades open for learning/visibility even when live execution is off or blocked
- rejected signals are still recorded as shadow paper candidates when enabled
- STOP can optionally pause paper trades if `PAPER_TRADING_RESPECTS_STOP=true`


## Paper Trading interface

The frontend includes a dedicated Paper Trading module showing:

- paper mode status
- shadow-all-signals mode
- open paper trades
- closed paper trades
- paper PnL
- lifecycle events
- replay buffer count
- AI learning samples
- profit lock state

Useful endpoints:

```text
/api/auto-trader/status
/api/auto-trader/reset
```


## Explosive Runner Engine

The Explosive Runner Engine hunts rare premium-expansion moves such as 30%+, 50%+ and 100%+ option premium runs.

Required live data:

- option premium LTP
- option-chain volume/OI
- bid/ask spread
- Greeks delta/gamma/theta/vega
- IV expansion
- underlying momentum
- market profile/opening range

Ideal data still missing for exact historical runner training:

- historical option premium candles
- tick-level option trades
- level-2 DOM depth
- aggressor side
- multi-strike gamma exposure history

Runner training endpoints:

```text
/api/ai-learning/train-runner?symbol=NIFTY&target_trades=1000
/api/ai-learning/train-runner?symbol=SENSEX&target_trades=1000
/api/ai-learning/train-runner-both?target_trades=1000
```

Until historical option premium candles are available, runner training is marked as proxy-based from real Upstox index candles.


## News/event intelligence

NexusQuant uses Upstox News API when available:

```text
/api/market/news/NIFTY
/api/market/news/SENSEX
```

The news layer can:

- raise TQS requirements during negative/high-risk events
- block fresh trades during high-risk negative news
- support runner bias during positive non-critical news

If Upstox news entitlement or endpoint is unavailable, snapshots continue and show `news unavailable` without breaking market data.
