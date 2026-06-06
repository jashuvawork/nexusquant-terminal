# AWS Quick Deploy for NexusQuant Backend

Use AWS because Upstox account/order APIs can require a static outbound IP. Railway is fine for market-data/paper mode, but live Upstox funds/orders need a whitelisted static IP.

## Recommended AWS architecture

- ECR: Docker image registry
- ECS Fargate: backend container
- ALB: public HTTPS API endpoint
- RDS PostgreSQL: persistence
- ElastiCache Redis: token/cache/control state
- NAT Gateway + Elastic IP: static outbound IP for Upstox whitelist
- Secrets Manager: credentials and URLs

## 1. Push Docker image to ECR

Run from repo root in AWS CloudShell or a machine with AWS CLI + Docker:

```bash
AWS_REGION=ap-south-1 ECR_REPOSITORY=nexusquant-api IMAGE_TAG=latest ./scripts/aws-ecr-push.sh
```

## 2. Create static outbound IP

Create:

1. VPC private subnets for ECS tasks
2. NAT Gateway in public subnet
3. Elastic IP attached to NAT Gateway
4. Route private subnet outbound traffic through NAT Gateway
5. Whitelist that Elastic IP in Upstox developer console/app settings

## 3. Create RDS + Redis

Create PostgreSQL RDS and ElastiCache Redis in same VPC.

Store these in Secrets Manager:

```text
nexusquant/DATABASE_URL
nexusquant/REDIS_URL
nexusquant/UPSTOX_API_KEY
nexusquant/UPSTOX_API_SECRET
nexusquant/UPSTOX_ACCESS_TOKEN
```

## 4. ECS task definition

Use template:

```text
deploy/aws/ecs-task-definition.json
```

Replace:

```text
<ACCOUNT_ID>
your-vercel-domain
api.yourdomain.com
```

## 5. ECS service

Create ECS Fargate service behind ALB.

Target group:

```text
Port: 8000
Health path: /health
```

## 6. Vercel variables after AWS deploy

```text
VITE_API_URL=https://api.yourdomain.com
VITE_STREAM_MODE=websocket
VITE_FORCE_WEBSOCKET=true
VITE_WS_URL=wss://api.yourdomain.com/ws/market
```

For Railway keep polling, for AWS use WebSocket.
