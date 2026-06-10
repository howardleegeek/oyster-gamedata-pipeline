---
task_id: S62-dashboard-minimal-nextjs
project: gamedata-pipeline
priority: 2
estimated_minutes: 50
depends_on:
  - S25-backend-stub-fastapi
modifies:
  - dashboard_frontend/package.json
  - dashboard_frontend/app/page.tsx
  - dashboard_frontend/app/layout.tsx
  - dashboard_frontend/app/income/page.tsx
  - dashboard_frontend/lib/api.ts
  - dashboard_frontend/README.md
executor: qwen3.6-plus
---

## 目标

最简 Next.js dashboard — 内测 user 登录后看 income + sessions history。

1. Next.js 14 app dir, TypeScript, Tailwind
2. `app/page.tsx` — landing: "Sign in with Google / Discord" buttons (OAuth redirect to backend stub)
3. `app/income/page.tsx`:
   - Today's income (\$X)
   - Last 7 days table (date, sessions count, \$ earned)
   - "How payouts work" tooltip
4. `lib/api.ts` — fetch from backend stub `localhost:8500`
5. No login state persistence (rely on backend Bearer token)
6. `README.md` — `npm install && npm run dev` 起 port 3000

## 约束

- 不写 backend (S25 已有)
- 不真 OAuth (mock buttons that hit backend stub /auth/google/exchange)
- 不需要 db
- ESLint + Prettier defaults
- Tailwind CDN OR npm (whichever simpler)

## 验收

- [ ] `npm install && npm run dev` 起 :3000
- [ ] visit / → landing 显示 2 buttons
- [ ] /income → 拉 backend → 显示 mock data
- [ ] mobile responsive (tailwind sm: prefix)
- [ ] README 含完整 setup steps

## 不要做

- 不写真 OAuth provider config
- 不连真 db
- 不 deploy
- 直接 commit 到 branch `feat/S62-dashboard-nextjs`
