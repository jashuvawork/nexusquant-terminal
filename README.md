# NexusQuant Institutional Terminal

NexusQuant is a deployable institutional-style AI scalping terminal scaffold for Indian index options on NIFTY and SENSEX.

## Stack

- Frontend: React, TypeScript, Vite, TailwindCSS, Lightweight Charts, Recharts, Framer Motion, WebSocket client
- Backend: FastAPI, asyncio, WebSockets, Redis boundary, PostgreSQL boundary, XGBoost-ready AI scoring, Prometheus metrics, Docker
- Deployment: Vercel frontend, Render backend, PostgreSQL, Redis, Prometheus/Grafana-ready metrics, GitHub Actions CI

## Modules

The terminal includes Execution HUD, Heatmap Terminal, Orderflow Analytics, AI Matrix, Greeks & IV, Strategy Router, Upstox Portfolio, Risk Engine, Infrastructure Telemetry, AI Analytics, Trade Journal, Session Intelligence, Backtesting, and Settings.

## Local frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend connects to `VITE_WS_URL`. If the backend is unavailable, it automatically runs a local simulated one-second market stream.

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

## Production integration notes

The Upstox adapter is intentionally isolated in `backend/app/services/upstox_client.py`. Replace the mock methods with MarketDataStreamerV3, option chain APIs, order APIs, funds APIs, and positions APIs once broker credentials and order permissions are available.

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
