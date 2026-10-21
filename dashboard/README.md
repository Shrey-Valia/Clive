# Clive Trading dashboard

A Next.js app on Vercel with a Supabase database. It shows every bot: the Shorting bot (live), the Lucid bot (inactive) and the Options bot (planned).

- The bots write to Supabase with the **service-role** key from the machine they run on.
- The dashboard reads as a signed-in user. Row-level security only lets emails in `dashboard_admins` read anything.
- Live Alpaca positions are fetched server-side. The Alpaca keys never reach the browser.

## One-time setup

1. **Supabase.** Create a project, then open SQL Editor and run `supabase/migrations/001_init.sql`. Then add yourself:
   ```sql
   insert into dashboard_admins (email) values ('your-email');
   ```
   Under Authentication → URL Configuration, set the Site URL to your Vercel URL and add `https://<your-app>.vercel.app/auth/callback` to the Redirect URLs.
2. **Bot keys.** In `Stock Shorting Bot/.env`, set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` (Settings → API → service_role).
3. **Vercel.** From this folder:
   ```bash
   vercel link
   vercel env add NEXT_PUBLIC_SUPABASE_URL
   vercel env add NEXT_PUBLIC_SUPABASE_ANON_KEY
   vercel env add APCA_API_KEY_ID        # paper key
   vercel env add APCA_API_SECRET_KEY
   vercel --prod
   ```

## Local

```bash
cp .env.example .env.local   # fill in
npm install && npm run dev
```

To preview without Supabase, run `DEMO_MODE=1 npm run dev`. That renders a captured real scan, with simulated positions built from its 5 picks, behind a DEMO DATA badge. It also turns off sign-in, so **never set `DEMO_MODE` on Vercel**.

## Adding the Lucid bot later

Fill `lucid_accounts` (one row per Tradovate account) and `lucid_daily`, using the service key, from whatever reads Tradovate (its API or CrossTrade). Also set `bots.status = 'active'` for `lucid`. The `/lucid` page already renders those tables.
