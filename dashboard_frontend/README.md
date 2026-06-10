# GameData Dashboard (Next.js)

Minimal Next.js 14 dashboard for viewing game session income and payout history.

## Prerequisites

- **Node.js** ≥ 18
- **Backend stub** running on `localhost:8500` (see `../backend_stub/`)

## Quick Start

```bash
# 1. Install dependencies
npm install

# 2. (Optional) Start the backend stub in another terminal
cd ../backend_stub
uvicorn main:create_app --factory --port 8500

# 3. Start the dev server
npm run dev
```

The dashboard will be available at **http://localhost:3000**.

## Pages

| Route        | Description                                      |
| ------------ | ------------------------------------------------ |
| `/`          | Landing page with Google / Discord sign-in buttons |
| `/income`    | Today's income + last 7 days session history table |

## Architecture

```
dashboard_frontend/
├── app/
│   ├── layout.tsx        # Root layout with nav + Tailwind globals
│   ├── page.tsx          # Landing: OAuth sign-in buttons
│   ├── globals.css       # Tailwind directives
│   └── income/
│       └── page.tsx      # Income dashboard (today + 7-day table)
├── lib/
│   └── api.ts            # API client → backend stub (localhost:8500)
├── package.json
├── tailwind.config.js
├── postcss.config.js
├── next.config.js
└── tsconfig.json
```

## API Client

`lib/api.ts` communicates with the backend stub at `http://localhost:8500`.
Override the URL with the `NEXT_PUBLIC_API_URL` environment variable:

```bash
NEXT_PUBLIC_API_URL=http://my-server:8500 npm run dev
```

### Endpoints used

| Method | Path                          | Purpose              |
| ------ | ----------------------------- | -------------------- |
| POST   | `/api/v1/auth/google/exchange`  | Mock Google OAuth    |
| POST   | `/api/v1/auth/discord/exchange` | Mock Discord OAuth   |
| GET    | `/api/v1/income/today`          | Today's income summary |

## Tech Stack

- **Next.js 14** (App Router)
- **TypeScript**
- **Tailwind CSS 3**
- **ESLint** + **Prettier**

## Notes

- No login state persistence — relies on backend Bearer token per request
- OAuth buttons are mock (hit backend stub exchange endpoints)
- No database — all data is in-memory on the backend stub
