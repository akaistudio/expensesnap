"""
ExpenseSnap - Receipt Scanner Web App
======================================
A web app for small teams to scan receipts and track expenses.

Setup:
  pip install flask anthropic openpyxl Pillow
  export ANTHROPIC_API_KEY="your-key"
  python app.py

Then open http://localhost:5000 in your browser.
"""

import os
import json
import base64
import uuid
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from io import BytesIO

import anthropic
from flask import Flask, request, jsonify, send_file, render_template_string
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

DB_PATH = Path(__file__).parent / "expenses.db"
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MODEL = "claude-sonnet-4-5-20250929"

# ── Database ───────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id TEXT PRIMARY KEY,
            date TEXT,
            vendor TEXT,
            location TEXT,
            category TEXT,
            subtotal REAL DEFAULT 0,
            tax REAL DEFAULT 0,
            tip REAL DEFAULT 0,
            total REAL DEFAULT 0,
            payment_method TEXT,
            currency TEXT DEFAULT 'USD',
            items TEXT,
            uploaded_by TEXT DEFAULT 'default',
            receipt_image TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# ── Claude API ─────────────────────────────────────────────────
def extract_receipt(image_bytes, media_type="image/jpeg"):
    client = anthropic.Anthropic()
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    prompt = """Analyze this receipt/bill image and extract the following.
Return ONLY a valid JSON object with these exact keys:
{
  "date": "YYYY-MM-DD format, or empty string if not found",
  "vendor": "Business/restaurant name",
  "location": "City, State/Province or City, Country",
  "category": "One of: Food & Dining, Groceries, Air Travel, Cab & Rideshare, Hotel & Accommodation, Shopping & Retail, Utilities, Entertainment, Office & Business, Healthcare, Fuel & Parking, Other",
  "subtotal": 0.00,
  "tax": 0.00,
  "tip": 0.00,
  "total": 0.00,
  "payment_method": "e.g. Visa ****1234, Cash, etc.",
  "currency": "e.g. CAD, USD, EUR, INR",
  "items": "Comma-separated list of items"
}
Use 0.00 for missing amounts. Return ONLY JSON."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": prompt}
            ]
        }]
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    return json.loads(text)

# ── Excel Export ───────────────────────────────────────────────
def generate_excel(expenses):
    wb = Workbook()
    ws = wb.active
    ws.title = "Expenses"

    headers = ["Date", "Vendor", "Location", "Category", "Subtotal",
               "Tax", "Tip", "Total", "Payment Method", "Currency", "Items"]
    widths = [14, 28, 22, 18, 14, 12, 12, 14, 18, 12, 35]

    hfill = PatternFill('solid', fgColor='1F4E79')
    hfont = Font(name='Arial', bold=True, color='FFFFFF', size=11)
    dfont = Font(name='Arial', size=10)
    border = Border(bottom=Side(style='thin', color='D9D9D9'))
    curr_fmt = '$#,##0.00'

    for i, (name, w) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(row=1, column=i, value=name)
        cell.font, cell.fill = hfont, hfill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[chr(64+i)].width = w

    ws.row_dimensions[1].height = 28
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = 'A1:K1'

    for r, exp in enumerate(expenses, 2):
        vals = [exp['date'], exp['vendor'], exp['location'], exp['category'],
                exp['subtotal'], exp['tax'], exp['tip'], exp['total'],
                exp['payment_method'], exp['currency'], exp['items']]
        for c, val in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.font, cell.border = dfont, border
            if c in (5,6,7,8):
                cell.number_format = curr_fmt
            cell.alignment = Alignment(horizontal='left' if c == 11 else 'center')

    # Summary row
    last = len(expenses) + 1
    sr = last + 2
    ws.cell(row=sr, column=7, value="TOTAL:").font = Font(name='Arial', bold=True, size=11)
    ws.cell(row=sr, column=8, value=f"=SUM(H2:H{last})").font = Font(name='Arial', bold=True, size=11)
    ws.cell(row=sr, column=8).number_format = curr_fmt

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

# ── API Routes ─────────────────────────────────────────────────
@app.route('/api/upload', methods=['POST'])
def upload_receipt():
    if 'receipt' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['receipt']
    uploaded_by = request.form.get('user', 'default')

    ext_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
               '.webp': 'image/webp', '.gif': 'image/gif', '.heic': 'image/png'}
    ext = Path(file.filename).suffix.lower()
    media_type = ext_map.get(ext, 'image/jpeg')

    image_bytes = file.read()

    # Save image
    img_id = str(uuid.uuid4())[:8]
    img_filename = f"{img_id}{ext}"
    img_path = UPLOAD_DIR / img_filename
    with open(img_path, 'wb') as f:
        f.write(image_bytes)

    # Extract with Claude
    try:
        data = extract_receipt(image_bytes, media_type)
    except Exception as e:
        return jsonify({"error": f"Failed to extract: {str(e)}"}), 500

    # Save to DB
    expense_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute("""
        INSERT INTO expenses (id, date, vendor, location, category, subtotal, tax, tip, total,
                             payment_method, currency, items, uploaded_by, receipt_image)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (expense_id, data.get('date',''), data.get('vendor',''), data.get('location',''),
          data.get('category',''), data.get('subtotal',0), data.get('tax',0),
          data.get('tip',0), data.get('total',0), data.get('payment_method',''),
          data.get('currency',''), data.get('items',''), uploaded_by, img_filename))
    conn.commit()
    conn.close()

    data['id'] = expense_id
    return jsonify({"success": True, "expense": data})

@app.route('/api/expenses')
def get_expenses():
    conn = get_db()
    rows = conn.execute("SELECT * FROM expenses ORDER BY date DESC, created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/expenses/<expense_id>', methods=['DELETE'])
def delete_expense(expense_id):
    conn = get_db()
    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/expenses/<expense_id>', methods=['PUT'])
def update_expense(expense_id):
    data = request.json
    conn = get_db()
    fields = []
    values = []
    for key in ['date','vendor','location','category','subtotal','tax','tip','total','payment_method','currency','items']:
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    values.append(expense_id)
    conn.execute(f"UPDATE expenses SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/dashboard')
def dashboard_data():
    conn = get_db()
    rows = conn.execute("SELECT * FROM expenses ORDER BY date DESC").fetchall()
    expenses = [dict(r) for r in rows]
    conn.close()

    total = sum(e['total'] for e in expenses)
    by_category = {}
    by_month = {}
    for e in expenses:
        cat = e['category'] or 'Other'
        by_category[cat] = by_category.get(cat, 0) + e['total']
        month = e['date'][:7] if e['date'] else 'Unknown'
        by_month[month] = by_month.get(month, 0) + e['total']

    return jsonify({
        "total": total,
        "count": len(expenses),
        "by_category": by_category,
        "by_month": dict(sorted(by_month.items())),
        "recent": expenses[:10]
    })

@app.route('/api/export')
def export_excel():
    conn = get_db()
    rows = conn.execute("SELECT * FROM expenses ORDER BY date ASC").fetchall()
    conn.close()
    expenses = [dict(r) for r in rows]

    if not expenses:
        return jsonify({"error": "No expenses to export"}), 400

    buf = generate_excel(expenses)
    today = datetime.now().strftime('%Y-%m-%d')
    return send_file(buf, download_name=f"expenses_{today}.xlsx",
                     as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

# ── Frontend ───────────────────────────────────────────────────
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>ExpenseSnap</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0B0F1A;
  --surface: #141926;
  --surface2: #1C2235;
  --border: #2A3148;
  --text: #E8ECF4;
  --text2: #8B95B0;
  --accent: #6C5CE7;
  --accent2: #A29BFE;
  --green: #00D2A0;
  --red: #FF6B6B;
  --orange: #FDCB6E;
  --blue: #74B9FF;
  --radius: 16px;
  --radius-sm: 10px;
}

* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: 'DM Sans', sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}

/* ── Top Bar ── */
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 20px 28px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 100;
  backdrop-filter: blur(20px);
}
.logo {
  font-size: 22px; font-weight: 700;
  background: linear-gradient(135deg, var(--accent), var(--green));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  letter-spacing: -0.5px;
}
.logo span { font-weight: 400; opacity: 0.7; }
.topbar-actions { display: flex; gap: 10px; }
.btn {
  padding: 10px 20px; border: none; border-radius: var(--radius-sm);
  font-family: inherit; font-size: 14px; font-weight: 600;
  cursor: pointer; transition: all 0.2s;
}
.btn-primary {
  background: var(--accent); color: white;
}
.btn-primary:hover { background: #5A4BD1; transform: translateY(-1px); }
.btn-ghost {
  background: transparent; color: var(--text2);
  border: 1px solid var(--border);
}
.btn-ghost:hover { color: var(--text); border-color: var(--text2); }

/* ── Navigation ── */
.nav {
  display: flex; gap: 0;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 0 28px;
}
.nav-tab {
  padding: 16px 24px; font-size: 14px; font-weight: 500;
  color: var(--text2); cursor: pointer; border: none;
  background: none; border-bottom: 2px solid transparent;
  transition: all 0.2s; font-family: inherit;
}
.nav-tab:hover { color: var(--text); }
.nav-tab.active {
  color: var(--accent2); border-bottom-color: var(--accent);
}

/* ── Main ── */
.main { padding: 28px; max-width: 1200px; margin: 0 auto; }

/* ── Upload Zone ── */
.upload-zone {
  border: 2px dashed var(--border);
  border-radius: var(--radius);
  padding: 60px 40px;
  text-align: center;
  transition: all 0.3s;
  cursor: pointer;
  background: var(--surface);
  position: relative;
  overflow: hidden;
}
.upload-zone:hover, .upload-zone.dragover {
  border-color: var(--accent);
  background: rgba(108, 92, 231, 0.05);
}
.upload-zone.dragover { transform: scale(1.01); }
.upload-icon {
  width: 64px; height: 64px; margin: 0 auto 20px;
  background: linear-gradient(135deg, var(--accent), var(--green));
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font-size: 28px;
}
.upload-title { font-size: 20px; font-weight: 600; margin-bottom: 8px; }
.upload-sub { color: var(--text2); font-size: 14px; }
.upload-input { display: none; }

/* ── Processing overlay ── */
.processing {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(11,15,26,0.85); backdrop-filter: blur(10px);
  display: flex; align-items: center; justify-content: center;
  z-index: 200; opacity: 0; pointer-events: none; transition: opacity 0.3s;
}
.processing.active { opacity: 1; pointer-events: all; }
.processing-card {
  background: var(--surface); border-radius: var(--radius);
  padding: 48px; text-align: center; border: 1px solid var(--border);
  max-width: 400px;
}
.spinner {
  width: 48px; height: 48px; border: 3px solid var(--border);
  border-top-color: var(--accent); border-radius: 50%;
  animation: spin 0.8s linear infinite; margin: 0 auto 20px;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Toast ── */
.toast {
  position: fixed; bottom: 28px; right: 28px; z-index: 300;
  padding: 16px 24px; border-radius: var(--radius-sm);
  font-weight: 500; font-size: 14px;
  transform: translateY(100px); opacity: 0; transition: all 0.3s;
}
.toast.show { transform: translateY(0); opacity: 1; }
.toast.success { background: var(--green); color: #000; }
.toast.error { background: var(--red); color: #fff; }

/* ── Dashboard Cards ── */
.stats-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px; margin-bottom: 28px;
}
.stat-card {
  background: var(--surface); border-radius: var(--radius);
  padding: 24px; border: 1px solid var(--border);
}
.stat-label { font-size: 13px; color: var(--text2); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-value { font-size: 32px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
.stat-value.green { color: var(--green); }

/* ── Category bars ── */
.cat-section {
  background: var(--surface); border-radius: var(--radius);
  padding: 24px; border: 1px solid var(--border); margin-bottom: 28px;
}
.cat-section h3 { font-size: 16px; margin-bottom: 20px; font-weight: 600; }
.cat-row { display: flex; align-items: center; margin-bottom: 14px; gap: 12px; }
.cat-name { width: 160px; font-size: 13px; color: var(--text2); flex-shrink: 0; }
.cat-bar-bg {
  flex: 1; height: 28px; background: var(--surface2);
  border-radius: 6px; overflow: hidden; position: relative;
}
.cat-bar {
  height: 100%; border-radius: 6px; transition: width 0.8s ease;
  min-width: 2px;
}
.cat-amount {
  width: 100px; text-align: right; font-family: 'JetBrains Mono', monospace;
  font-size: 13px; font-weight: 500; flex-shrink: 0;
}

/* ── Table ── */
.table-wrap {
  background: var(--surface); border-radius: var(--radius);
  border: 1px solid var(--border); overflow: hidden;
}
.table-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 20px 24px; border-bottom: 1px solid var(--border);
}
.table-header h3 { font-size: 16px; font-weight: 600; }
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left; padding: 14px 20px; font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.5px;
  color: var(--text2); font-weight: 600;
  border-bottom: 1px solid var(--border);
  background: var(--surface2);
}
td {
  padding: 16px 20px; font-size: 14px;
  border-bottom: 1px solid var(--border);
}
tr:hover td { background: rgba(108,92,231,0.03); }
.cat-badge {
  display: inline-block; padding: 4px 12px;
  border-radius: 20px; font-size: 12px; font-weight: 500;
  background: rgba(108,92,231,0.15); color: var(--accent2);
}
.amount {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 500;
}
.delete-btn {
  background: none; border: none; color: var(--text2);
  cursor: pointer; font-size: 16px; padding: 4px 8px;
  border-radius: 6px; transition: all 0.2s;
}
.delete-btn:hover { color: var(--red); background: rgba(255,107,107,0.1); }

/* ── Empty state ── */
.empty-state {
  text-align: center; padding: 60px 20px; color: var(--text2);
}
.empty-state .icon { font-size: 48px; margin-bottom: 16px; }
.empty-state p { font-size: 15px; }

/* ── Section hidden ── */
.section { display: none; }
.section.active { display: block; }

/* ── Responsive ── */
@media (max-width: 768px) {
  .topbar { padding: 16px 20px; }
  .main { padding: 20px; }
  .nav { padding: 0 12px; overflow-x: auto; }
  .nav-tab { padding: 14px 16px; font-size: 13px; white-space: nowrap; }
  .upload-zone { padding: 40px 20px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 12px; }
  .stat-value { font-size: 24px; }
  .table-wrap { overflow-x: auto; }
  table { min-width: 600px; }
  .cat-name { width: 120px; }
}
</style>
</head>
<body>

<div class="topbar">
  <div class="logo">Expense<span>Snap</span></div>
  <div class="topbar-actions">
    <button class="btn btn-ghost" onclick="exportExcel()">📥 Export Excel</button>
  </div>
</div>

<nav class="nav">
  <button class="nav-tab active" data-tab="upload">Upload</button>
  <button class="nav-tab" data-tab="dashboard">Dashboard</button>
  <button class="nav-tab" data-tab="expenses">All Expenses</button>
</nav>

<!-- Upload Section -->
<div class="main">
<div id="upload" class="section active">
  <div class="upload-zone" id="dropZone">
    <div class="upload-icon">📸</div>
    <div class="upload-title">Drop receipt here or tap to upload</div>
    <div class="upload-sub">Supports JPG, PNG, WebP, HEIC • Phone camera works great</div>
    <input type="file" class="upload-input" id="fileInput" accept="image/*" capture="environment" multiple>
  </div>

  <div id="recentUploads" style="margin-top:28px;"></div>
</div>

<!-- Dashboard Section -->
<div id="dashboard" class="section">
  <div class="stats-grid" id="statsGrid"></div>
  <div class="cat-section" id="catSection">
    <h3>Spending by Category</h3>
    <div id="catBars"></div>
  </div>
</div>

<!-- Expenses Section -->
<div id="expenses" class="section">
  <div class="table-wrap">
    <div class="table-header">
      <h3>All Expenses</h3>
      <button class="btn btn-ghost" onclick="exportExcel()" style="font-size:13px;padding:8px 16px;">📥 Export</button>
    </div>
    <div id="expenseTable"></div>
  </div>
</div>
</div>

<!-- Processing Overlay -->
<div class="processing" id="processing">
  <div class="processing-card">
    <div class="spinner"></div>
    <div style="font-size:18px;font-weight:600;margin-bottom:8px;">Scanning Receipt...</div>
    <div style="color:var(--text2);font-size:14px;">Claude is reading your receipt</div>
  </div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script>
// ── Navigation ──
document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(tab.dataset.tab).classList.add('active');
    if (tab.dataset.tab === 'dashboard') loadDashboard();
    if (tab.dataset.tab === 'expenses') loadExpenses();
  });
});

// ── Upload ──
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
  e.preventDefault(); dropZone.classList.remove('dragover');
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener('change', e => handleFiles(e.target.files));

async function handleFiles(files) {
  for (const file of files) {
    await uploadFile(file);
  }
  fileInput.value = '';
}

async function uploadFile(file) {
  const proc = document.getElementById('processing');
  proc.classList.add('active');

  const formData = new FormData();
  formData.append('receipt', file);

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await res.json();

    if (data.success) {
      showToast(`✓ ${data.expense.vendor} — ${data.expense.currency} ${data.expense.total}`, 'success');
      showRecentUpload(data.expense);
    } else {
      showToast('Failed: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (err) {
    showToast('Upload failed: ' + err.message, 'error');
  }
  proc.classList.remove('active');
}

function showRecentUpload(exp) {
  const container = document.getElementById('recentUploads');
  const card = document.createElement('div');
  card.style.cssText = `background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;animation:fadeIn 0.3s ease;`;
  card.innerHTML = `
    <div>
      <div style="font-weight:600;margin-bottom:4px;">${exp.vendor || 'Unknown'}</div>
      <div style="font-size:13px;color:var(--text2);">${exp.date} · ${exp.category} · ${exp.items || ''}</div>
    </div>
    <div style="font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:600;color:var(--green);">
      ${exp.currency} ${Number(exp.total).toFixed(2)}
    </div>`;
  container.prepend(card);
}

// ── Dashboard ──
async function loadDashboard() {
  try {
    const res = await fetch('/api/dashboard');
    const data = await res.json();

    document.getElementById('statsGrid').innerHTML = `
      <div class="stat-card">
        <div class="stat-label">Total Spent</div>
        <div class="stat-value green">$${data.total.toFixed(2)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Receipts Scanned</div>
        <div class="stat-value">${data.count}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Categories</div>
        <div class="stat-value">${Object.keys(data.by_category).length}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Average per Receipt</div>
        <div class="stat-value">$${data.count ? (data.total / data.count).toFixed(2) : '0.00'}</div>
      </div>`;

    const cats = Object.entries(data.by_category).sort((a,b) => b[1]-a[1]);
    const maxVal = cats.length ? cats[0][1] : 1;
    const colors = ['#6C5CE7','#00D2A0','#FDCB6E','#74B9FF','#FF6B6B','#A29BFE','#FD79A8','#55E6C1','#FDA7DF','#778CA3'];

    document.getElementById('catBars').innerHTML = cats.map(([ cat, amt], i) => `
      <div class="cat-row">
        <div class="cat-name">${cat}</div>
        <div class="cat-bar-bg">
          <div class="cat-bar" style="width:${(amt/maxVal*100)}%;background:${colors[i%colors.length]};"></div>
        </div>
        <div class="cat-amount">$${amt.toFixed(2)}</div>
      </div>`).join('');

    if (!cats.length) {
      document.getElementById('catBars').innerHTML = '<div class="empty-state"><p>No expenses yet. Upload a receipt to get started!</p></div>';
    }
  } catch(err) {
    console.error(err);
  }
}

// ── Expenses Table ──
async function loadExpenses() {
  try {
    const res = await fetch('/api/expenses');
    const expenses = await res.json();

    if (!expenses.length) {
      document.getElementById('expenseTable').innerHTML = '<div class="empty-state"><div class="icon">🧾</div><p>No expenses yet. Upload your first receipt!</p></div>';
      return;
    }

    document.getElementById('expenseTable').innerHTML = `
      <table>
        <thead><tr>
          <th>Date</th><th>Vendor</th><th>Category</th><th>Total</th><th>Currency</th><th>Payment</th><th></th>
        </tr></thead>
        <tbody>
          ${expenses.map(e => `<tr>
            <td>${e.date}</td>
            <td><strong>${e.vendor}</strong><br><span style="font-size:12px;color:var(--text2)">${e.location || ''}</span></td>
            <td><span class="cat-badge">${e.category}</span></td>
            <td class="amount">${Number(e.total).toFixed(2)}</td>
            <td>${e.currency}</td>
            <td style="font-size:13px;color:var(--text2)">${e.payment_method || ''}</td>
            <td><button class="delete-btn" onclick="deleteExpense('${e.id}')">✕</button></td>
          </tr>`).join('')}
        </tbody>
      </table>`;
  } catch(err) {
    console.error(err);
  }
}

async function deleteExpense(id) {
  if (!confirm('Delete this expense?')) return;
  await fetch(`/api/expenses/${id}`, { method: 'DELETE' });
  loadExpenses();
  showToast('Expense deleted', 'success');
}

// ── Export ──
function exportExcel() {
  window.location.href = '/api/export';
}

// ── Toast ──
function showToast(msg, type='success') {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = `toast ${type} show`;
  setTimeout(() => toast.classList.remove('show'), 3500);
}
</script>
</body>
</html>
"""

init_db()  if __name__ == '__main__':
    init_db()
    print("\n" + "="*50)
    print("  🧾 ExpenseSnap is running!")
    print("="*50)
    print(f"  Open: http://localhost:5000")
    print(f"  Press Ctrl+C to stop")
    print("="*50 + "\n")
    app.run(host='0.0.0.0', port=5000, debug=True)
