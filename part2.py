# beckup_part2.py
# Cleaned & fixed single-file program
# - Multi-product PurchaseWindow (keeps only new version)
# - SaleWindow (multi-product)
# - Stock, Ledger, Dashboard, Bill PDF
# - Fixed datetime import/usage
#
# Source: based on your uploaded file. :contentReference[oaicite:1]{index=1}

import os
import json
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors

import firebase_admin
from firebase_admin import credentials, db

# -------------------------
# Firebase Setup
# -------------------------
cred = credentials.Certificate("firebase_key.json")

firebase_admin.initialize_app(cred, {
    "databaseURL": "https://inventory-677b9-default-rtdb.firebaseio.com/"
})

# -------------------------
# Constants / filenames
# -------------------------
PURCHASE_FILE = "purchase.json"
SALE_FILE = "sale.json"
STOCK_FILE = "stock.json"
LEDGER_FILE = "ledger.json"
RECEIPTS_DIR = "receipts"
BILLS_DIR = "bills"

# -------------------------
# Basic file helpers
# -------------------------
def ensure_files_exist():
    """Make sure required files and folders exist."""
    os.makedirs(RECEIPTS_DIR, exist_ok=True)
    os.makedirs(BILLS_DIR, exist_ok=True)
    for fn, default in [
        (PURCHASE_FILE, []),
        (SALE_FILE, []),
        (STOCK_FILE, []),
        (LEDGER_FILE, {}),
    ]:
        if not os.path.exists(fn):
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(default, f, indent=2, ensure_ascii=False)

def load_json(fn):
    """Load JSON, return empty list or dict on error depending on file."""
    try:
        with open(fn, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [] if fn != LEDGER_FILE else {}

def save_json(fn, data):
    """Save object as JSON with indentation."""
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def save_to_firebase(path, data):
    ref = db.reference(path)
    ref.set(data)
    print("Firebase Updated:", path)


def next_id(fn):
    """Return next integer id for records in fn (based on existing 'id' fields)."""
    recs = load_json(fn)
    if not isinstance(recs, list) or len(recs) == 0:
        return 1
    try:
        return max(int(r.get("id", 0)) for r in recs) + 1
    except Exception:
        return len(recs) + 1

def next_invoice(prefix, fn):
    """Create a readable invoice string using prefix + date + seq number."""
    seq = next_id(fn)
    date_part = datetime.now().strftime("%y%m%d")
    return f"{prefix}{date_part}{seq:04d}"

# -------------------------
# Calculation helpers
# -------------------------
def calc_totals(qty, rate, discount_pct=0, tax_pct=0):
    """
    Calculate subtotal, discount, tax, total.
    Returns rounded (subtotal, discount_amount, tax_amount, total).
    """
    try:
        q = float(qty)
        r = float(rate)
        d = float(discount_pct or 0)
        t = float(tax_pct or 0)
    except Exception:
        q, r, d, t = 0.0, 0.0, 0.0, 0.0

    subtotal = q * r
    discount_amt = subtotal * (d / 100.0)
    taxable = subtotal - discount_amt
    tax_amt = taxable * (t / 100.0)
    total = taxable + tax_amt

    # Round to 2 decimals for storing/display
    return round(subtotal, 2), round(discount_amt, 2), round(tax_amt, 2), round(total, 2)

# -------------------------
# Stock & Ledger computation
# -------------------------
def compute_stock_from_files():
    """
    Build stock summary from purchases and sales.
    For each product compute: purchased, sold, available, avg_price, total value, latest_invoice.
    Returns a list of dicts to be saved as stock.json.
    Handles both old single-product records and new multi-product records.
    """
    purchases = load_json(PURCHASE_FILE)
    sales = load_json(SALE_FILE)

    products = {}

    # accumulate purchases (support both formats)
    for p in purchases:
        prods = p.get("products")
        if isinstance(prods, list):
            for line in prods:
                name = str(line.get("product", "")).strip()
                if not name:
                    continue
                rec = products.setdefault(name, {
                    "product": name,
                    "purchased": 0,
                    "sold": 0,
                    "purchase_value": 0.0,
                    "unit": line.get("unit", "") or "pcs",
                    "latest_invoice": p.get("invoice", "") or "",
                    "latest_purchase_date": p.get("date", "") or ""
                })
                try:
                    qty = float(line.get("qty", 0) or 0)
                except:
                    qty = 0
                try:
                    rate = float(line.get("rate", 0) or 0)
                except:
                    rate = 0.0
                rec["purchased"] += qty
                rec["purchase_value"] += qty * rate
                # prefer unit and latest invoice/date
                if line.get("unit"):
                    rec["unit"] = line.get("unit")
                date_str = p.get("date", "")
                if date_str and (not rec.get("latest_purchase_date") or date_str > rec.get("latest_purchase_date", "")):
                    rec["latest_purchase_date"] = date_str
                    rec["latest_invoice"] = p.get("invoice", "")
        else:
            # fallback single-product purchase record
            name = str(p.get("product", "")).strip()
            if not name:
                continue
            rec = products.setdefault(name, {
                "product": name,
                "purchased": 0,
                "sold": 0,
                "purchase_value": 0.0,
                "unit": p.get("unit", "") or "pcs",
                "latest_invoice": p.get("invoice", "") or "",
                "latest_purchase_date": p.get("date", "") or ""
            })
            try:
                qty = float(p.get("qty", 0) or 0)
            except:
                qty = 0
            try:
                rate = float(p.get("rate", 0) or 0)
            except:
                rate = 0.0
            rec["purchased"] += qty
            rec["purchase_value"] += qty * rate
            date_str = p.get("date", "")
            if date_str and (not rec.get("latest_purchase_date") or date_str > rec.get("latest_purchase_date", "")):
                rec["latest_purchase_date"] = date_str
                rec["latest_invoice"] = p.get("invoice", "")

    # accumulate sales (support multi-product)
    for s in sales:
        prods = s.get("products")
        if isinstance(prods, list):
            for line in prods:
                name = str(line.get("product", "")).strip()
                if not name:
                    continue
                rec = products.setdefault(name, {
                    "product": name,
                    "purchased": 0,
                    "sold": 0,
                    "purchase_value": 0.0,
                    "unit": line.get("unit", "") or "pcs",
                    "latest_invoice": "",
                    "latest_purchase_date": ""
                })
                try:
                    qty = float(line.get("qty", 0) or 0)
                except:
                    qty = 0
                rec["sold"] += qty
                if line.get("unit"):
                    rec["unit"] = line.get("unit")
        else:
            name = str(s.get("product", "")).strip()
            if not name:
                continue
            rec = products.setdefault(name, {
                "product": name,
                "purchased": 0,
                "sold": 0,
                "purchase_value": 0.0,
                "unit": s.get("unit", "") or "pcs",
                "latest_invoice": "",
                "latest_purchase_date": ""
            })
            try:
                qty = float(s.get("qty", 0) or 0)
            except:
                qty = 0
            rec["sold"] += qty

    # build final summary list
    summary = []
    for name, rec in products.items():
        purchased = rec.get("purchased", 0)
        sold = rec.get("sold", 0)
        available = max(0, purchased - sold)
        avg_price = (rec.get("purchase_value", 0.0) / purchased) if purchased > 0 else 0.0
        value = round(available * avg_price, 2)
        summary.append({
            "product": name,
            "purchased": purchased,
            "sold": sold,
            "available": available,
            "avg_price": round(avg_price, 2),
            "value": value,
            "unit": rec.get("unit", "pcs"),
            "latest_invoice": rec.get("latest_invoice", "")
        })
    return summary

def recompute_ledger():
    purchases = load_json(PURCHASE_FILE)
    sales = load_json(SALE_FILE)

    # Load existing ledger (contains manual entries)
    existing = load_json(LEDGER_FILE)
    if not isinstance(existing, dict):
        existing = {}

    ledger = {}

    # Copy old ledger safely
    for party, data in existing.items():
        ledger[party] = {
            "transactions": [t.copy() for t in data.get("transactions", [])],
            "purchases": 0.0,
            "sales": 0.0
        }

    # Helper to find matching purchase/sale row
    def find_txn(txns, tx_type, invoice):
        for i, t in enumerate(txns):
            if t.get("type") == tx_type and t.get("invoice") == invoice:
                return i
        return None

    # Process PURCHASE entries
    for p in purchases:
        party = p.get("party")
        if not party:
            continue
        ledger.setdefault(party, {"transactions": [], "purchases": 0.0, "sales": 0.0})

        amount = float(p.get("total", 0))
        ledger[party]["purchases"] += amount

        auto_txn = {
            "date": p.get("date"),
            "type": "Purchase",
            "invoice": p.get("invoice"),
            "credit": "",
            "debit": "",
            "remaining": amount,
            "amount": amount
        }

        idx = find_txn(ledger[party]["transactions"], "Purchase", p.get("invoice"))
        if idx is not None:
            ledger[party]["transactions"][idx].update(auto_txn)
        else:
            ledger[party]["transactions"].append(auto_txn)

    # Process SALE entries
    for s in sales:
        party = s.get("party")
        if not party:
            continue
        ledger.setdefault(party, {"transactions": [], "purchases": 0.0, "sales": 0.0})

        amount = float(s.get("total", 0))
        ledger[party]["sales"] += amount

        auto_txn = {
            "date": s.get("date"),
            "type": "Sale",
            "invoice": s.get("invoice"),
            "credit": "",
            "debit": "",
            "remaining": amount,
            "amount": amount
        }

        idx = find_txn(ledger[party]["transactions"], "Sale", s.get("invoice"))
        if idx is not None:
            ledger[party]["transactions"][idx].update(auto_txn)
        else:
            ledger[party]["transactions"].append(auto_txn)

    # Now recalc remaining for each party WITHOUT deleting manual rows
    for party, ent in ledger.items():
        recalc_party_transactions(ent)

    save_json(LEDGER_FILE, ledger)
    return ledger

#------------------------------
# Recalculate the remaining values for all rows WITHOUT deleting any ro
#-------------------------------------------------
def recalc_party_transactions(ledger_party):
    """
    Recalculate the remaining values for all rows WITHOUT deleting any row.
    Formula:
        remaining = previous_remaining - credit + debit
    Starts from first row.
    """
    txns = ledger_party.get("transactions", [])
    if not txns:
        return

    def to_float(v):
        try: return float(v or 0)
        except: return 0.0

    # First row sets the starting remaining
    first = txns[0]
    prev_remaining = to_float(first.get("remaining") or first.get("amount") or 0)

    first["remaining"] = round(prev_remaining, 2)
    first["amount"] = round(to_float(first.get("amount")), 2)

    # Propagate remaining for all next rows
    for i in range(1, len(txns)):
        t = txns[i]
        credit = to_float(t.get("credit"))
        debit  = to_float(t.get("debit"))

        new_remaining = prev_remaining - credit + debit

        t["remaining"] = round(new_remaining, 2)
        t["amount"] = round(to_float(t.get("amount")), 2)

        prev_remaining = new_remaining

    ledger_party["last_amount"] = round(prev_remaining, 2)


# -------------------------
# Dashboard helpers
# -------------------------
def total_purchases_amount():
    p = load_json(PURCHASE_FILE)
    return round(sum(float(x.get("total", 0) or 0) for x in p), 2)

def total_sales_amount():
    s = load_json(SALE_FILE)
    return round(sum(float(x.get("total", 0) or 0) for x in s), 2)

def total_stock_value():
    s = load_json(STOCK_FILE)
    if not isinstance(s, list):
        return 0.0
    return round(sum(float(x.get("value", 0) or 0) for x in s), 2)

def profit_or_loss():
    return round(total_sales_amount() - total_purchases_amount(), 2)

# -------------------------
# Small utilities (UI)
# -------------------------
def color_rows(tree):
    """Alternate row background for readability."""
    for i, iid in enumerate(tree.get_children()):
        tag = "even" if i % 2 == 0 else "odd"
        tree.item(iid, tags=(tag,))
    tree.tag_configure("even", background="white")
    tree.tag_configure("odd", background="#f1fbff")

# ---------- Helper: refresh stock window if open ----------
def refresh_stock_if_open(parent):
    try:
        for w in parent.winfo_children():
            if isinstance(w, StockWindow):
                try:
                    w.refresh_and_save_stock()
                except:
                    try:
                        w.load_stock()
                    except:
                        pass
                break
    except:
        pass
    
# -------------------------
# Receipt & Bill helpers
# -------------------------
def save_receipt_text(parent, record, kind="Purchase"):
    """Open receipt preview window and allow saving as PDF."""
    top = tk.Toplevel(parent)
    top.title(f"{kind} Receipt")
    top.geometry("420x620")
    top.transient(parent)
    top.grab_set()

    # Header info and product lines
    lines = [
        f"{kind.upper()} RECEIPT",
        "-" * 40,
        f"Invoice : {record.get('invoice','')}",
        f"Date    : {record.get('date','')}",
        f"Party   : {record.get('party','')}",
        f"Phone   : {record.get('phone','')}",
        f"Address : {record.get('address','')}",
        f"GST No  : {record.get('gst_no','')}",
        f"Place   : {record.get('place_of_supply','')}",
        "-" * 40,
        "PRODUCTS:"
    ]

    for p in record.get("products", []):
        lines.extend([
            f"Product : {p.get('product','')}",
            f"Qty     : {p.get('qty','')} {p.get('unit','')}",
            f"Rate    : {p.get('rate','')}",
            f"Subtotal: {p.get('subtotal','')}",
            f"Discount: {p.get('discount_amt','')}",
            f"Tax     : {p.get('tax_amt','')}",
            f"Total   : {p.get('total','')}",
            "-" * 40
        ])

    lines.extend([
        f"Grand Total: {record.get('total','')}",
        f"Authorized: {record.get('auth_sign','')}",
        "-" * 40,
        "Generated by Smart Sale & Purchase Manager"
    ])

    text = tk.Text(top, font=("Consolas", 11))
    text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    text.insert(tk.END, "\n".join(lines))
    text.config(state="disabled")

    btn_frame = tk.Frame(top, pady=8)
    btn_frame.pack(fill=tk.X)

    def save_as_pdf():
        folder = RECEIPTS_DIR
        os.makedirs(folder, exist_ok=True)
        filename = f"{kind.lower()}_receipt_{record.get('invoice')}.pdf"
        path = os.path.join(folder, filename)
        try:
            c = pdf_canvas.Canvas(path, pagesize=A4)
            width, height = A4
            y = height - 80
            c.setFont("Helvetica-Bold", 14)
            c.drawCentredString(width / 2, y, f"{kind.upper()} RECEIPT")
            c.setFont("Helvetica", 11)
            y -= 30
            for line in lines[2:]:
                c.drawString(70, y, line)
                y -= 18
                if y < 50:
                    c.showPage()
                    y = height - 80
            c.setFont("Helvetica-Oblique", 10)
            c.drawCentredString(width / 2, 60, "Generated by Smart Sale & Purchase Manager")
            c.save()
            messagebox.showinfo("Saved", f"Receipt saved at:\n{path}", parent=top)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save PDF:\n{e}", parent=top)

    tk.Button(btn_frame, text="💾 Save as PDF", font=("Arial", 10, "bold"),
              bg="#28a745", fg="white", padx=10, pady=5, command=save_as_pdf).pack(side=tk.LEFT, padx=10)
    tk.Button(btn_frame, text="❌ Close", font=("Arial", 10, "bold"),
              bg="#dc3545", fg="white", padx=10, pady=5, command=top.destroy).pack(side=tk.RIGHT, padx=10)

# -------------------------
# generate_bill_text
# -------------------------
def generate_bill_text(parent, record, kind="Sale"):
    """Show bill preview and allow saving formatted PDF for Sale or Purchase."""
    win = tk.Toplevel(parent)
    win.title(f"{kind} Bill - {record.get('invoice','')}")
    win.geometry("650x690+1+1")
    win.config(bg="white")
    win.resizable(False, False)
    canvas_w = tk.Canvas(win, bg="white", width=550, height=700)
    canvas_w.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar = tk.Scrollbar(win, orient="vertical", command=canvas_w.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas_w.configure(yscrollcommand=scrollbar.set)
    frame = tk.Frame(canvas_w, bg="white", width=600)
    canvas_w.create_window((0, 0), window=frame, anchor="nw")
    def on_frame_configure(event):
        canvas_w.configure(scrollregion=canvas_w.bbox("all"))
    frame.bind("<Configure>", on_frame_configure)

    # header
    tk.Label(frame, text="GST No. 07AIAPV0703B2ZU            TAX INVOICE           M: 9971052240",
             font=("Arial", 10, "bold"), fg="#4a148c", bg="white").pack(pady=(10, 0))
    tk.Label(frame, text="Kidzibooks Publications",
             font=("Arial", 16, "bold"), fg="#4a148c", bg="white").pack()
    tk.Label(frame, text="A-32, Second Floor, Rishi Nagar, Rani Bagh, Delhi",
             font=("Arial", 10), fg="#4a148c", bg="white").pack()

    date_frame = tk.Frame(frame, bg="white")
    date_frame.pack(fill=tk.X, padx=15, pady=10)
    tk.Label(date_frame, text=f"Date: {record.get('date','')}", font=("Arial", 10), bg="white").pack(side=tk.LEFT)
    tk.Label(date_frame, text=f"Invoice No: {record.get('invoice','')}", font=("Arial", 10), bg="white").pack(side=tk.RIGHT)

    ttk.Separator(frame).pack(fill=tk.X, pady=10)

    # party info
    info = tk.Frame(frame, bg="white"); info.pack(fill=tk.X, padx=15)
    left = tk.Frame(info, bg="white"); left.pack(side=tk.LEFT, fill=tk.X, expand=True)
    data_left = [
        ("Party / Customer:", record.get("party", "")),
        ("Phone:", record.get("phone", "")),
        ("Address:", record.get("address", ""))
    ]
    for lbl, val in data_left:
        row = tk.Frame(left, bg="white"); row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=lbl, font=("Arial", 10, "bold"), bg="white", width=16, anchor="w").pack(side=tk.LEFT)
        tk.Label(row, text=str(val), font=("Arial", 10), bg="white", anchor="w").pack(side=tk.LEFT)
    right = tk.Frame(info, bg="white"); right.pack(side=tk.RIGHT, padx=10, anchor="ne")
    tk.Label(right, text=f"GST No: {record.get('gst_no','')}", font=("Arial", 10, "bold"), bg="white").pack(anchor="w", pady=2)
    tk.Label(right, text=f"Place of Supply: {record.get('place_of_supply','')}", font=("Arial", 10, "bold"), bg="white").pack(anchor="w", pady=2)

    ttk.Separator(frame).pack(fill=tk.X, pady=10)

    cols = ("SNo", "Product", "PageNo", "HSN", "Qty", "Rate", "Amount")
    tbl = ttk.Treeview(frame, columns=cols, show="headings", height=6)
    tbl.pack(fill=tk.X, padx=15)
    tbl.heading("SNo", text="S.No"); tbl.column("SNo", width=50, anchor="center")
    tbl.heading("Product", text="Product"); tbl.column("Product", width=180, anchor="w")
    tbl.heading("PageNo", text="Page No"); tbl.column("PageNo", width=70, anchor="center")
    tbl.heading("HSN", text="HSN Code"); tbl.column("HSN", width=80, anchor="center")
    tbl.heading("Qty", text="Qty"); tbl.column("Qty", width=50, anchor="center")
    tbl.heading("Rate", text="Rate"); tbl.column("Rate", width=80, anchor="center")
    tbl.heading("Amount", text="Amount"); tbl.column("Amount", width=100, anchor="e")

    products = record.get("products", [])
    sno = 1
    for p in products:
        tbl.insert("", "end", values=(
            sno,
            p.get("product", ""),
            p.get("page_no", ""),
            p.get("hsn", ""),
            p.get("qty", ""),
            p.get("rate", ""),
            f"{float(p.get('subtotal', 0)):.2f}"
        ))
        sno += 1

    # editable PageNo & HSN
    def edit_cell(event):
        rowid = tbl.identify_row(event.y)
        colid = tbl.identify_column(event.x)
        if not rowid or not colid:
            return
        try:
            col_index = int(colid.replace("#", "")) - 1
        except:
            return
        if col_index not in (2, 3):
            return
        col_name = cols[col_index]
        bbox = tbl.bbox(rowid, col_name)
        if not bbox:
            return
        x, y, width, height = bbox
        cur_val = tbl.set(rowid, col_name)
        entry = tk.Entry(tbl)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, cur_val)
        entry.focus()
        def save_edit(e=None):
            new_val = entry.get()
            tbl.set(rowid, col_name, new_val)
            entry.destroy()
        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)
    tbl.bind("<Double-1>", edit_cell)

    ttk.Separator(frame).pack(fill=tk.X, pady=10)

    total_frame = tk.Frame(frame, bg="white"); total_frame.pack(fill=tk.X, padx=20)
    bank = tk.Frame(total_frame, bg="white"); bank.pack(side=tk.LEFT, anchor="nw")
    for line in ["Central Bank Of India", "Branch: Pitampura, Delhi-110034", "A/c No: 5322181315", "IFSC Code: CBIN0283490"]:
        tk.Label(bank, text=line, font=("Arial", 10), bg="white").pack(anchor="w")
    totals = tk.Frame(total_frame, bg="white"); totals.pack(side=tk.RIGHT, anchor="ne")
    vals = [
        ("Subtotal:", record.get("subtotal", 0)),
        ("Discount:", record.get("discount_amt", 0)),
        ("Tax:", record.get("tax_amt", 0)),
        ("Grand Total:", record.get("total", 0)),
    ]
    for name, v in vals:
        r = tk.Frame(totals, bg="white"); r.pack(anchor="e")
        tk.Label(r, text=name, font=("Arial", 10, "bold"), bg="white").pack(side=tk.LEFT)
        tk.Label(r, text=f"{float(v):.2f}", font=("Arial", 10), bg="white").pack(side=tk.LEFT, padx=6)

    ttk.Separator(frame).pack(fill=tk.X, pady=10)

    tk.Label(frame, text="Terms and Conditions:", font=("Arial", 10, "bold"), bg="white").pack(anchor="w", padx=15)
    for t in [
        "1. Goods once sold will not be taken back",
        "2. Our responsibility ceases once the goods are delivered.",
        "3. All disputes are subjected to “Delhi” Jurisdiction only.",
        "4. Cheque in favour of Kidzibooks Publications"
    ]:
        tk.Label(frame, text=t, font=("Arial", 9), bg="white", anchor="w").pack(fill=tk.X, padx=15)

    sig = tk.Frame(frame, bg="white"); sig.pack(fill=tk.X, padx=15, pady=10)
    tk.Label(sig, text="For Kidzibooks Publications", font=("Arial", 10, "bold"), bg="white").pack(side=tk.LEFT)
    tk.Label(sig, text=record.get("auth_sign", ""), font=("Arial", 10), bg="white").pack(side=tk.RIGHT)
    tk.Label(sig, text="Authorized Signatory", font=("Arial", 10, "bold"), bg="white").pack(side=tk.RIGHT)

    def save_bill_pdf():
        updated_products = []
        for child in tbl.get_children():
            vals = tbl.item(child)["values"]
            updated_products.append([
                vals[0], vals[1], vals[2], vals[3],
                str(vals[4]), str(vals[5]), str(vals[6])
            ])
        folder = BILLS_DIR
        os.makedirs(folder, exist_ok=True)
        filename = f"Invoice_{record.get('invoice','')}.pdf"
        file_pdf = os.path.join(folder, filename)
        c = pdf_canvas.Canvas(file_pdf, pagesize=A4)
        width, height = A4
        m = 20 * mm
        x = m
        y = height - m
        # header
        c.setFillColorRGB(0, 0.2, 0.5)
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(width/2, y, "Kidzibooks Publications")
        y -= 18
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawCentredString(width/2, y, "A-32, Second Floor, Rishi Nagar, Rani Bagh, Delhi")
        y -= 14
        c.setFont("Helvetica-Bold", 11)
        c.setFillColorRGB(0.2, 0.4, 0.1)
        c.drawCentredString(width/2, y, "GST No. 07AIAPV0703B2ZU      TAX INVOICE      M: 9971052240")
        y -= 22
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 9)
        c.drawString(x, y, f"Date: {record.get('date','')}")
        c.drawRightString(width-m, y, f"Invoice No: {record.get('invoice','')}")
        y -= 20
        c.drawString(x, y, f"Party: {record.get('party','')}")
        y -= 12
        c.drawString(x, y, f"Phone: {record.get('phone','')}")
        y -= 12
        c.drawString(x, y, f"Address: {record.get('address','')}")
        y -= 20
        c.drawRightString(width-m, y+40, f"GST No: {record.get('gst_no','')}")
        c.drawRightString(width-m, y+25, f"Place of Supply: {record.get('place_of_supply','')}")
        # table
        data = [["S.No", "Product", "Page No", "HSN", "Qty", "Rate", "Amount"]]
        # Convert tbl rows to printable rows
        for child in tbl.get_children():
            vals = tbl.item(child)["values"]
            data.append([str(v) for v in vals])
        table = Table(data, colWidths=[40, 150, 60, 60, 50, 60, 70])
        table_style = TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.8, colors.darkgray),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d1e0ff")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.darkblue),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ])
        table.setStyle(table_style)
        table_width, table_height = table.wrapOn(c, width, height)
        table.drawOn(c, x, y - table_height)
        y_after_table = (y - table_height) - 20
        left_rows = [
            "Central Bank Of India",
            "Branch: Pitampura, Delhi-110034",
            "A/c No: 5322181315",
            "IFSC Code: CBIN0283490"
        ]
        right_rows = [
            f"Subtotal: {float(record.get('subtotal', 0)):.2f}",
            f"Discount: {float(record.get('discount_amt', 0)):.2f}",
            f"Tax: {float(record.get('tax_amt', 0)):.2f}",
            f"Grand Total: {float(record.get('total', 0)):.2f}"
        ]
        y_side = y_after_table
        for left_text, right_text in zip(left_rows, right_rows):
            c.setFillColorRGB(0.2, 0.2, 0.2)
            c.drawString(x, y_side, left_text)
            c.setFillColorRGB(0.1, 0.1, 0.5)
            c.drawRightString(width - m, y_side, right_text)
            y_side -= 12
        y_terms = y_side - 15
        c.setFillColorRGB(0, 0, 0)
        c.drawString(x, y_terms, "Terms and Conditions:")
        y_terms -= 12
        for tt in [
            "Goods once sold will not be taken back.",
            "Our responsibility ceases once the goods are delivered.",
            "All disputes subject to Delhi Jurisdiction.",
            "Cheque in favour of Kidzibooks Publications"
        ]:
            c.drawString(x, y_terms, tt)
            y_terms -= 12
        y_sig = y_terms - 20
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(0.2, 0, 0.4)
        c.drawString(x, y_sig, "For Kidzibooks Publications")
        auth_name = record.get("auth_sign", "").strip() or " "
        y_sig -= 10
        c.setFillColorRGB(0.05, 0.05, 0.05)
        c.drawRightString(width - m, y_sig, auth_name)
        y_sig -= 14
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.2, 0.2, 0.6)
        c.drawRightString(width - m, y_sig, "Authorized Sign")
        c.showPage()
        c.save()
        messagebox.showinfo("Saved", f"PDF saved:\n{file_pdf}",parent=win)

    tk.Button(frame, text="Save Bill as PDF", command=save_bill_pdf,
              bg="#6f42c1", fg="white", font=("Arial",12,"bold")).pack(pady=20)

# -------------------------
# Main Dashboard App
# -------------------------
class DashboardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        ensure_files_exist()

        self.title("Simple Inventory & Accounting (Kidzibooks)")
        self.geometry("1360x700+0+0")
        self.config(bg="#E8EAF6")

        self._build_ui()
        self.refresh_dashboard()

    # ============================================================
    # BUILD UI
    # ============================================================
    def _build_ui(self):

        # ---------------- TOP BAR ----------------
        top = tk.Frame(self, bg="#0a4661", height=60)
        top.pack(fill=tk.X)

        tk.Label(
            top,
            text=" Simple Inventory & Accounting ",
            bg="#0a4661",
            fg="white",
            font=("Arial", 20, "bold")
        ).pack(side=tk.LEFT, padx=12, pady=8)

        # BUTTON STYLES
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Purchase.TButton", background="#2E7D32", foreground="white",
                        font=("Arial", 11, "bold"), padding=6)
        style.configure("Sale.TButton", background="#DC3545", foreground="white",
                        font=("Arial", 11, "bold"), padding=6)
        style.configure("Stock.TButton", background="#17a2b8", foreground="white",
                        font=("Arial", 11, "bold"), padding=6)
        style.configure("Ledger.TButton", background="#6f42c1", foreground="white",
                        font=("Arial", 11, "bold"), padding=6)

        ttk.Button(top, text="Exit", style="Stock.TButton",
                   command=self.on_exit).pack(side=tk.RIGHT, padx=12, pady=8)

        # ---------------- KPI CARDS ----------------
        cards = tk.Frame(self, pady=10, bg="#E8EAF6")
        cards.pack(fill=tk.X)

        self.card_vars = []
        titles = ["Total Purchases", "Total Sales", "Stock Value", "Profit/Loss"]
        colors = ["#28a745", "#007bff", "#17a2b8", "#ffc107"]

        for title, col in zip(titles, colors):
            f = tk.Frame(cards, bd=5, relief=tk.RIDGE,
                         padx=12, pady=8, bg=col, width=200, height=90)
            f.pack(side=tk.LEFT, padx=10, ipadx=8, ipady=8)
            f.pack_propagate(False)

            tk.Label(f, text=title, font=("Arial", 10, "bold"),
                     bg=col, fg="white").pack(anchor="w")

            v = tk.StringVar(value="₹ 0")
            tk.Label(f, textvariable=v, font=("Arial", 16, "bold"),
                     bg=col, fg="white").pack()

            self.card_vars.append(v)

        ttk.Button(cards, text="Refresh",style="Ledger.TButton",
                   command=self.refresh_dashboard).pack(side=tk.RIGHT, padx=20)

        # ---------------- MAIN BUTTONS ----------------
        btns = tk.Frame(self, pady=8, bg="#E8EAF6")
        btns.pack(fill=tk.X)

        ttk.Button(btns, text="Purchase", style="Purchase.TButton",
                   command=lambda: PurchaseWindow(self)).pack(side=tk.LEFT, padx=6)

        ttk.Button(btns, text="Sale", style="Sale.TButton",
                   command=lambda: SaleWindow(self)).pack(side=tk.LEFT, padx=6)

        ttk.Button(btns, text="Stock", style="Stock.TButton",
                   command=lambda: StockWindow(self)).pack(side=tk.LEFT, padx=6)

        ttk.Button(btns, text="Ledger", style="Ledger.TButton",
                   command=lambda: LedgerWindow(self)).pack(side=tk.LEFT, padx=6)

        # ---------------- LISTS (LEFT/RIGHT) ----------------
        lists = tk.Frame(self, bg="#E8EAF6")
        lists.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # LEFT SIDE - PURCHASES
        left = tk.Frame(lists, bg="#E8EAF6")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

        tk.Label(left, text="Latest Purchases", font=("Arial", 12, "bold"),
                 bg="#E8EAF6").pack(anchor="w")

        p_frame = tk.Frame(left, bg="#E8EAF6")
        p_frame.pack(fill=tk.BOTH, expand=True)

        self.p_tree = ttk.Treeview(
            p_frame,
            columns=("invoice", "date", "party", "product", "total"),
            show="headings",
            height=12
        )

        style.configure("Treeview.Heading", background="#4A148C",
                        foreground="white", font=("Arial", 12, "bold"))
        style.configure("Treeview", rowheight=30, font=("Arial", 10))

        for col, w in [("invoice", 80), ("date", 120), ("party", 120),
                       ("product", 150), ("total", 80)]:
            self.p_tree.heading(col, text=col.title())
            self.p_tree.column(col, width=w, anchor="center")

        p_scroll = ttk.Scrollbar(p_frame, orient="vertical",
                                 command=self.p_tree.yview)
        self.p_tree.configure(yscroll=p_scroll.set)

        self.p_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        p_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # RIGHT SIDE - SALES
        right = tk.Frame(lists, bg="#E8EAF6")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8)

        tk.Label(right, text="Latest Sales", font=("Arial", 12, "bold"),
                 bg="#E8EAF6").pack(anchor="w")

        s_frame = tk.Frame(right, bg="#E8EAF6")
        s_frame.pack(fill=tk.BOTH, expand=True)

        self.s_tree = ttk.Treeview(
            s_frame,
            columns=("invoice", "date", "party", "product", "total"),
            show="headings",
            height=12
        )

        for col, w in [("invoice", 80), ("date", 120), ("party", 120),
                       ("product", 150), ("total", 80)]:
            self.s_tree.heading(col, text=col.title())
            self.s_tree.column(col, width=w, anchor="center")

        s_scroll = ttk.Scrollbar(s_frame, orient="vertical",
                                 command=self.s_tree.yview)
        self.s_tree.configure(yscroll=s_scroll.set)

        self.s_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        s_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Recolor rows
        color_rows(self.p_tree)
        color_rows(self.s_tree)

    # ============================================================
    # REFRESH DASHBOARD DATA
    # ============================================================
    def refresh_dashboard(self):
        tp = total_purchases_amount()
        ts = total_sales_amount()
        sv = total_stock_value()
        pl = profit_or_loss()

        self.card_vars[0].set(f"₹ {tp}")
        self.card_vars[1].set(f"₹ {ts}")
        self.card_vars[2].set(f"₹ {sv}")
        self.card_vars[3].set(f"₹ {pl}")

        # Load latest 12 records in each table
        for tree, fn in [(self.p_tree, PURCHASE_FILE),
                         (self.s_tree, SALE_FILE)]:

            tree.delete(*tree.get_children())
            recs = load_json(fn)
            latest = sorted(recs, key=lambda r: r.get("date", ""),
                            reverse=True)[:12]

            for r in latest:
                product_display = r.get("product", "")

                if not product_display:
                    if isinstance(r.get("products"), list) and r.get("products"):
                        product_display = ", ".join(
                            [x.get("product", "") for x in r["products"][:2]]
                        )

                tree.insert("", tk.END, values=(
                    r.get("invoice"),
                    r.get("date"),
                    r.get("party"),
                    product_display,
                    r.get("total")
                ))

        color_rows(self.p_tree)
        color_rows(self.s_tree)

    # ============================================================
    # EXIT
    # ============================================================
    def on_exit(self):
        if messagebox.askyesno("Exit", "Close application?"):
            self.destroy()

# -------------------------
# PurchaseWindow 
# -------------------------
class PurchaseWindow(tk.Toplevel):
    """Purchase entry window (multi-product). Styled like SaleWindow."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Purchase Entry")
        self.geometry("1350x610+15+82")
        self.config(bg="#E8EAF6")

        self.product_list = []
        self.selected_product_index = None

        self._build_ui()
        self.load_table()

    def _build_ui(self):
        top = tk.Frame(self, padx=12, pady=4, bg="#E8EAF6")
        top.pack(fill=tk.X)

        self.inputs = {}

        # -----------------------
        # PARTY / SUPPLIER FIELDS
        # -----------------------
        party_layout = [
            [("Party / Supplier", "party"), ("Phone", "phone"), ("Address", "address")],
            [("GST No", "gst_no"), ("Place of Supply", "place_of_supply"), ("Authorized Sign", "auth_sign")],
            [("Notes", "notes"), ("", ""), ("", "")]
        ]

        for r, row_items in enumerate(party_layout):
            for c, (lbl, key) in enumerate(row_items):
                tk.Label(top, text=lbl, font=("Arial", 10, "bold"), bg="#E8EAF6")\
                    .grid(row=r, column=c*2, sticky="w", padx=6, pady=4)

                if key != "":
                    e = tk.Entry(top, width=25, font=("", 11), bg="lightyellow")
                    e.grid(row=r, column=c*2+1, padx=6, pady=4)
                    self.inputs[key] = e

        # -----------------------
        # PRODUCT ENTRY FRAME
        # -----------------------
        prod_frame = tk.LabelFrame(self, text="Add Products for Purchase", padx=10, pady=10)
        prod_frame.pack(fill=tk.X, padx=10, pady=6)

        self.prod_inputs = {}
        product_layout = [
            [("Product", "product"), ("Unit", "unit"), ("Qty", "qty")],
            [("Rate", "rate"), ("Discount %", "discount_pct"), ("Tax %", "tax_pct")]
        ]

        for r, row_items in enumerate(product_layout):
            for c, (lbl, key) in enumerate(row_items):
                tk.Label(prod_frame, text=lbl, font=("Arial", 10, "bold"))\
                    .grid(row=r, column=c*2, sticky="w", padx=10, pady=6)
                e = tk.Entry(prod_frame, width=18, font=("", 11), bg="lightyellow")
                e.grid(row=r, column=c*2+1, padx=6, pady=6)
                self.prod_inputs[key] = e

        self.prod_inputs["unit"].insert(0, "pcs")
        self.prod_inputs["discount_pct"].insert(0, "0")
        self.prod_inputs["tax_pct"].insert(0, "0")

        # -----------------------
        # PRODUCT LINE TABLE (upper)
        # -----------------------
        table_cols = ("product","unit","qty","rate","disc_pct","tax_pct","subtotal","total")

        pro_frame = tk.Frame(self)
        pro_frame.pack(fill=tk.X, padx=10)

        self.pro_tree = ttk.Treeview(pro_frame, columns=table_cols, show="headings", height=3)
        for col in table_cols:
            self.pro_tree.heading(col, text=col.replace("_", " ").title())
            self.pro_tree.column(col, width=120, anchor="center")

        pro_vs = ttk.Scrollbar(pro_frame, orient="vertical", command=self.pro_tree.yview)
        self.pro_tree.configure(yscroll=pro_vs.set)

        self.pro_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pro_vs.pack(side=tk.RIGHT, fill=tk.Y)

        self.pro_tree.bind("<<TreeviewSelect>>", self.on_product_row_select)

        # -----------------------
        # PRODUCT BUTTONS
        # -----------------------
        pl_btns = tk.Frame(self)
        pl_btns.pack(fill=tk.X, padx=10, pady=5)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Add.TButton", background="#28a745", foreground="white",
                        font=("Arial", 11, "bold"), padding=6)
        style.configure("Update.TButton", background="#ff8c00", foreground="white",
                        font=("Arial", 11, "bold"), padding=6)
        style.configure("Delete.TButton", background="#17a2b8", foreground="white",
                        font=("Arial", 11, "bold"), padding=6)
        style.configure("Save.TButton", background="#6f42c1", foreground="white",
                        font=("Arial", 11, "bold"), padding=6)

        ttk.Button(pl_btns, text="Add Product", style="Delete.TButton",
                   command=self.add_product_row).pack(side=tk.LEFT, padx=6)
        ttk.Button(pl_btns, text="Update Product", style="Add.TButton",
                   command=self.update_product_row).pack(side=tk.LEFT, padx=6)
        ttk.Button(pl_btns, text="Remove Product", style="Save.TButton",
                   command=self.remove_product_row).pack(side=tk.LEFT, padx=6)
        ttk.Button(pl_btns, text="Clear All Products", style="Update.TButton",
                   command=self.clear_product_lines).pack(side=tk.LEFT, padx=6)

        # -----------------------
        # ACTION BUTTONS
        # -----------------------
        action = tk.Frame(self, pady=10, bg="#E8EAF6")
        action.pack(fill=tk.X)

        ttk.Button(action, text="Add Purchase", style="Add.TButton", command=self.add_purchase)\
            .pack(side=tk.LEFT, padx=6)
        ttk.Button(action, text="Update Selected", style="Update.TButton", command=self.update_selected)\
            .pack(side=tk.LEFT, padx=6)
        ttk.Button(action, text="Delete Selected", style="Delete.TButton", command=self.delete_selected)\
            .pack(side=tk.LEFT, padx=6)
        ttk.Button(action, text="Save Receipt", style="Save.TButton", command=self.save_receipt_selected)\
            .pack(side=tk.LEFT, padx=6)
        ttk.Button(action, text="Show Bill", style="Save.TButton", command=self.show_bill_selected)\
            .pack(side=tk.LEFT, padx=6)
        ttk.Button(action, text="Clear", style="Delete.TButton", command=self.clear_inputs)\
            .pack(side=tk.LEFT, padx=6)

        # -----------------------
        # SEARCH
        # -----------------------
        search = tk.Frame(self, bg="#E8EAF6")
        search.pack(fill=tk.X, padx=10)
        tk.Label(search, text="Search:", font=("Arial", 10, "bold"), bg="#E8EAF6")\
            .pack(side=tk.LEFT)
        self.search_var = tk.Entry(search, width=40, bg="lightyellow")
        self.search_var.pack(side=tk.LEFT, padx=6)
        self.search_var.bind("<KeyRelease>", lambda e: self.load_table())

        # -----------------------
        # PURCHASE RECORD TABLE (BOTTOM) WITH SCROLLBARS
        # -----------------------
        table_frame = tk.Frame(self)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        cols = ("id","invoice","date","party","phone","address","gst_no",
                "place_of_supply","auth_sign","notes")

        # grid works best for scrollbars
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings")

        for c in cols:
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=180, anchor="center")

        # Scrollbars
        rec_vs = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        rec_hs = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)

        self.tree.configure(yscroll=rec_vs.set, xscroll=rec_hs.set)

        # Proper grid placement
        self.tree.grid(row=0, column=0, sticky="nsew")
        rec_vs.grid(row=0, column=1, sticky="ns")
        rec_hs.grid(row=1, column=0, sticky="ew")

        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())

    # ---------------------------------------------------------------------
    # PRODUCT LINE FUNCTIONS
    # ---------------------------------------------------------------------
    def clear_product_lines(self):
        self.product_list.clear()
        self.pro_tree.delete(*self.pro_tree.get_children())
        self.selected_product_index = None

    def add_product_row(self):
        try:
            qty = float(self.prod_inputs["qty"].get())
            rate = float(self.prod_inputs["rate"].get())
            disc = float(self.prod_inputs["discount_pct"].get() or 0)
            tax = float(self.prod_inputs["tax_pct"].get() or 0)
        except:
            messagebox.showerror("Error", "Qty / Rate / Discount / Tax must be numbers.", parent=self)
            return

        prod = self.prod_inputs["product"].get().strip()
        if not prod:
            messagebox.showwarning("Missing", "Enter product name.", parent=self)
            return

        subtotal, disc_amt, tax_amt, total = calc_totals(qty, rate, disc, tax)

        line = {
            "product": prod,
            "unit": self.prod_inputs["unit"].get().strip(),
            "qty": qty,
            "rate": rate,
            "discount_pct": disc,
            "tax_pct": tax,
            "subtotal": subtotal,
            "discount_amt": disc_amt,
            "tax_amt": tax_amt,
            "total": total
        }

        self.product_list.append(line)

        self.pro_tree.insert("", tk.END, values=(
            line["product"], line["unit"], line["qty"], line["rate"],
            line["discount_pct"], line["tax_pct"], line["subtotal"], line["total"]
        ))

    def on_product_row_select(self, event=None):
        sel = self.pro_tree.selection()
        if not sel:
            self.selected_product_index = None
            return

        idx = list(self.pro_tree.get_children()).index(sel[0])
        self.selected_product_index = idx
        vals = self.pro_tree.item(sel[0])["values"]

        keys = ["product", "unit", "qty", "rate", "discount_pct", "tax_pct"]
        for key, value in zip(keys, vals):
            self.prod_inputs[key].delete(0, tk.END)
            self.prod_inputs[key].insert(0, value)

    def update_product_row(self):
        if self.selected_product_index is None:
            messagebox.showwarning("Select", "Select a product row to update.", parent=self)
            return

        try:
            qty = float(self.prod_inputs["qty"].get())
            rate = float(self.prod_inputs["rate"].get())
            disc = float(self.prod_inputs["discount_pct"].get())
            tax = float(self.prod_inputs["tax_pct"].get())
        except:
            messagebox.showerror("Error", "Qty / Rate / Discount / Tax must be numbers.", parent=self)
            return

        prod = self.prod_inputs["product"].get().strip()
        subtotal, disc_amt, tax_amt, total = calc_totals(qty, rate, disc, tax)

        updated = {
            "product": prod,
            "unit": self.prod_inputs["unit"].get().strip(),
            "qty": qty,
            "rate": rate,
            "discount_pct": disc,
            "tax_pct": tax,
            "subtotal": subtotal,
            "discount_amt": disc_amt,
            "tax_amt": tax_amt,
            "total": total
        }

        self.product_list[self.selected_product_index] = updated
        

        rid = list(self.pro_tree.get_children())[self.selected_product_index]
        self.pro_tree.item(rid, values=(
            prod, updated["unit"], qty, rate, disc, tax, subtotal, total
        ))

    def remove_product_row(self):
        if self.selected_product_index is None:
            messagebox.showwarning("Select", "Select a product row.", parent=self)
            return

        self.product_list.pop(self.selected_product_index)
        rid = list(self.pro_tree.get_children())[self.selected_product_index]
        self.pro_tree.delete(rid)
        self.selected_product_index = None

    def clear_inputs(self):
        for e in self.inputs.values():
            e.delete(0, tk.END)
        for e in self.prod_inputs.values():
            e.delete(0, tk.END)

        self.prod_inputs["unit"].insert(0, "pcs")
        self.prod_inputs["discount_pct"].insert(0, "0")
        self.prod_inputs["tax_pct"].insert(0, "0")

        self.clear_product_lines()

    # ---------------------------------------------------------------------
    # SAVE PURCHASE
    # ---------------------------------------------------------------------
    def add_purchase(self):
        if not self.product_list:
            messagebox.showwarning("Empty", "Add at least one product.", parent=self)
            return

        party = self.inputs["party"].get()
        if not party:
            messagebox.showwarning("Missing", "Enter Supplier/Party.", parent=self)
            return

        subtotal = sum(p["subtotal"] for p in self.product_list)
        disc = sum(p["discount_amt"] for p in self.product_list)
        tax = sum(p["tax_amt"] for p in self.product_list)
        total = sum(p["total"] for p in self.product_list)

        rec = {
            "id": next_id(PURCHASE_FILE),
            "invoice": next_invoice("P", PURCHASE_FILE),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "party": party,
            "phone": self.inputs["phone"].get(),
            "address": self.inputs["address"].get(),
            "gst_no": self.inputs["gst_no"].get(),
            "place_of_supply": self.inputs["place_of_supply"].get(),
            "auth_sign": self.inputs["auth_sign"].get(),
            "products": self.product_list.copy(),
            "subtotal": subtotal,
            "discount_amt": disc,
            "tax_amt": tax,
            "total": total,
            "notes": self.inputs["notes"].get().strip()
        }

        # ⭐ ASK USER BEFORE SAVE — THIS WILL NOT CLOSE THE WINDOW
        if not messagebox.askokcancel(
            "Confirm Save",
            f"Do you want to save this purchase?\nInvoice: {rec['invoice']}",
            parent=self
        ):
            return  # user pressed Cancel


        # ⭐ SAVE PURCHASE
        db = load_json(PURCHASE_FILE)
        db.append(rec)
        save_json(PURCHASE_FILE, db)

        # Update stock safely
        try:
            save_json(STOCK_FILE, compute_stock_from_files())
        except:
            pass

        # Update ledger safely
        recompute_ledger()
        # ---------------- FIREBASE SYNC ----------------
        save_to_firebase("purchases", load_json(PURCHASE_FILE))
        save_to_firebase("stock", load_json(STOCK_FILE))
        save_to_firebase("ledger", load_json(LEDGER_FILE))
        # -----------------------------------------------


        # ⭐ Show Saved Message
        messagebox.showinfo(
            "Saved",
            f"Purchase saved successfully!\nInvoice: {rec['invoice']}",
            parent=self
        )

        # Refresh UI
        self.load_table()
        self.clear_inputs()
        self.clear_product_lines()


    # ---------------------------------------------------------------------
    # LOAD TABLE
    # ---------------------------------------------------------------------
    def load_table(self):
        self.tree.delete(*self.tree.get_children())

        term = self.search_var.get().lower()
        db = load_json(PURCHASE_FILE)

        for r in db:
            if term:
                if term not in r.get("invoice","").lower() and term not in r.get("party","").lower():
                    continue

            self.tree.insert("", tk.END, values=(
                r.get("id"), r.get("invoice"), r.get("date"), r.get("party"),
                r.get("phone"), r.get("address"), r.get("gst_no"),
                r.get("place_of_supply"), r.get("auth_sign"),
                r.get("notes","")
            ))

        color_rows(self.tree)

    # ---------------------------------------------------------------------
    # LOAD SELECTED RECORD
    # ---------------------------------------------------------------------
    def on_select(self):
        sel = self.tree.selection()
        if not sel:
            return

        tid = int(self.tree.item(sel[0])["values"][0])
        rec = next((r for r in load_json(PURCHASE_FILE) if r["id"] == tid), None)
        if not rec:
            return

        # fill supplier inputs
        for k in self.inputs:
            self.inputs[k].delete(0, tk.END)
            self.inputs[k].insert(0, rec.get(k, ""))

        # load product lines
        self.clear_product_lines()

        for p in rec["products"]:
            self.product_list.append(p.copy())
            self.pro_tree.insert("", tk.END, values=(
                p["product"], p["unit"], p["qty"], p["rate"],
                p["discount_pct"], p["tax_pct"], p["subtotal"], p["total"]
            ))

        self.selected_product_index = None

    # ---------------------------------------------------------------------
    # UPDATE & DELETE
    # ---------------------------------------------------------------------
    def update_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a purchase to update.", parent=self)
            return

        tid = int(self.tree.item(sel[0])["values"][0])
        db = load_json(PURCHASE_FILE)
        rec = next((r for r in db if r["id"] == tid), None)

        if not self.product_list:
            messagebox.showwarning("No Items", "Add at least one product.", parent=self)
            return

        # Ask confirmation
        if not messagebox.askokcancel(
            "Confirm Update",
            "Do you want to update this purchase?",
            parent=self
        ):
            return

        subtotal = sum(p["subtotal"] for p in self.product_list)
        disc = sum(p["discount_amt"] for p in self.product_list)
        tax = sum(p["tax_amt"] for p in self.product_list)
        total = sum(p["total"] for p in self.product_list)

        rec.update({
            "party": self.inputs["party"].get(),
            "phone": self.inputs["phone"].get(),
            "address": self.inputs["address"].get(),
            "gst_no": self.inputs["gst_no"].get(),
            "place_of_supply": self.inputs["place_of_supply"].get(),
            "auth_sign": self.inputs["auth_sign"].get(),
            "products": [p.copy() for p in self.product_list],
            "subtotal": subtotal,
            "discount_amt": disc,
            "tax_amt": tax,
            "total": total,
            "notes": self.inputs["notes"].get().strip()
        })

        save_json(PURCHASE_FILE, db)
        # Firebase sync
        save_to_firebase("purchases", load_json(PURCHASE_FILE))
        save_to_firebase("sales", load_json(SALE_FILE))
        save_to_firebase("stock", load_json(STOCK_FILE))
        save_to_firebase("ledger", load_json(LEDGER_FILE))


        try:
            save_json(STOCK_FILE, compute_stock_from_files())
        except:
            pass

        recompute_ledger()

        messagebox.showinfo("Updated", "Purchase updated successfully!", parent=self)
        self.load_table()


    def delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a purchase to delete.", parent=self)
            return

        if not messagebox.askokcancel(
            "Confirm Delete",
            "Are you sure you want to delete this purchase?",
            parent=self
        ):
            return

        tid = int(self.tree.item(sel[0])["values"][0])
        db = [r for r in load_json(PURCHASE_FILE) if r["id"] != tid]
        save_json(PURCHASE_FILE, db)
        save_to_firebase("purchases", load_json(PURCHASE_FILE))
        save_to_firebase("sales", load_json(SALE_FILE))
        save_to_firebase("stock", load_json(STOCK_FILE))
        save_to_firebase("ledger", load_json(LEDGER_FILE))


        try:
            save_json(STOCK_FILE, compute_stock_from_files())
        except:
            pass

        recompute_ledger()

        messagebox.showinfo("Deleted", "Purchase deleted successfully!", parent=self)
        self.load_table()


    # ---------------------------------------------------------------------
    # RECEIPT / BILL
    # ---------------------------------------------------------------------
    def save_receipt_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a purchase.", parent=self)
            return
        invoice = self.tree.item(sel[0])["values"][1]
        rec = next((r for r in load_json(PURCHASE_FILE) if r["invoice"] == invoice), None)
        save_receipt_text(self, rec, kind="Purchase")

    def show_bill_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a purchase.", parent=self)
            return
        invoice = self.tree.item(sel[0])["values"][1]
        rec = next((r for r in load_json(PURCHASE_FILE) if r["invoice"] == invoice), None)
        generate_bill_text(self, rec, kind="Purchase")

# -------------------------
# SaleWindow  
# -------------------------
class SaleWindow(tk.Toplevel):
    """Sale entry window (multi-product)."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Sale Entry")
        self.geometry("1350x610+2+82")
        self.config(bg="#E8EAF6")

        self.product_list = []
        self.stock_map = {}
        self.selected_product_index = None
        self._build_ui()
        self.load_products_from_stock()
        self.load_table()

    def _build_ui(self):
        top = tk.Frame(self, padx=12, pady=12, bg="#E8EAF6")
        top.pack(fill=tk.X)

        tk.Label(top, text="Product (from stock)", font=("Arial", 10, "bold"), bg="#E8EAF6")\
            .grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.product_cb = ttk.Combobox(top, values=[], width=30, state="normal")
        self.product_cb.grid(row=0, column=1, padx=6, pady=4)
        self.product_cb.bind("<<ComboboxSelected>>", lambda e: self.on_product_selected())

        tk.Label(top, text="Available", font=("Arial", 10, "bold"), bg="#E8EAF6")\
            .grid(row=0, column=2, sticky="w", padx=6, pady=4)
        self.available_lbl = tk.Label(top, text="0", width=28, bg="#f0f0f0", relief="groove")
        self.available_lbl.grid(row=0, column=3, padx=6, pady=4)

        tk.Label(top, text="Ref Invoice (latest)", font=("Arial", 10, "bold"), bg="#E8EAF6")\
            .grid(row=0, column=4, sticky="w", padx=6, pady=4)
        self.ref_lbl = tk.Label(top, text="", width=28, bg="#f0f0f0", relief="sunken")
        self.ref_lbl.grid(row=0, column=5, padx=6, pady=4)

        labels = [
            ("Customer", "party"),
            ("Phone", "phone"),
            ("Address", "address"),
            ("GST No", "gst_no"),
            ("Place of Supply", "place_of_supply"),
            ("Authorized Sign", "auth_sign"),
            ("Unit", "unit"),
            ("Qty", "qty"),
            ("Rate", "rate"),
            ("Discount %", "discount_pct"),
            ("Tax %", "tax_pct"),
            ("Notes", "notes")
        ]
        self.inputs = {}
        row = 1; col = 0
        for label_text, key in labels:
            tk.Label(top, text=label_text, font=("Arial", 10, "bold"), bg="#E8EAF6")\
                .grid(row=row, column=col, sticky="w", padx=6, pady=4)
            e = tk.Entry(top, width=25, font=("", 11), bg="lightyellow")
            e.grid(row=row, column=col + 1, padx=10, pady=4)
            self.inputs[key] = e
            col += 2
            if col > 4:
                col = 0; row += 1

        self.inputs["unit"].insert(0, "pcs")
        self.inputs["discount_pct"].insert(0, "0")
        self.inputs["tax_pct"].insert(0, "0")

        prod_frame = tk.LabelFrame(self, text="Products in current bill", padx=6, pady=6)
        prod_frame.pack(fill=tk.X, padx=8, pady=6)

        pcols = ("product", "unit", "qty", "rate", "disc_pct", "tax_pct", "ref_invoice", "subtotal", "total")
        self.pro_tree = ttk.Treeview(prod_frame, columns=pcols, show="headings", height=3)
        for c in pcols:
            self.pro_tree.heading(c, text=c.replace("_", " ").title()); self.pro_tree.column(c, width=110, anchor="center")
        self.pro_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        p_vs = ttk.Scrollbar(prod_frame, orient="vertical", command=self.pro_tree.yview)
        self.pro_tree.configure(yscroll=p_vs.set); p_vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.pro_tree.bind("<<TreeviewSelect>>", self.on_product_row_select)

        pl_btns = tk.Frame(self); pl_btns.pack(fill=tk.X, padx=8)
        ttk.Button(pl_btns, text="Add Product",style="Delete.TButton", command=self.add_product_row).pack(side=tk.LEFT, padx=6)
        ttk.Button(pl_btns, text="Update Product",style="Add.TButton", command=self.update_product_row).pack(side=tk.LEFT, padx=6)
        ttk.Button(pl_btns, text="Remove Selected Product",style="Save.TButton", command=self.remove_product_row).pack(side=tk.LEFT, padx=6)
        ttk.Button(pl_btns, text="Clear Product Lines",style="Update.TButton", command=self.clear_product_lines).pack(side=tk.LEFT, padx=6)

        btns = tk.Frame(self, pady=10, bg="#E8EAF6"); btns.pack(fill=tk.X)
        style = ttk.Style(); style.theme_use("clam")
        style.configure("Add.TButton", background="#28a745", foreground="white", font=("Arial", 11, "bold"), padding=6)
        style.map("Add.TButton", background=[("active", "#218838")])
        style.configure("Update.TButton", background="#ff8c00", foreground="white", font=("Arial", 11, "bold"), padding=6)
        style.map("Update.TButton", background=[("active", "#e67300")])
        style.configure("Delete.TButton", background="#17a2b8", foreground="white", font=("Arial", 11, "bold"), padding=6)
        style.map("Delete.TButton", background=[("active", "#138496")])
        style.configure("Save.TButton", background="#6f42c1", foreground="white", font=("Arial", 11, "bold"), padding=6)
        style.map("Save.TButton", background=[("active", "#5936a0")])

        ttk.Button(btns, text="Add Sale", style="Add.TButton", command=self.add_sale).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Update Selected", style="Update.TButton", command=self.update_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Delete Selected", style="Delete.TButton", command=self.delete_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Save Receipt", style="Save.TButton", command=self.save_receipt_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Show Bill", style="Save.TButton", command=self.show_bill_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Clear", style="Delete.TButton", command=self.clear_inputs).pack(side=tk.LEFT, padx=6)

        search_frame = tk.Frame(self, bg="#E8EAF6"); search_frame.pack(fill=tk.X, padx=8)
        tk.Label(search_frame, text="Search:", font=("Arial", 10, "bold"), bg="#E8EAF6").pack(side=tk.LEFT)
        self.search_var = tk.Entry(search_frame, bg="lightyellow", width=40); self.search_var.pack(side=tk.LEFT, padx=6)
        self.search_var.bind("<KeyRelease>", lambda e: self.load_table())

        table_frame = tk.Frame(self); table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        cols = ("id", "invoice", "date", "party", "phone", "address", "gst_no", "place_of_supply", "auth_sign", "ref_invoice", "notes")
        headers = ["ID", "Invoice", "Date", "Party", "Phone", "Address", "GST No", "Place", "Auth Sign", "Ref", "Notes"]
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=6)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h); 
            self.tree.column(c, width=150, anchor="center")
        vs = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vs.set, xscroll=hs.set); 
        vs.pack(side=tk.RIGHT, fill=tk.Y); 
        hs.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())

    def load_products_from_stock(self):
        stock = load_json(STOCK_FILE)
        if not isinstance(stock, list):
            stock = []
        products = [s.get("product") for s in stock if s.get("available", 0) > 0]
        self.stock_map = {s.get("product"): s for s in stock}
        self.product_cb['values'] = products
        if products:
            try:
                self.product_cb.current(0); self.on_product_selected()
            except:
                pass
        else:
            self.available_lbl.config(text="0"); self.ref_lbl.config(text="")

    def on_product_selected(self):
        prod = self.product_cb.get().strip()
        if not prod:
            self.available_lbl.config(text="0"); self.ref_lbl.config(text=""); return
        s = self.stock_map.get(prod, {})
        self.available_lbl.config(text=str(s.get("available", 0)))
        self.ref_lbl.config(text=str(s.get("latest_invoice", "")))
        self.inputs["unit"].delete(0, tk.END); self.inputs["unit"].insert(0, s.get("unit", "pcs"))
        self.inputs["rate"].delete(0, tk.END); self.inputs["rate"].insert(0, str(s.get("avg_price", 0.0)))

    def clear_product_lines(self):
        self.product_list.clear(); self.pro_tree.delete(*self.pro_tree.get_children()); self.selected_product_index = None

    def add_product_row(self):
        prod = self.product_cb.get().strip()
        if not prod:
            messagebox.showwarning("Select product", "Choose a product from stock.", parent=self); return
        try:
            qty = float(self.inputs["qty"].get())
            rate = float(self.inputs["rate"].get())
            disc_pct = float(self.inputs["discount_pct"].get() or 0)
            tax_pct = float(self.inputs["tax_pct"].get() or 0)
        except:
            messagebox.showerror("Invalid", "Qty/Rate/Discount/Tax must be numbers.", parent=self); return
        s = self.stock_map.get(prod)
        if s and qty > s.get("available", 0):
            messagebox.showwarning("Stock", f"Available {s.get('available', 0)} — cannot add qty {qty}.", parent=self); return
        subtotal, disc_amt, tax_amt, total = calc_totals(qty, rate, disc_pct, tax_pct)
        line = {
            "product": prod,
            "unit": self.inputs["unit"].get().strip() or "pcs",
            "qty": qty,
            "rate": rate,
            "discount_pct": disc_pct,
            "tax_pct": tax_pct,
            "subtotal": subtotal,
            "discount_amt": disc_amt,
            "tax_amt": tax_amt,
            "total": total,
            "ref_invoice": self.stock_map.get(prod, {}).get("latest_invoice", "")
        }
        self.product_list.append(line)
        ref_inv = self.stock_map.get(prod, {}).get("latest_invoice", "")
        self.pro_tree.insert("", tk.END, values=(
            line["product"], line["unit"], line["qty"], line["rate"],
            line["discount_pct"], line["tax_pct"], ref_inv,
            line["subtotal"], line["total"]
        ))

    def on_product_row_select(self, event=None):
        sel = self.pro_tree.selection()
        if not sel: self.selected_product_index = None; return
        children = list(self.pro_tree.get_children())
        try:
            idx = children.index(sel[0])
        except ValueError:
            self.selected_product_index = None; return
        vals = self.pro_tree.item(sel[0])["values"]
        self.product_cb.set(vals[0])
        self.inputs["unit"].delete(0, tk.END); self.inputs["unit"].insert(0, vals[1])
        self.inputs["qty"].delete(0, tk.END); self.inputs["qty"].insert(0, vals[2])
        self.inputs["rate"].delete(0, tk.END); self.inputs["rate"].insert(0, vals[3])
        self.inputs["discount_pct"].delete(0, tk.END); self.inputs["discount_pct"].insert(0, vals[4])
        self.inputs["tax_pct"].delete(0, tk.END); self.inputs["tax_pct"].insert(0, vals[5])
        self.ref_lbl.config(text=str(vals[6])); self.selected_product_index = idx

    def update_product_row(self):
        if self.selected_product_index is None:
            messagebox.showwarning("Select", "Select a product row to update.", parent=self); return
        try:
            qty = float(self.inputs["qty"].get()); rate = float(self.inputs["rate"].get())
            disc_pct = float(self.inputs["discount_pct"].get() or 0); tax_pct = float(self.inputs["tax_pct"].get() or 0)
        except:
            messagebox.showerror("Invalid", "Qty/Rate/Discount/Tax must be numbers.", parent=self); return
        product = self.product_cb.get().strip()
        if not product:
            messagebox.showwarning("Product", "Select a product before updating.", parent=self); return
        s = self.stock_map.get(product)
        if s and qty > s.get("available", 0):
            messagebox.showwarning("Stock", f"Available {s.get('available', 0)} — cannot set qty {qty}.", parent=self); return
        subtotal, discount_amt, tax_amt, total = calc_totals(qty, rate, disc_pct, tax_pct)
        updated_line = {
            "product": product, "unit": self.inputs["unit"].get().strip() or "pcs",
            "qty": qty, "rate": rate, "discount_pct": disc_pct, "tax_pct": tax_pct,
            "subtotal": subtotal, "discount_amt": discount_amt, "tax_amt": tax_amt, "total": total
        }
        try:
            self.product_list[self.selected_product_index] = updated_line
            children = list(self.pro_tree.get_children()); row_id = children[self.selected_product_index]
            self.pro_tree.item(row_id, values=(updated_line["product"], updated_line["unit"], updated_line["qty"], updated_line["rate"],
                                              updated_line["discount_pct"], updated_line["tax_pct"], updated_line["subtotal"], updated_line["total"]))
            messagebox.showinfo("Updated", "Product line updated.", parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to update product line:\n{e}", parent=self); return
        try:
            self.pro_tree.selection_remove(self.pro_tree.selection())
        except:
            pass
        self.selected_product_index = None

    def remove_product_row(self):
        sel = self.pro_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a product line to remove.", parent=self); return
        idx = list(self.pro_tree.get_children()).index(sel[0])
        try:
            del self.product_list[idx]
        except:
            pass
        self.pro_tree.delete(sel[0])
        self.selected_product_index = None

    def clear_inputs(self):
        self.product_cb.set(""); self.available_lbl.config(text="0"); self.ref_lbl.config(text="")
        for e in self.inputs.values(): e.delete(0, tk.END)
        self.inputs["unit"].insert(0, "pcs"); self.inputs["discount_pct"].insert(0, "0"); self.inputs["tax_pct"].insert(0, "0")

    # -------------------- Sale CRUD --------------------
    # -----------------------------------
    # ASK CONFIRMATION BEFORE SAVING
    # -----------------------------------
    def add_sale(self):
        if not messagebox.askokcancel(
            "Confirm Save",
            "Do you want to save this sale?",
            parent=self
        ):
            return   # <-- CANCEL = DO NOT SAVE SALE

        # -----------------------------------
        # VALIDATION
        # -----------------------------------
        if not self.product_list:
            messagebox.showwarning("Empty", "Add at least one product.", parent=self)
            return

        if not self.inputs["party"].get():
            messagebox.showwarning("Missing", "Enter Customer/Party.", parent=self)
            return

        # -----------------------------------
        # CALCULATE TOTALS
        # -----------------------------------
        subtotal = sum(p["subtotal"] for p in self.product_list)
        discount = sum(p.get("discount_amt", 0) for p in self.product_list)
        tax = sum(p.get("tax_amt", 0) for p in self.product_list)
        total = sum(p.get("total", 0) for p in self.product_list)

        # -----------------------------------
        # CREATE SALE OBJECT
        # -----------------------------------
        rec = {
            "id": next_id(SALE_FILE),
            "invoice": next_invoice("S", SALE_FILE),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "party": self.inputs["party"].get(),
            "phone": self.inputs["phone"].get(),
            "address": self.inputs["address"].get(),
            "gst_no": self.inputs["gst_no"].get(),
            "place_of_supply": self.inputs["place_of_supply"].get(),
            "auth_sign": self.inputs["auth_sign"].get(),
            "products": self.product_list.copy(),
            "subtotal": subtotal,
            "discount_amt": discount,
            "tax_amt": tax,
            "total": total,
            "notes": self.inputs.get("notes", tk.Entry()).get() if "notes" in self.inputs else ""
        }

        # -----------------------------------
        # SAVE TO JSON (ONLY IF OK WAS PRESSED)
        # -----------------------------------
        db = load_json(SALE_FILE)
        db.append(rec)
        save_json(SALE_FILE, db)

        # UPDATE STOCK & LEDGER
        save_json(STOCK_FILE, compute_stock_from_files())
        recompute_ledger()
        # ---------------- FIREBASE SYNC ----------------
        save_to_firebase("sales", load_json(SALE_FILE))
        save_to_firebase("stock", load_json(STOCK_FILE))
        save_to_firebase("ledger", load_json(LEDGER_FILE))
        # -----------------------------------------------


        # -----------------------------------
        # SHOW SUCCESS POPUP
        # -----------------------------------
        messagebox.showinfo(
            "Saved",
            f"Sale saved successfully!\nInvoice: {rec['invoice']}",
            parent=self
        )

        # -----------------------------------
        # RESET UI
        # -----------------------------------
        self.load_table()
        self.clear_product_lines()
        self.clear_inputs()

        try:
            self.master.refresh_dashboard()
        except:
            pass


        # -----------------------------------
        # load_table
        # -----------------------------------
    def load_table(self):
        self.tree.delete(*self.tree.get_children())
        db = load_json(SALE_FILE)
        term = self.search_var.get().strip().lower()
        for r in db:
            if term:
                if term not in r.get("invoice","").lower() and term not in r.get("party","").lower():
                    continue
            self.tree.insert("", tk.END, values=(r.get("id"), r.get("invoice"), r.get("date"), r.get("party"),
                                                 r.get("phone"), r.get("address"), r.get("gst_no"), r.get("place_of_supply"),
                                                 r.get("auth_sign"), r.get("invoice", ""), r.get("notes","")))
        color_rows(self.tree)

    def on_select(self):
        sel = self.tree.selection()
        if not sel:
            return
        # Get selected sale ID
        tid = int(self.tree.item(sel[0])["values"][0])
        # Load record from JSON
        db = load_json(SALE_FILE)
        rec = next((r for r in db if r.get("id") == tid), None)
        if not rec:
            return

        # -----------------------------------------
        # FILL CUSTOMER INPUTS
        # -----------------------------------------
        mapping = {
            "party": rec.get("party", ""),
            "phone": rec.get("phone", ""),
            "address": rec.get("address", ""),
            "gst_no": rec.get("gst_no", ""),
            "place_of_supply": rec.get("place_of_supply", ""),
            "auth_sign": rec.get("auth_sign", ""),
            "notes": rec.get("notes", "")
        }

        for k, v in mapping.items():
            if k in self.inputs:
                self.inputs[k].delete(0, tk.END)
                self.inputs[k].insert(0, str(v))
        # -----------------------------------------
        # FILL REF INVOICE
        # -----------------------------------------
        self.ref_lbl.config(text=str(rec.get("ref_invoice", "")))
        # -----------------------------------------
        # LOAD PRODUCTS INTO TREE
        # -----------------------------------------
        self.clear_product_lines()

        for p in rec.get("products", []):
            line = {
                "product": p.get("product", ""),
                "unit": p.get("unit", "pcs"),
                "qty": p.get("qty", 0),
                "rate": p.get("rate", 0),
                "discount_pct": p.get("discount_pct", 0),
                "tax_pct": p.get("tax_pct", 0),
                "subtotal": p.get("subtotal", 0),
                "discount_amt": p.get("discount_amt", 0),
                "tax_amt": p.get("tax_amt", 0),
                "total": p.get("total", 0),
                "ref_invoice": p.get("ref_invoice", "")
            }

            self.product_list.append(line)

            self.pro_tree.insert("", tk.END, values=(
                line["product"], line["unit"], line["qty"], line["rate"],
                line["discount_pct"], line["tax_pct"], line["ref_invoice"],
                line["subtotal"], line["total"]
            ))

        # Reset selection for product entry fields
        self.selected_product_index = None

        # Clear entry fields for product area
        for k in ["unit", "qty", "rate", "discount_pct", "tax_pct"]:
            self.inputs[k].delete(0, tk.END)

        self.inputs["unit"].insert(0, "pcs")
        self.inputs["discount_pct"].insert(0, "0")
        self.inputs["tax_pct"].insert(0, "0")

    def update_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a sale to update.", parent=self)
            return

        tid = int(self.tree.item(sel[0])["values"][0])

        db = load_json(SALE_FILE)
        rec = next((r for r in db if r.get("id") == tid), None)

        if not rec:
            messagebox.showerror("Error", "Sale record not found.", parent=self)
            return

        if not self.product_list:
            messagebox.showwarning("Empty", "Add at least one product.", parent=self)
            return

        if not messagebox.askokcancel("Confirm", "Update this sale?", parent=self):
            return

        # Recalculate totals
        subtotal = sum(p["subtotal"] for p in self.product_list)
        discount_amt = sum(p["discount_amt"] for p in self.product_list)
        tax_amt = sum(p["tax_amt"] for p in self.product_list)
        total = sum(p["total"] for p in self.product_list)

        # Update fields
        rec.update({
            "party": self.inputs["party"].get(),
            "phone": self.inputs["phone"].get(),
            "address": self.inputs["address"].get(),
            "gst_no": self.inputs["gst_no"].get(),
            "place_of_supply": self.inputs["place_of_supply"].get(),
            "auth_sign": self.inputs["auth_sign"].get(),
            "products": [p.copy() for p in self.product_list],
            "subtotal": subtotal,
            "discount_amt": discount_amt,
            "tax_amt": tax_amt,
            "total": total,
            "notes": self.inputs.get("notes", tk.Entry()).get()
        })

        save_json(SALE_FILE, db)
        # Firebase sync
        save_to_firebase("purchases", load_json(PURCHASE_FILE))
        save_to_firebase("sales", load_json(SALE_FILE))
        save_to_firebase("stock", load_json(STOCK_FILE))
        save_to_firebase("ledger", load_json(LEDGER_FILE))


        # Recompute stock & ledger
        try:
            save_json(STOCK_FILE, compute_stock_from_files())
        except:
            pass

        recompute_ledger()

        messagebox.showinfo("Updated", "Sale updated successfully!", parent=self)
        self.load_table()

        # Reset UI
        self.clear_inputs()
        self.clear_product_lines()

    def delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a record to delete.", parent=self); return
        if not messagebox.askyesno("Confirm", "Delete selected sale?", parent=self): return
        tid = int(self.tree.item(sel[0])["values"][0])
        db = load_json(SALE_FILE); 
        db = [r for r in db if r.get("id") != tid]; 
        save_json(SALE_FILE, db)
        
        save_to_firebase("purchases", load_json(PURCHASE_FILE))
        save_to_firebase("sales", load_json(SALE_FILE))
        save_to_firebase("stock", load_json(STOCK_FILE))
        save_to_firebase("ledger", load_json(LEDGER_FILE))

        save_json(STOCK_FILE, compute_stock_from_files()); 
        recompute_ledger()
        messagebox.showinfo("Deleted", "Sale deleted.", parent=self); 
        self.load_table()

    def save_receipt_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a sale.", parent=self); return
        invoice = self.tree.item(sel[0])["values"][1]
        db = load_json(SALE_FILE); rec = next((r for r in db if r.get("invoice") == invoice), None)
        if not rec:
            messagebox.showerror("Error", "Record not found.", parent=self); return
        save_receipt_text(self, rec, kind="Sale")

    def show_bill_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a sale.", parent=self); return
        invoice = self.tree.item(sel[0])["values"][1]
        db = load_json(SALE_FILE); rec = next((r for r in db if r.get("invoice") == invoice), None)
        if not rec:
            messagebox.showerror("Error", "Record not found.", parent=self); return
        generate_bill_text(self, rec, kind="Sale")

# -------------------------
# StockWindow  
# -------------------------
class StockWindow(tk.Toplevel):
    """Show current stock and allow Refresh/Export. Updates Sold & Available dynamically."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Stock (Live Updates)")
        self.geometry("1320x610+15+82")
        self.config(bg="#E8EAF6")
        self.parent = parent
        self._build_ui()
        self.load_stock()

    # -----------------------------------------------------------
    # UI BUILD
    # -----------------------------------------------------------
    def _build_ui(self):

        # ===== Toolbar =====
        toolbar = tk.Frame(self, pady=6, bg="#E8EAF6")
        toolbar.pack(fill=tk.X)

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "Refresh.TButton",
            background="#004D40",
            foreground="white",
            font=("Arial", 11, "bold"),
            padding=6
        )
        style.map("Refresh.TButton", background=[("active", "#00897B")])

        style.configure(
            "Export.TButton",
            background="#4E342E",
            foreground="white",
            font=("Arial", 11, "bold"),
            padding=6
        )
        style.map("Export.TButton", background=[("active", "#795548")])

        ttk.Button(
            toolbar,
            text="Refresh Stock (compute & save)",
            style="Refresh.TButton",
            command=self.refresh_and_save_stock
        ).pack(side=tk.LEFT, padx=6)

        ttk.Button(
            toolbar,
            text="Export CSV",
            style="Export.TButton",
            command=self.export_csv
        ).pack(side=tk.LEFT, padx=6)

        # ===== Table (Treeview) =====
        frame = tk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        self.cols = (
            "product", "purchased", "sold", "available",
            "avg_price", "value", "unit", "latest_invoice"
        )

        headers = [
            "Product", "Purchased", "Sold", "Available",
            "Avg Price", "Value", "Unit", "Latest Invoice"
        ]

        self.tree = ttk.Treeview(frame, columns=self.cols, show="headings", height=20)

        for c, h in zip(self.cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=150, anchor="center")

        vs = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vs.set, xscroll=hs.set)

        vs.pack(side=tk.RIGHT, fill=tk.Y)
        hs.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)

    # -----------------------------------------------------------
    # LOAD STOCK DATA
    # -----------------------------------------------------------
    def load_stock(self):
        self.tree.delete(*self.tree.get_children())

        # Load stock.json
        try:
            stock = load_json(STOCK_FILE)
        except:
            stock = []

        # Safety: ensure list type
        if not isinstance(stock, list):
            stock = []

        for r in stock:
            vals = (
                r.get("product"),
                r.get("purchased"),
                r.get("sold"),
                r.get("available"),
                round(float(r.get("avg_price", 0)), 2),
                round(float(r.get("value", 0)), 2),
                r.get("unit"),
                r.get("latest_invoice")
            )
            self.tree.insert("", tk.END, values=vals)

        color_rows(self.tree)

    # -----------------------------------------------------------
    # RECOMPUTE STOCK FROM PURCHASE + SALE FILES
    # -----------------------------------------------------------
    def refresh_and_save_stock(self):
        try:
            stock = compute_stock_from_files()  # recompute using your working function
            save_json(STOCK_FILE, stock)
            self.load_stock()
            messagebox.showinfo("Updated", "Stock recomputed successfully!", parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to compute stock:\n{e}", parent=self)

    # -----------------------------------------------------------
    # EXPORT TABLE AS CSV
    # -----------------------------------------------------------
    def export_csv(self):
        file = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            title="Save Stock CSV"
        )
        if not file:
            return

        try:
            import csv
            stock = load_json(STOCK_FILE)

            with open(file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)

                writer.writerow([
                    "Product", "Purchased", "Sold", "Available",
                    "Avg Price", "Value", "Unit", "Latest Invoice"
                ])

                for r in stock:
                    writer.writerow([
                        r.get("product"),
                        r.get("purchased"),
                        r.get("sold"),
                        r.get("available"),
                        r.get("avg_price"),
                        r.get("value"),
                        r.get("unit"),
                        r.get("latest_invoice"),
                    ])

            messagebox.showinfo("Exported", "CSV Export Successful!", parent=self)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to export CSV:\n{e}", parent=self)

# -------------------------
# LedgerWindow  
# -------------------------
class LedgerWindow(tk.Toplevel):
    """Show ledger grouped by party."""
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Ledger")
        self.geometry("1320x610+15+82")
        self._build_ui()
        self.load_parties()
        self.config(bg="#E8EAF6")

    # ---------------------------------------------------------------
    # BUILD UI
    # ---------------------------------------------------------------
    def _build_ui(self):
        # ========== Top Bar ==========
        top = tk.Frame(self, bg="#E8EAF6")
        top.pack(fill=tk.X, padx=8, pady=6)

        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Show.TButton", background="#2196F3", foreground="white",
                        font=("Arial", 10, "bold"), padding=5)
        style.map("Show.TButton", background=[("active", "#1976D2")])

        style.configure("Add.TButton", background="#4CAF50", foreground="white",
                        font=("Arial", 10, "bold"), padding=5)
        style.map("Add.TButton", background=[("active", "#388E3C")])

        style.configure("Delete.TButton", background="#F44336", foreground="white",
                        font=("Arial", 10, "bold"), padding=5)
        style.map("Delete.TButton", background=[("active", "#D32F2F")])

        style.configure("Refresh.TButton", background="#9C27B0", foreground="white",
                        font=("Arial", 10, "bold"), padding=5)
        style.map("Refresh.TButton", background=[("active", "#7B1FA2")])

        tk.Label(top, text="Ledger (Party Wise)", font=("Arial", 12, "bold"),
                 bg="#E8EAF6").pack(side=tk.LEFT)

        ttk.Button(top, text="Refresh", style="Refresh.TButton",
                   command=self.load_parties).pack(side=tk.RIGHT, padx=6)

        # ========== Middle Section ==========
        mid = tk.Frame(self, bg="#E8EAF6")
        mid.pack(fill=tk.X, padx=8, pady=6)

        tk.Label(mid, text="Party:", font=("Arial", 10, "bold"),
                 bg="#E8EAF6").pack(side=tk.LEFT, padx=6)

        self.party_cb = ttk.Combobox(mid, values=[], state="readonly", width=50)
        self.party_cb.pack(side=tk.LEFT, padx=6)

        ttk.Button(mid, text="Show", style="Show.TButton",
                   command=self.show_party).pack(side=tk.LEFT, padx=6)

        ttk.Button(mid, text="Add", style="Add.TButton",
                   command=self.open_add_popup).pack(side=tk.LEFT, padx=6)

        ttk.Button(mid, text="Delete", style="Delete.TButton",
                   command=self.delete_row).pack(side=tk.LEFT, padx=6)

        # ========== Ledger Table ==========
        columns = ("date", "type", "invoice", "credit", "debit", "remaining", "amount")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")

        headers = [
            ("date", "Date"),
            ("type", "Type"),
            ("invoice", "Invoice"),
            ("credit", "Credit"),
            ("debit", "Debit"),
            ("remaining", "Remaining"),
            ("amount", "Amount")
        ]

        for col, txt in headers:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=150, anchor="center")

        self.tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

    # ---------------------------------------------------------------
    # GET LAST TRANSACTION
    # ---------------------------------------------------------------
    def get_last_transaction(self, party):
        ledger = load_json(LEDGER_FILE)
        ent = ledger.get(party, {})
        trans = ent.get("transactions", [])
        if len(trans) > 0:
            return trans[-1]
        return None

    # ---------------------------------------------------------------
    # ADD POPUP
    # ---------------------------------------------------------------
    def open_add_popup(self):
        party = self.party_cb.get()
        if not party:
            messagebox.showwarning("Select", "Select a party first.", parent=self)
            return

        last = self.get_last_transaction(party) or {}

        popup = tk.Toplevel(self)
        popup.title("Add Ledger Entry")
        popup.geometry("420x420+100+60")
        popup.grab_set()

        labels = ["Date", "Type", "Invoice", "Credit", "Debit", "Remaining", "Amount"]
        self.entries = {}

        auto_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        auto_type = last.get("type", "")
        auto_invoice = last.get("invoice", "")

        prev_amount = float(last.get("remaining", last.get("amount", 0)) or 0)

        def update_remaining(event=None):
            credit = self.entries["credit"].get()
            debit = self.entries["debit"].get()

            try: credit_val = float(credit) if credit else 0
            except: credit_val = 0

            try: debit_val = float(debit) if debit else 0
            except: debit_val = 0

            if credit_val > 0:
                remaining = prev_amount - credit_val
            elif debit_val > 0:
                remaining = prev_amount - debit_val
            else:
                remaining = prev_amount

            self.entries["remaining"].config(state="normal")
            self.entries["remaining"].delete(0, tk.END)
            self.entries["remaining"].insert(0, str(remaining))
            self.entries["remaining"].config(state="readonly")

        for i, label in enumerate(labels):
            tk.Label(popup, text=label, font=("Arial", 10, "bold")).place(x=20, y=20 + (i * 40))
            ent = tk.Entry(popup, width=30, font=("Arial", 10))
            ent.place(x=160, y=20 + (i * 40))
            self.entries[label.lower()] = ent

            if label == "Date":
                ent.insert(0, auto_date)
                ent.config(state="readonly")
            elif label == "Type":
                ent.insert(0, auto_type)
                ent.config(state="readonly")
            elif label == "Invoice":
                ent.insert(0, auto_invoice)
                ent.config(state="readonly")
            elif label == "Amount":
                ent.insert(0, str(prev_amount))
                ent.config(state="readonly")
            elif label == "Remaining":
                ent.insert(0, str(prev_amount))
                ent.config(state="readonly")

        self.entries["credit"].bind("<KeyRelease>", update_remaining)
        self.entries["debit"].bind("<KeyRelease>", update_remaining)

        ttk.Button(popup, text="Save", style="Refresh.TButton",
                   command=lambda: self.save_new_entry(party, popup)
                   ).place(x=160, y=360)

    # ---------------------------------------------------------------
    # SAVE NEW ENTRY INTO JSON
    # ---------------------------------------------------------------
    def save_new_entry(self, party, popup):
        ledger = load_json(LEDGER_FILE)

        if party not in ledger:
            ledger[party] = {"transactions": []}

        new_txn = {
            "date": self.entries["date"].get(),
            "type": self.entries["type"].get(),
            "invoice": self.entries["invoice"].get(),
            "credit": self.entries["credit"].get(),
            "debit": self.entries["debit"].get(),
            "remaining": self.entries["remaining"].get(),
            "amount": self.entries["amount"].get()
        }

        ledger[party]["transactions"].append(new_txn)
        ledger[party]["last_amount"] = new_txn["remaining"]

        save_json(LEDGER_FILE, ledger)
        self.show_party()

        popup.destroy()
        messagebox.showinfo("Saved", "Entry added successfully!", parent=self)

    # ---------------------------------------------------------------
    # LOAD PARTIES
    # ---------------------------------------------------------------
    def load_parties(self):
        ledger = load_json(LEDGER_FILE)
        parties = sorted(list(ledger.keys()))
        self.party_cb["values"] = parties

    # ---------------------------------------------------------------
    # DELETE ROW
    # ---------------------------------------------------------------
    def delete_row(self):
        party = self.party_cb.get()

        if not party:
            messagebox.showwarning("Select", "Select a party first.",parent=self)
            return

        selected = self.tree.focus()
        if not selected:
            messagebox.showwarning("Select", "Please select a row to delete.",parent=self)
            return

        row_values = self.tree.item(selected)["values"]

        row_date = row_values[0]
        row_invoice = row_values[2]

        ledger = load_json(LEDGER_FILE)

        if party in ledger and "transactions" in ledger[party]:
            new_list = []
            for txn in ledger[party]["transactions"]:
                if not (txn.get("date") == row_date and txn.get("invoice") == row_invoice):
                    new_list.append(txn)

            ledger[party]["transactions"] = new_list
            save_json(LEDGER_FILE, ledger)

        self.tree.delete(selected)

        messagebox.showinfo("Deleted", "Selected row deleted successfully!",parent=self)

    # ---------------------------------------------------------------
    # SHOW PARTY TRANSACTIONS
    # ---------------------------------------------------------------
    def show_party(self):
        party = self.party_cb.get()

        if not party:
            messagebox.showwarning("Select", "Select a party.", parent=self)
            return

        self.tree.delete(*self.tree.get_children())

        ledger = load_json(LEDGER_FILE)
        ent = ledger.get(party, {})

        for t in ent.get("transactions", []):
            self.tree.insert(
                "",
                tk.END,
                values=(
                    t.get("date", ""),
                    t.get("type", ""),
                    t.get("invoice", ""),
                    t.get("credit", ""),
                    t.get("debit", ""),
                    t.get("remaining", ""),
                    t.get("amount", "")
                )
            )

# -------------------------
# Start the app
# -------------------------
if __name__ == "__main__":
    app = DashboardApp()
    app.mainloop()


#----------this is 02-12-2025