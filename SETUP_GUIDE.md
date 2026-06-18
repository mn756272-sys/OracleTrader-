# NEXUS TRADER — Complete Setup & Deployment Guide

## What You Just Built (Architecture)

```
[Yahoo Finance]
      ↓  (free data)
[Python FastAPI Backend]  ← runs on Render.com (free)
      ↓  (JSON via HTTP)
[React Frontend]          ← runs on Vercel (free)
      ↓  (display)
[Your Phone Browser]
      ↑
[Claude AI (NEXUS)]       ← powers the chat analyst
```

---

## STEP 1: Set Up Your Computer

Install Python if you don't have it:
- Download from: https://python.org/downloads
- During install, CHECK "Add Python to PATH"
- Verify: open terminal and type `python --version`

---

## STEP 2: Run Backend Locally First (Test It)

Open your terminal (Command Prompt on Windows):

```bash
# 1. Go to the backend folder
cd backend

# 2. Install all libraries
pip install -r requirements.txt

# 3. Start the server
python main.py
```

You should see:
```
NEXUS TRADER API — Starting...
Local URL : http://localhost:8000
API Docs  : http://localhost:8000/docs
```

Test it by opening http://localhost:8000/prices in your browser.
You should see real JSON market data.

---

## STEP 3: Deploy Backend to Render.com (Free, 24/7)

WHY RENDER?
Your laptop can't run the server 24/7. Render hosts it for
free on their cloud computers that never sleep.

1. Create account at: https://render.com
2. Create account at: https://github.com  (needed to upload code)

3. Create a new GitHub repository:
   - Go to github.com → New Repository
   - Name it: nexus-trader-backend
   - Upload all files from your /backend folder

4. On Render:
   - Click "New +" → "Web Service"
   - Connect GitHub → Select nexus-trader-backend repo
   - Render auto-detects render.yaml and configures itself
   - Click "Create Web Service"
   - Wait ~3 minutes for deployment

5. You'll get a URL like:
   https://nexus-trader-backend.onrender.com

6. Test it: https://nexus-trader-backend.onrender.com/prices
   You should see live market data.

---

## STEP 4: Update Frontend With Your Backend URL

Open /frontend/NexusTrader.jsx
Find this line near the top:

```javascript
const API_BASE = "http://localhost:8000";  // ← CHANGE THIS
```

Change it to your Render URL:
```javascript
const API_BASE = "https://nexus-trader-backend.onrender.com";
```

---

## STEP 5: Deploy Frontend to Vercel (Free)

1. Create account at: https://vercel.com
2. Create new GitHub repo: nexus-trader-frontend
3. Upload NexusTrader.jsx

EASY OPTION — Use v0.dev instead:
1. Go to: https://v0.dev
2. Paste the entire NexusTrader.jsx code
3. It hosts it instantly with a public URL
4. No GitHub needed

OR use Replit:
1. Go to: https://replit.com
2. New → React (Vite) template
3. Paste NexusTrader.jsx into App.jsx
4. Click Run → get public URL

---

## STEP 6: Add to Your Phone Home Screen

Once you have a public URL (from Vercel/v0.dev/Replit):

Android:
1. Open URL in Chrome
2. Tap the 3-dot menu (⋮)
3. Tap "Add to Home Screen"
4. It installs like a native app

iPhone:
1. Open URL in Safari
2. Tap the Share button (box with arrow)
3. Scroll down → "Add to Home Screen"
4. Tap Add

---

## Understanding Key Concepts (Taught Simply)

### What is a REST API?
Like a vending machine. You press a button (make a request),
it gives you what you asked for (sends back data).
- GET /prices → give me all prices
- GET /signal/EURUSD → give me EUR/USD signal

### What is JSON?
A standard way to write data that any language can read.
{"price": 1.0842, "rsi": 45.2, "signal": "BUY"}

### What is CORS?
A browser security rule. By default, a webpage at
site-A.com can't call an API at site-B.com. The CORS
middleware in our backend says "I allow all websites
to call me" — needed so your React app can call
the Python backend.

### What is a PORT?
Your computer is like a building. Ports are numbered doors.
- Port 80   = standard web (HTTP)
- Port 443  = secure web (HTTPS)
- Port 8000 = our Python API (development)
When deployed to Render, it uses Port 443 automatically.

### What is async/await?
When fetching data, your app doesn't know how long it will
take. async/await says "start fetching, but don't freeze
the whole app while waiting. Come back when it's done."

---

## Troubleshooting

Problem: "Backend offline" error
Solution: Make sure you ran `python main.py` and it's running

Problem: No prices showing
Solution: Check your API_BASE URL is correct in the JSX file

Problem: Render shows "free tier spins down"
Solution: Free tier sleeps after 15min of no traffic.
First load after sleep takes ~30 seconds. Upgrade to $7/mo
paid plan for always-on hosting.

Problem: CORS error in browser console
Solution: Already handled in main.py. If you see it,
check that your Render deployment is running.

---

## Files Summary

backend/
  main.py          ← Python FastAPI server (THE BRAIN)
  requirements.txt ← Python libraries to install
  render.yaml      ← Render.com deployment config

frontend/
  NexusTrader.jsx  ← React app (THE FACE)

---

## Cost Summary

Everything is FREE:
- Yahoo Finance data: Free
- Render.com hosting: Free (spins down after 15min idle)
- Vercel/v0.dev: Free
- Claude AI (NEXUS chat): Powered by Claude artifacts

Optional paid upgrades:
- Render paid ($7/mo): Always-on, faster
- Polygon.io ($29/mo): Millisecond real-time data
- VPS (DigitalOcean $5/mo): Full control, always on
