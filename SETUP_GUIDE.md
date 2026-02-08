# 🧾 ExpenseSnap - Setup & Deployment Guide

## What is this?
A web app where your team can scan receipts from their phone or laptop, and it automatically extracts all the data into a dashboard + downloadable Excel report.

---

## Option A: Run on Your Laptop (for testing)

### Step 1: Install Python
- Download from https://www.python.org/downloads/
- During install, ✅ CHECK "Add Python to PATH"
- Restart your computer after installing

### Step 2: Install libraries
Open Command Prompt (press Windows key, type `cmd`, press Enter):
```
pip install flask anthropic openpyxl Pillow
```

### Step 3: Set your API key
In the same Command Prompt:
```
setx ANTHROPIC_API_KEY "sk-ant-your-key-here"
```
Close and reopen Command Prompt after this.

### Step 4: Run the app
```
cd path\to\folder
python app.py
```

### Step 5: Open in browser
Go to: **http://localhost:5000**

Your team can access it too if they're on the same WiFi:
- Find your IP: type `ipconfig` in Command Prompt, look for "IPv4 Address" (e.g., 192.168.1.50)
- Team opens: **http://192.168.1.50:5000** on their phones

---

## Option B: Deploy to the Internet ($5/month)

This makes it accessible from anywhere — your friend's team just opens a link.

### Using Railway (Easiest)

1. Go to https://railway.app and sign up
2. Install Railway CLI or use their dashboard
3. Create a new project → Deploy from GitHub or upload files
4. Add these files to your project:

**requirements.txt** (create this file):
```
flask==3.0.0
anthropic==0.40.0
openpyxl==3.1.2
Pillow==10.4.0
gunicorn==21.2.0
```

**Procfile** (create this file):
```
web: gunicorn app:app --bind 0.0.0.0:$PORT
```

5. In Railway dashboard, add Environment Variable:
   - Key: `ANTHROPIC_API_KEY`
   - Value: your API key

6. Deploy! Railway gives you a URL like `https://expensesnap-xxx.up.railway.app`

### Using Render (Also easy, has free tier)

1. Go to https://render.com and sign up
2. New → Web Service → Upload your code
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app`
5. Add environment variable: `ANTHROPIC_API_KEY`

---

## How Your Friend's Team Uses It

1. Open the link on their phone or laptop
2. Tap "Upload" → take a photo of receipt or choose from gallery
3. Wait 2-3 seconds → expense appears automatically
4. View dashboard for spending breakdown
5. Download Excel report anytime

---

## Cost Breakdown

| Item | Cost |
|------|------|
| Claude API (per receipt) | ~$0.01-0.03 |
| Railway hosting | ~$5/month |
| 100 receipts/month | ~$2-3/month API |
| **Total for a small team** | **~$7-8/month** |

---

## Files in this project

```
expense_app/
├── app.py              ← The entire app (backend + frontend)
├── requirements.txt    ← Python libraries needed
├── Procfile           ← For cloud deployment
├── expenses.db        ← Auto-created, stores all data
└── uploads/           ← Auto-created, stores receipt images
```

---

## FAQ

**Q: Is the data secure?**
A: Data stays in your own database. Nothing is shared externally except the receipt image sent to Claude's API for reading.

**Q: Can multiple people upload at the same time?**
A: Yes! It handles concurrent uploads.

**Q: What if Claude reads a number wrong?**
A: You can edit any expense from the All Expenses tab (click on it to edit).

**Q: Can I add expenses manually?**
A: Not yet in this version, but I can add that feature.
