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
