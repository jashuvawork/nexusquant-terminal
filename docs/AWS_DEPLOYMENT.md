# Deploy NexusQuant Backend on AWS and Frontend on Vercel

This backend needs WebSocket support for `/ws/market`, so use **Amazon ECS Fargate behind an Application Load Balancer**. Avoid AWS App Runner for this project because App Runner is not a reliable target for persistent WebSocket connections.

## Target architecture

- Frontend: Vercel
- Backend container: Amazon ECS Fargate
- Public ingress: Application Load Balancer with HTTPS
- Container registry: Amazon ECR
- Database: Amazon RDS PostgreSQL
- Token/cache queue: Amazon ElastiCache Redis
- Secrets: AWS Secrets Manager or ECS task environment secrets
- Logs: Amazon CloudWatch Logs

## 1. Prepare AWS account

Choose one AWS region close to Indian markets/users, for example:

```text
ap-south-1
```

Install locally if you want CLI deployment:

```bash
aws --version
aws configure
```

You can also do all steps from the AWS Console.

## 2. Create ECR repository

AWS Console:

1. Open **ECR**.
2. Click **Create repository**.
3. Name it:

```text
nexusquant-api
```

CLI alternative:

```bash
aws ecr create-repository --repository-name nexusquant-api --region ap-south-1
```

## 3. Build and push backend Docker image

From the repo root:

```bash
AWS_ACCOUNT_ID=123456789012
AWS_REGION=ap-south-1
ECR_REPO=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/nexusquant-api

aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker build -t nexusquant-api ./backend
docker tag nexusquant-api:latest $ECR_REPO:latest
docker push $ECR_REPO:latest
```

## 4. Create RDS PostgreSQL

AWS Console:

1. Open **RDS** -> **Create database**.
2. Engine: **PostgreSQL**.
3. Template: **Free tier** or **Production**, depending on your account.
4. DB name:

```text
nexusquant
```

5. Create username/password.
6. Put it in the same VPC that ECS will use.
7. Security group: allow inbound PostgreSQL `5432` only from the ECS task security group.

Your final `DATABASE_URL` will look like:

```text
postgresql://USER:PASSWORD@RDS_ENDPOINT:5432/nexusquant
```

## 5. Create ElastiCache Redis

AWS Console:

1. Open **ElastiCache** -> **Redis OSS caches**.
2. Create a small Redis cache in the same VPC.
3. Security group: allow inbound Redis `6379` only from ECS task security group.

Your final `REDIS_URL` will look like:

```text
redis://REDIS_ENDPOINT:6379/0
```

If in-transit encryption/auth token is enabled, use the TLS/auth URL required by your Redis configuration.

## 6. Create ECS cluster

AWS Console:

1. Open **ECS**.
2. Create cluster.
3. Infrastructure: **AWS Fargate**.
4. Name:

```text
nexusquant-cluster
```

## 7. Create ECS task definition

Create a Fargate task definition:

- Name: `nexusquant-api`
- Launch type: Fargate
- CPU: `0.5 vCPU` minimum for testing, `1 vCPU` preferred
- Memory: `1 GB` minimum, `2 GB` preferred
- Container image: your ECR image URL
- Container port: `8000`
- Health check path later on ALB: `/health`

Environment variables:

```text
ENVIRONMENT=production
CORS_ORIGINS=https://your-vercel-domain.vercel.app,http://localhost:5173
DATABASE_URL=postgresql://USER:PASSWORD@RDS_ENDPOINT:5432/nexusquant
REDIS_URL=redis://REDIS_ENDPOINT:6379/0
UPSTOX_REDIRECT_URI=https://your-backend-domain.com/api/upstox/callback
PRIMARY_SYMBOL=NIFTY
NIFTY_INSTRUMENT_KEY=NSE_INDEX|Nifty 50
SENSEX_INSTRUMENT_KEY=BSE_INDEX|SENSEX
NIFTY_EXPIRY_DATE=YYYY-MM-DD
SENSEX_EXPIRY_DATE=YYYY-MM-DD
MARKET_POLL_SECONDS=1
ENABLE_LIVE_TRADING=false
AGGRESSIVE_MODE=false
AI_SCORE_THRESHOLD=76
SAFE_MODE_THRESHOLD=86
MAX_EXPOSURE_PCT=42
DAILY_DRAWDOWN_PCT=3
```

Store these as ECS secrets, not plain text, if possible:

```text
UPSTOX_API_KEY=your_upstox_api_key
UPSTOX_API_SECRET=your_upstox_api_secret
DATABASE_URL=...
REDIS_URL=...
```

## 8. Create ECS service with Application Load Balancer

In ECS cluster:

1. Click **Create service**.
2. Launch type: **Fargate**.
3. Task definition: `nexusquant-api`.
4. Desired tasks: `1` for testing, `2` for production.
5. Networking: select public subnets or private subnets with NAT.
6. Create or select an **Application Load Balancer**.
7. Listener: HTTPS `443` preferred, HTTP `80` acceptable for first test.
8. Target group:
   - Target type: IP
   - Protocol: HTTP
   - Port: `8000`
   - Health check path: `/health`

ALB supports WebSocket upgrade requests, so `/ws/market` can work through the same backend domain.

## 9. Add HTTPS domain

Recommended:

1. Use **Route 53** or your DNS provider.
2. Create domain like:

```text
api.yourdomain.com
```

3. Point it to the ALB.
4. Use **AWS Certificate Manager** to issue an SSL certificate.
5. Attach the certificate to ALB listener `443`.

If you do not have a domain yet, you can temporarily use the ALB DNS name, but Vercel WebSocket should use `wss://` in production, so HTTPS domain is strongly preferred.

## 10. Configure Upstox redirect URL

In Upstox developer console, set redirect URL to your AWS backend:

```text
https://api.yourdomain.com/api/upstox/callback
```

This must exactly match:

```text
UPSTOX_REDIRECT_URI=https://api.yourdomain.com/api/upstox/callback
```

## 11. Test backend

Open:

```text
https://api.yourdomain.com/health
```

Then token status:

```text
https://api.yourdomain.com/api/upstox/token/status
```

Get login URL:

```text
https://api.yourdomain.com/api/upstox/login-url
```

Open the returned `loginUrl`, login to Upstox, approve access, then check:

```text
https://api.yourdomain.com/api/upstox/token/status
```

Expected:

```json
{
  "configured": true,
  "hasToken": true
}
```

Test real snapshots:

```text
https://api.yourdomain.com/api/market/snapshot/NIFTY
https://api.yourdomain.com/api/market/snapshot/SENSEX
```

## 12. Deploy frontend on Vercel

1. Open Vercel.
2. Add New -> Project.
3. Import this GitHub repository.
4. Keep root directory as repo root. `vercel.json` handles frontend build.
5. Add environment variables:

```text
VITE_API_URL=https://api.yourdomain.com
VITE_WS_URL=wss://api.yourdomain.com/ws/market
```

6. Deploy.

After Vercel gives your frontend URL, update backend `CORS_ORIGINS` in ECS:

```text
CORS_ORIGINS=https://your-vercel-domain.vercel.app,http://localhost:5173
```

Redeploy the ECS service.

## 13. Live trading switch

Start in analysis-only mode:

```text
ENABLE_LIVE_TRADING=false
AGGRESSIVE_MODE=false
```

Only after verifying data, token, expiry, orders, risk limits, and small quantity testing, change ECS variables to:

```text
ENABLE_LIVE_TRADING=true
AGGRESSIVE_MODE=true
```

Then redeploy ECS service.

## Troubleshooting

### Frontend says `BACKEND_WS_ERROR`

Check:

```text
VITE_WS_URL=wss://api.yourdomain.com/ws/market
```

Also verify ALB target group is healthy and HTTPS listener routes to container port `8000`.

### Backend says `UPSTOX_AUTH_REQUIRED`

Open:

```text
https://api.yourdomain.com/api/upstox/login-url
```

Complete broker login again.

### Backend says `CONFIGURATION_REQUIRED`

Set expiry dates:

```text
NIFTY_EXPIRY_DATE=YYYY-MM-DD
SENSEX_EXPIRY_DATE=YYYY-MM-DD
```

### ALB target is unhealthy

Check ECS task logs in CloudWatch. Confirm:

- Container port is `8000`
- ALB target group port is `8000`
- Health check path is `/health`
- Security group allows ALB -> ECS on port `8000`
