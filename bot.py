# 🚀 DEPLOY IN 5 MINUTES — GET YOUR LIVE URL

## FASTEST OPTION: Railway.app (2-3 min, free, no credit card)

### Step 1 — Upload to GitHub (1 min)
1. Go to https://github.com/new
2. Create repo named `vera-bot` (public)
3. Upload ALL files from this zip (drag & drop the files into GitHub)
4. Commit

### Step 2 — Deploy on Railway (1 min)
1. Go to https://railway.app
2. Click **"Start a New Project"** → **"Deploy from GitHub repo"**
3. Select `vera-bot`
4. Railway auto-detects Python ✅

### Step 3 — Set environment variable (30 sec)
1. In Railway dashboard → **Variables** tab
2. Add: `ANTHROPIC_API_KEY` = `your_anthropic_api_key`

### Step 4 — Get your URL (30 sec)
1. Go to **Settings** tab → **Domains**
2. Click **"Generate Domain"**
3. Your URL will be: `https://vera-bot-XXXX.railway.app`

### Step 5 — Test it works
Open browser: `https://vera-bot-XXXX.railway.app/v1/healthz`
You should see: `{"status": "ok", ...}`

✅ Submit this URL to magicpin: `https://vera-bot-XXXX.railway.app`

---

## OPTION 2: Render.com (also free, ~3-4 min)

1. Push code to GitHub (same as above)
2. Go to https://render.com → New → Web Service
3. Connect GitHub repo `vera-bot`
4. Build command: `pip install -r requirements.txt`
5. Start command: `uvicorn server_standalone:app --host 0.0.0.0 --port $PORT`
6. Add env var: `ANTHROPIC_API_KEY` = your key
7. Deploy → get URL from dashboard

---

## Which file to use as main server?

Use **`server_standalone.py`** — it has everything in one file, no imports from bot.py needed.

Start command: `uvicorn server_standalone:app --host 0.0.0.0 --port $PORT`

---

## ANTHROPIC API KEY

Get one free at: https://console.anthropic.com
(The bot works WITHOUT it too — it has smart fallback responses)
