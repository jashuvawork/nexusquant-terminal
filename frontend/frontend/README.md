# Vercel compatibility wrapper

This folder exists only to protect deployments where Vercel Root Directory is already `frontend` but the project still has an old manual build command:

```bash
cd frontend && npm install && npm run build
```

The correct Vercel configuration is either:

- Root Directory `./`, Build Command `npm --prefix frontend install && npm --prefix frontend run build`, Output `frontend/dist`
- Root Directory `frontend`, Build Command `npm run build`, Output `dist`

Do not add application code here.
