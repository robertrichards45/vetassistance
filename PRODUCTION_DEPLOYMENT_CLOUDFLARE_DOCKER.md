# Production Deployment (Docker + Cloudflare Tunnel)

## 1) Prereqs
- Docker Desktop installed
- Domain managed in Cloudflare
- Cloudflare Zero Trust enabled (for Tunnel)

## 2) Configure environment
Copy `.env.example` to `.env` and fill in:
- `SECRET_KEY`
- SMTP settings (optional but recommended)
- Stripe keys (optional)
- Cloudflare tunnel token

**Important:** `SEED_ADMIN_ENABLED` should be `0` in production.

## 3) Build + run locally (prod-like)
```bash
docker compose up --build
```
App will be available at:
- http://localhost:8000

## 4) Create first admin (no seed admin)
```bash
docker compose exec web flask create-admin
```
This creates a DIRECTOR user under the first Organization.

## 5) Cloudflare Tunnel
### Option A (token-based, recommended)
1. In Cloudflare Zero Trust → Access → Tunnels → Create Tunnel.
2. Add public hostname (your domain/subdomain) pointing to:
   - Service: `http://web:8000`
3. Copy the token into `.env`:
   - `CLOUDFLARE_TUNNEL_TOKEN=...`

Then:
```bash
docker compose up -d cloudflared
```

## 6) Persistence
- SQLite & intake files: `./instance`
- Uploads: `./uploads`
Keep these folders backed up.

## 7) Hardening checklist
- Use a strong `SECRET_KEY`
- Set `FLASK_ENV=production`
- Ensure debug is off
- Set upload size/type restrictions (already partially in place)
- Add Cloudflare WAF rules/rate limits for public endpoints
