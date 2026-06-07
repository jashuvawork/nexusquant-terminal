# NexusQuant Website and AI System Guide

This document explains what the NexusQuant terminal is, how the website works, how the backend generates AI trading intelligence, how paper trading works, and how the AWS deployment should be operated.

The current production design is:

```text
Browser / Vercel frontend
  -> https://api.nexusquant.uk
  -> AWS Application Load Balancer
  -> EC2 Docker backend on port 8000
  -> Upstox APIs, Redis, PostgreSQL, Finnhub
```

Live broker execution must remain disabled during the paper-trading test week:

```env
ENABLE_LIVE_TRADING=false
PAPER_TRADING=true
```

## 1. What the website is

NexusQuant is an institutional-style Indian index options scalping terminal for:

- NIFTY
- SENSEX

It is designed to show no fake/random market prices. If Upstox or the backend is unavailable, the website displays a waiting/error state instead of dummy values.

The terminal focuses on:

- real Upstox index and option-chain data
- option premium LTP monitoring
- AI Trade Quality Score, called TQS
- premarket/open-drive planning
- explosive runner detection
- paper/shadow trading
- risk controls
- readiness checks before any live deployment

## 2. Main website modules

The sidebar modules are defined in `frontend/src/components/navItems.ts`.

### 2.1 Execution HUD

Purpose:

- Shows the current trading condition.
- Shows whether the setup is only paper, analysis-only, or execution-ready.
- Shows current TQS, premium focus zone, regime, latency, and trade status.

Example:

```text
NIFTY spot: 23250
ATM strike: 23250
Premium focus: 23250 CALL
Trade mode: PAPER_EXECUTION
TQS: 78
```

How it helps AI:

- Gives a one-screen view of whether all engines agree.
- Helps verify that the AI is not acting on only one signal.

### 2.2 Heatmap Terminal

Purpose:

- Shows nearby option strikes.
- Highlights liquidity, OI/gamma walls, sweep risk, and absorption.

Example:

```text
23200 CALL: high liquidity
23250 CALL: gamma wall
23300 PUT: thin liquidity
```

How it helps AI:

- Avoids trades into poor liquidity.
- Helps identify strikes where option premium can expand quickly.

### 2.3 Orderflow Analytics

Purpose:

- Tracks orderflow-derived state such as:
  - cumulative delta
  - delta velocity
  - aggressive buyers/sellers
  - liquidity shift
  - sweep detection
  - volume acceleration
  - breakout velocity

Example:

```text
breakoutVelocity: 72
deltaVelocity: 65
volumeAcceleration: 80
```

How it helps AI:

- Confirms whether the move is active now.
- Prevents taking stale chart setups after the move is over.

### 2.4 AI Matrix

Purpose:

- Breaks the TQS into weighted components:
  - delta engine
  - momentum engine
  - heatmap engine
  - volume engine
  - regime engine
  - spread analysis
  - option-chain bias
  - gamma positioning
  - IV expansion
  - market-profile alignment

Example:

```text
Momentum Engine: 82
Volume Engine: 76
Spread Analysis: 90
TQS: 79
```

How it helps AI:

- TQS is not a black box.
- Weak components show why a trade is skipped.

### 2.5 Greeks and IV

Purpose:

- Shows option Greek context:
  - delta
  - gamma
  - theta
  - vega
  - IV rank
  - IV expansion

How it helps AI:

- Explosive trades usually need responsive delta/gamma and supportive IV.
- Helps avoid options where theta/spread risk is too high.

### 2.6 Strategy Router

Purpose:

- Shows the active strategy route:
  - controlled scalp
  - aggressive momentum scalp
  - runner breakout
  - safe mode

It also shows:

- optimized profile
- explosive runner engine
- second-by-second runner watchlist
- suggested trades
- trading capital controls

Example:

```text
Strategy: Aggressive momentum scalp
Runner watchlist:
  NIFTY 23250 CALL score 78 HIGH
  NIFTY 23300 CALL score 61 MEDIUM
```

How it helps AI:

- Converts raw market data into action candidates.
- Paper trading uses these candidates to open/close shadow trades.

### 2.7 Upstox Portfolio

Purpose:

- Shows broker account information:
  - funds
  - margin
  - positions
  - orders

Important:

Upstox funds API has service-hour restrictions. If outside service hours, it can return:

```text
Funds service is accessible from 5:30 AM to 12:00 AM IST
```

How it helps AI:

- Live mode can check actual account capital.
- During paper mode, funds can be unavailable while analysis continues.

### 2.8 Risk Engine

Purpose:

- Shows safe mode, drawdown, exposure, latency, stale data, spread widening, and cooldown.

Key live safety rule:

```env
ENABLE_LIVE_TRADING=false
```

This blocks real broker order placement.

How it helps AI:

- Prevents trades when risk is too high.
- Explains why a signal was rejected.

### 2.9 Infrastructure Telemetry

Purpose:

- Shows backend/broker health and latency:
  - broker health
  - websocket/polling latency
  - Upstox latency
  - Redis/Postgres health

How it helps AI:

- High latency can invalidate scalping entries.
- Helps decide if only observation/paper mode is safe.

### 2.10 AI Analytics

Purpose:

- Shows continuous learning state and historical/paper samples.

How it helps AI:

- Tracks how paper trades perform over time.
- Supports a week-long paper evaluation before live mode.

### 2.11 Trade Journal

Purpose:

- Shows signals, entries, exits, rejects, risk gates, and latency events.

Example event:

```text
SIGNAL: NIFTY 23250 CALL TQS 78
REJECTION: precision checklist failed
ENTRY: Paper trade entered
EXIT: Paper trade closed
```

How it helps AI:

- Gives a replayable audit trail.
- Shows which signals worked or failed.

### 2.12 Session Intelligence

Purpose:

- Shows market phase:
  - premarket
  - live market
  - post-market
  - closed market

How it helps AI:

- Premarket builds levels and bias.
- At open, the system watches for instant paper trades.

### 2.13 Backtesting

Purpose:

- Shows recent candle-based metrics:
  - sample count
  - win rate proxy
  - profit factor proxy
  - volume
  - TQS

How it helps AI:

- Checks if current conditions are statistically acceptable.
- Helps decide whether to stay in paper mode.

### 2.14 Paper Trading

Purpose:

- Opens paper trades based on real-data signals.
- Does not place real Upstox orders when live trading is disabled.

Paper trade lifecycle:

```text
SIGNAL_GENERATED
RISK_CHECKED
PAPER_OPENED
EXITED
```

How it helps AI:

- Lets the system prove itself for a full week.
- Tracks win rate, profit factor, PnL, losses, and reasons.

## 3. How the backend works

Backend framework:

```text
FastAPI + asyncio + Docker
```

Main file:

```text
backend/app/main.py
```

Main route file:

```text
backend/app/api/routes.py
```

Main AI snapshot engine:

```text
backend/app/services/realtime_engine.py
```

Main paper-trading engine:

```text
backend/app/services/auto_trader.py
```

## 4. Data flow

Every market snapshot does roughly this:

```text
1. Resolve expiry from Upstox option contracts
2. Fetch option chain
3. Fetch 1-minute candles
4. Fetch index LTP
5. Fetch optional funds/positions/orders
6. Fetch Finnhub news
7. Build heatmap, greeks, orderflow, profile, bias
8. Score AI/TQS
9. Evaluate risk/no-trade zones
10. Scan explosive runner candidates
11. Produce suggested trades
12. Feed candidates to paper trading
13. Return payload to frontend
```

Endpoint:

```text
GET /api/market/snapshots
```

Example response fields:

```json
{
  "type": "multi_snapshot",
  "snapshots": {
    "NIFTY": {
      "tradeQualityScore": 78,
      "explosiveRunner": {
        "candidate": true,
        "score": 76,
        "side": "CALL",
        "strike": 23250
      }
    }
  },
  "autoTrader": {
    "paperTrading": true,
    "openPaperTrades": []
  }
}
```

## 5. AI engines explained

### 5.1 TQS - Trade Quality Score

TQS combines multiple engines into one score.

Example:

```text
TQS 80+ = strong
TQS 70-79 = watch/tradable in paper
TQS below 68 = usually weak
```

TQS is used by:

- suggested trades
- paper trading
- risk engine
- readiness score
- open-drive logic

### 5.2 Explosive Runner Engine

Purpose:

Catch rare option premium expansion moves, especially near market open.

It scans:

```text
NIFTY and SENSEX
nearby strikes around ATM
CALL and PUT
every configured second
```

Important variables:

```env
EXPLOSIVE_RUNNER_ENABLED=true
EXPLOSIVE_RUNNER_SCAN_STRIKES=8
EXPLOSIVE_RUNNER_MIN_SCORE=55
MARKET_POLL_SECONDS=1
BACKGROUND_MARKET_MONITOR_ENABLED=true
```

What it checks:

- option premium LTP
- spread quality
- volume/OI
- delta velocity
- breakout velocity
- gamma
- IV expansion
- market profile/opening range

Example:

```text
NIFTY 23250 CALL
Premium LTP: 112.45
Runner score: 78
Confidence: HIGH
Target premium: +33%
Hard stop: -12%
Trail: 18%
```

How it helps AI:

- It creates a ranked watchlist.
- It can open paper trades even when the UI is closed because the backend monitor runs continuously.

### 5.3 Premarket and open-drive engine

Premarket analysis builds:

- direction bias
- PCR
- VAH/VAL/POC levels
- open-drive readiness
- first-minute triggers

Example:

```json
{
  "readiness": "WAIT_FOR_MARKET_OPEN",
  "bias": "BULLISH",
  "openDriveReadiness": {
    "score": 72,
    "state": "ARMED_FOR_PAPER_OPEN",
    "firstMinuteTriggers": [
      "Runner score >= EXPLOSIVE_RUNNER_MIN_SCORE",
      "Premium LTP expands with bid/ask spread still tradable"
    ]
  }
}
```

How it helps AI:

- The system prepares before 09:15 IST.
- At open, paper trades can trigger quickly if the real-time option premium confirms the plan.

### 5.4 Market movers snapshot

Endpoint:

```text
GET /api/market/movers
```

It uses:

```env
MARKET_SNAPSHOT_INSTRUMENT_KEYS=NSE_INDEX|Nifty 50,BSE_INDEX|SENSEX
MARKET_SNAPSHOT_MONITOR_ENABLED=true
MARKET_SNAPSHOT_POLL_SECONDS=5
```

It returns:

- gainers
- losers
- most active by value
- most active by volume
- indices
- breadth

Example:

```json
{
  "breadth": {
    "advancing": 35,
    "declining": 15,
    "score": 70,
    "bias": "BULLISH"
  },
  "gainers": [],
  "losers": []
}
```

How it helps AI:

- Confirms whether the broader market supports the index move.
- Helps avoid taking NIFTY calls when the market breadth is weak.

Note:

With only NIFTY and SENSEX configured, movers output is limited. To get NSE-style gainers/losers, add more stock instrument keys to `MARKET_SNAPSHOT_INSTRUMENT_KEYS`.

### 5.5 News and event intelligence

Primary provider should be:

```env
NEWS_PROVIDER=finnhub
UPSTOX_NEWS_ENABLED=false
```

Why:

Upstox news endpoint currently returns:

```text
UDAPI100060 Resource not Found
```

So Upstox news is disabled by default.

How it helps AI:

- Raises TQS threshold during high event risk.
- Can block fresh trades during negative high-risk events.
- Supports premarket planning.

## 6. Paper trading week plan

For the whole week, keep:

```env
ENABLE_LIVE_TRADING=false
PAPER_TRADING=true
SHADOW_TRADE_ALL_SIGNALS=true
PAPER_TRADING_RESPECTS_STOP=false
```

Daily routine:

1. Login to Upstox:

```text
https://api.nexusquant.uk/api/upstox/login-url
```

2. Confirm token:

```text
https://api.nexusquant.uk/api/upstox/token/status
```

Expected:

```json
{
  "hasToken": true,
  "source": "redis"
}
```

3. Check backend:

```text
https://api.nexusquant.uk/health
```

4. Check market data:

```text
https://api.nexusquant.uk/api/market/snapshots
```

5. Check paper trades:

```text
https://api.nexusquant.uk/api/auto-trader/status
```

## 7. Important AWS variables

Active EC2 env file:

```text
/opt/nexusquant/env
```

Core variables:

```env
ENVIRONMENT=production
PORT=8000
CORS_ORIGINS=https://app.nexusquant.uk,https://nexusquant-terminal.vercel.app,http://localhost:5173
UPSTOX_REDIRECT_URI=https://api.nexusquant.uk/api/upstox/callback
NEWS_PROVIDER=finnhub
UPSTOX_NEWS_ENABLED=false
```

Paper/execution variables:

```env
ENABLE_LIVE_TRADING=false
PAPER_TRADING=true
SHADOW_TRADE_ALL_SIGNALS=true
PAPER_TRADING_RESPECTS_STOP=false
```

Runner variables:

```env
BACKGROUND_MARKET_MONITOR_ENABLED=true
EXPLOSIVE_RUNNER_ENABLED=true
EXPLOSIVE_RUNNER_SCAN_STRIKES=8
EXPLOSIVE_RUNNER_MIN_SCORE=55
MARKET_POLL_SECONDS=1
```

Market snapshot variables:

```env
MARKET_SNAPSHOT_MONITOR_ENABLED=true
MARKET_SNAPSHOT_POLL_SECONDS=5
MARKET_SNAPSHOT_INSTRUMENT_KEYS=NSE_INDEX|Nifty 50,BSE_INDEX|SENSEX
```

Risk variables:

```env
AI_SCORE_THRESHOLD=72
SAFE_MODE_THRESHOLD=86
MAX_EXPOSURE_PCT=60
DAILY_DRAWDOWN_PCT=3
MIN_REQUIRED_MOVE_POINTS=5
```

Paper exit variables:

```env
PAPER_TARGET_POINTS=5
PAPER_STOP_POINTS=3
MAX_PAPER_TRADE_SECONDS=180
```

Do not set this in EC2 env:

```env
UPSTOX_ACCESS_TOKEN
```

The token should come from Redis after login.

## 8. Current AWS production components

### EC2 backend

```text
Instance: i-09ce535a67a0a6810
Elastic IP: 13.206.45.57
Private IP: 172.31.3.133
Docker container: nexusquant-api
```

### Domain

```text
Backend: https://api.nexusquant.uk
Frontend: https://app.nexusquant.uk
```

### Load balancer

```text
ALB HTTPS 443 -> EC2 private IP 172.31.3.133:8000
```

### Static IP for Upstox

Whitelist this in Upstox:

```text
13.206.45.57
```

## 9. Deployment commands

From CloudShell:

```bash
cd ~/nexusquant-terminal
git fetch origin main
git checkout main
git pull origin main

export AWS_REGION=ap-south-1
AWS_REGION=ap-south-1 ECR_REPOSITORY=nexusquant-api IMAGE_TAG=latest ./scripts/aws-ecr-push.sh
```

On EC2:

```bash
ssh -i nexusquant-ec2-key.pem ubuntu@13.206.45.57

export IMAGE_URI=939198471076.dkr.ecr.ap-south-1.amazonaws.com/nexusquant-api:latest

aws ecr get-login-password --region ap-south-1 | sudo docker login --username AWS --password-stdin 939198471076.dkr.ecr.ap-south-1.amazonaws.com

sudo docker pull $IMAGE_URI
sudo docker rm -f nexusquant-api
sudo docker run -d --name nexusquant-api --restart unless-stopped --env-file /opt/nexusquant/env -p 8000:8000 $IMAGE_URI
```

Verify:

```bash
curl http://127.0.0.1:8000/health
curl https://api.nexusquant.uk/health
curl https://api.nexusquant.uk/api/market/snapshots
curl https://api.nexusquant.uk/api/market/movers
```

## 10. Current EC2 disk issue and recovery

The EC2 instance has a small root disk. A Docker pull failed with:

```text
no space left on device
```

Then the container was removed, causing:

```text
502 Bad Gateway
No such container: nexusquant-api
```

Recovery steps on EC2:

```bash
sudo docker system df
sudo docker image prune -af
sudo docker container prune -f
sudo journalctl --vacuum-time=1d
df -h
```

Then pull and run again:

```bash
export IMAGE_URI=939198471076.dkr.ecr.ap-south-1.amazonaws.com/nexusquant-api:latest
sudo docker pull $IMAGE_URI
sudo docker run -d --name nexusquant-api --restart unless-stopped --env-file /opt/nexusquant/env -p 8000:8000 $IMAGE_URI
```

Recommended permanent fix:

- increase EC2 root volume from about 8 GB to 20-30 GB
- or use a smaller Docker image
- or periodically prune Docker images

## 11. Common errors

### `/api/market/movers` returns 404

Cause:

Old Docker image is running.

Fix:

Rebuild/push latest image and recreate container.

### Upstox news 404

Cause:

Upstox news endpoint is unavailable.

Fix:

```env
NEWS_PROVIDER=finnhub
UPSTOX_NEWS_ENABLED=false
```

Then recreate the container.

### Invalid token

Cause:

Upstox token expired or old `UPSTOX_ACCESS_TOKEN` in env.

Fix:

Remove env token and login again:

```bash
sudo sed -i '/^UPSTOX_ACCESS_TOKEN=/d' /opt/nexusquant/env
```

Login:

```text
https://api.nexusquant.uk/api/upstox/login-url
```

### Static IP restriction

Cause:

Upstox requires static outbound IP for funds/orders/positions.

Fix:

Use EC2 Elastic IP:

```text
13.206.45.57
```

### Funds service unavailable

Cause:

Upstox funds service has service hours.

This is not a system failure.

## 12. How this helps tomorrow morning

Before market opens:

- backend monitor runs continuously
- premarket bias is prepared
- market snapshot breadth is refreshed
- explosive runner watchlist scans nearby options
- paper trading is ready

At market open:

- if option premium LTP expands
- spread remains tradable
- volume/OI confirms
- TQS is high enough
- no hard risk block appears

then the system can open a paper trade instantly.

Because:

```env
ENABLE_LIVE_TRADING=false
```

no real broker order is sent.

## 13. What to review after each trading day

Check:

```text
/api/auto-trader/status
```

Review:

- total signals
- paper trades
- open trades
- closed trades
- win rate
- profit factor
- loss reasons
- skipped signals
- explosive runner candidates
- market snapshot breadth

Only after a full stable paper week should live mode be considered.
