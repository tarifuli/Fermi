# Fermi — AI-Powered SME Intelligence Platform

> Inventory management + Invoice generation + AI Business Chatbot  
> Built with Python (Flask) · Deployable for free on Render.com

---

## What Fermi Does

| Feature | Description |
|---|---|
| 📦 Inventory Management | Add, edit, delete products with stock tracking |
| 🧾 Invoice Generation | Create invoices, auto-reduce stock, download PDF |
| 📊 Dashboard | Live revenue, sales charts, top products |
| 🤖 Fermi AI Chatbot | Ask questions about your sales in English or Bengali |

---

## PART 1 — Run on Your Computer (Local)

### Step 1: Install Python
Download Python 3.11 from https://python.org/downloads  
✅ Check "Add Python to PATH" during install.

### Step 2: Download the project
If you have Git installed:
```bash
git clone https://github.com/YOUR_USERNAME/fermi.git
cd fermi
```
Or just download the ZIP from GitHub and extract it.

### Step 3: Install dependencies
Open terminal / command prompt in the `fermi` folder and run:
```bash
pip install -r requirements.txt
```

### Step 4: Add your Anthropic API key
Open `app.py` and find this line near the top:
```python
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', 'YOUR_API_KEY_HERE')
```
Replace `YOUR_API_KEY_HERE` with your actual key from:  
👉 https://console.anthropic.com/settings/keys  
(Free to sign up — you get free credits)

### Step 5: Run the app
```bash
python app.py
```
Open your browser and go to: **http://localhost:5000**

---

## PART 2 — Upload to GitHub

### Step 1: Create a GitHub account
Go to https://github.com and sign up (free).

### Step 2: Create a new repository
1. Click the **+** button → **New repository**
2. Name it: `fermi`
3. Set to **Public**
4. Click **Create repository**

### Step 3: Upload your files
**Option A — Via browser (easiest, no Git needed):**
1. On your new repo page, click **uploading an existing file**
2. Drag and drop ALL the Fermi files and folders
3. Click **Commit changes**

**Option B — Via terminal (if Git is installed):**
```bash
cd fermi
git init
git add .
git commit -m "Initial commit - Fermi platform"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/fermi.git
git push -u origin main
```

---

## PART 3 — Deploy for Free on Render.com

Render gives you a **free Python web server** — your website will be live 24/7.

### Step 1: Sign up at Render
Go to https://render.com and sign up with your **GitHub account**.

### Step 2: Create a new Web Service
1. Click **New +** → **Web Service**
2. Connect your GitHub account if not already connected
3. Select your **fermi** repository
4. Click **Connect**

### Step 3: Configure the service
Fill in these settings:

| Setting | Value |
|---|---|
| **Name** | fermi |
| **Region** | Singapore (closest to Bangladesh) |
| **Branch** | main |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app` |
| **Plan** | Free |

### Step 4: Add your API key as an Environment Variable
1. Scroll down to **Environment Variables**
2. Click **Add Environment Variable**
3. Key: `ANTHROPIC_API_KEY`
4. Value: your API key from https://console.anthropic.com/settings/keys
5. Click **Save**

### Step 5: Deploy!
Click **Create Web Service**.  
Render will build and deploy your app in 2–3 minutes.  
Your live URL will be: `https://fermi.onrender.com` (or similar)

> ⚠️ **Free tier note:** On Render's free plan, the server "sleeps" after 15 minutes of inactivity. The first visit after sleeping takes ~30 seconds to wake up. This is normal and fine for demos and pilots.

---

## Project Structure

```
fermi/
├── app.py                  ← Main Python application (Flask)
├── requirements.txt        ← Python packages needed
├── Procfile                ← Tells Render how to start the app
├── render.yaml             ← Render deployment config
├── runtime.txt             ← Python version
├── .gitignore              ← Files to exclude from GitHub
├── fermi.db                ← SQLite database (auto-created on first run)
└── templates/
    ├── base.html           ← Shared navigation and styling
    ├── dashboard.html      ← Home dashboard with charts
    ├── products.html       ← Inventory management
    ├── new_invoice.html    ← Create a new invoice
    ├── invoices.html       ← Invoice history list
    ├── view_invoice.html   ← Single invoice view
    └── chatbot.html        ← Fermi AI chatbot
```

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Backend | Python + Flask | Simple, powerful, free to host |
| Database | SQLite | Zero setup, works locally and on Render |
| AI Chatbot | Anthropic Claude API | Reads real business data, answers in Bengali/English |
| PDF Export | ReportLab | Professional invoice PDFs |
| Frontend | HTML + CSS + JavaScript | No framework needed, fast loading |
| Charts | Chart.js (CDN) | Beautiful revenue charts |
| Fonts | Google Fonts (DM Sans + Playfair Display) | Professional typography |

---

## Getting Your Anthropic API Key (Free)

1. Go to https://console.anthropic.com
2. Sign up with your email
3. Go to **Settings → API Keys**
4. Click **Create Key**
5. Copy the key and paste it in `app.py` or as an environment variable on Render

---

## Built by
**Tarif Chowdhury** — Founder, Fermi  
tarif.chowdhury14@gmail.com  
Aspire Institute Seed Fund 2026
