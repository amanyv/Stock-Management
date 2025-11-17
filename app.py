# app.py
from flask import Flask, g, request, jsonify, render_template, send_file, render_template_string, make_response
import sqlite3, os, csv, logging
from datetime import datetime
from io import BytesIO, StringIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

# -----------------------------
# HIDE WERKZEUG LOGS
# -----------------------------
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # hide GET/POST request logs

DB_PATH = "data.db"
app = Flask(__name__, template_folder="templates")


# -----------------------
# DB helpers & init
# -----------------------
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        need_init = not os.path.exists(DB_PATH)
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        if need_init:
            init_db(db)
    return db

def init_db(db):
    cur = db.cursor()
    cur.executescript("""
    CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        qty INTEGER DEFAULT 0,
        price REAL DEFAULT 0.0,
        category TEXT,
        default_tax REAL DEFAULT 0.0,
        default_discount REAL DEFAULT 0.0
    );
    CREATE TABLE customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        phone TEXT,
        address TEXT
    );
    CREATE TABLE invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_no TEXT,
        year INTEGER,
        customer_id INTEGER,
        customer_name TEXT,
        customer_phone TEXT,
        date TEXT,
        total REAL
    );
    CREATE TABLE invoice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id INTEGER,
        product_id INTEGER,
        item TEXT,
        qty INTEGER,
        price REAL,
        discount REAL,
        tax REAL,
        amount REAL
    );
    """)
    db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


# -----------------------
# Utility
# -----------------------
def next_invoice_no(db_conn):
    cur = db_conn.cursor()
    y = datetime.now().year
    cur.execute("SELECT COUNT(*) as cnt FROM invoices WHERE year=?", (y,))
    cnt = cur.fetchone()["cnt"]
    seq = cnt + 1
    invoice_no = f"INV-{y}-{seq:04d}"
    return invoice_no, y


# -----------------------
# UI route
# -----------------------
@app.route("/")
def index():
    return render_template("index.html")


# -----------------------
# Products
# -----------------------
@app.route("/api/products", methods=["GET"])
def api_products():
    db = get_db()
    rows = db.execute(
        "SELECT id, name, qty, price, category, default_tax, default_discount FROM products ORDER BY id DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/product/add", methods=["POST"])
def api_product_add():
    data = request.get_json()
    name = data.get("name", "").strip()
    qty = int(data.get("qty") or 0)
    price = float(data.get("price") or 0)
    cat = data.get("category") or ""
    default_tax = float(data.get("default_tax") or 0)
    default_discount = float(data.get("default_discount") or 0)

    db = get_db()
    db.execute(
        "INSERT INTO products (name, qty, price, category, default_tax, default_discount) VALUES (?, ?, ?, ?, ?, ?)",
        (name, qty, price, cat, default_tax, default_discount),
    )
    db.commit()
    return jsonify(success=True)

@app.route("/api/product/edit", methods=["POST"])
def api_product_edit():
    data = request.get_json()
    pid = int(data.get("id"))
    name = data.get("name", "").strip()
    qty = int(data.get("qty") or 0)
    price = float(data.get("price") or 0)
    cat = data.get("category") or ""
    default_tax = float(data.get("default_tax") or 0)
    default_discount = float(data.get("default_discount") or 0)

    db = get_db()
    db.execute(
        "UPDATE products SET name=?, qty=?, price=?, category=?, default_tax=?, default_discount=? WHERE id=?",
        (name, qty, price, cat, default_tax, default_discount, pid),
    )
    db.commit()
    return jsonify(success=True)

@app.route("/api/product/delete", methods=["POST"])
def api_product_delete():
    data = request.get_json()
    pid = int(data.get("id"))

    db = get_db()
    db.execute("DELETE FROM products WHERE id=?", (pid,))
    db.commit()
    return jsonify(success=True)


# -----------------------
# Customers
# -----------------------
@app.route("/api/customers", methods=["GET"])
def api_customers():
    db = get_db()
    rows = db.execute(
        "SELECT id, name, phone, address FROM customers ORDER BY id DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/customer/add", methods=["POST"])
def api_customer_add():
    data = request.get_json()
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    address = data.get("address", "").strip()

    db = get_db()
    db.execute(
        "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
        (name, phone, address),
    )
    db.commit()
    return jsonify(success=True)


# -----------------------
# Invoices
# -----------------------
@app.route("/api/invoices", methods=["GET"])
def api_invoices():
    db = get_db()
    rows = db.execute(
        "SELECT id, invoice_no, customer_name, customer_phone, date, total FROM invoices ORDER BY id DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/invoice/<int:inv_id>", methods=["GET"])
def api_invoice_detail(inv_id):
    db = get_db()
    inv = db.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv:
        return jsonify(error="Not found"), 404

    items = db.execute(
        "SELECT * FROM invoice_items WHERE invoice_id=?", (inv_id,)
    ).fetchall()
    return jsonify(invoice=dict(inv), items=[dict(r) for r in items])

@app.route("/api/invoice/save", methods=["POST"])
def api_invoice_save():
    data = request.get_json()
    customer_id = data.get("customer_id")
    customer_name = data.get("customer") or ""
    customer_phone = data.get("phone") or ""
    inv_date = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    total = float(data.get("total") or 0)
    items = data.get("items") or []

    db = get_db()
    cur = db.cursor()

    invoice_no, year = next_invoice_no(db)

    # Stock check
    for it in items:
        pid = int(it.get("product_id") or 0)
        qty = int(it.get("qty") or 0)
        if pid:
            prod = db.execute(
                "SELECT qty, name FROM products WHERE id=?", (pid,)
            ).fetchone()
            if prod and prod["qty"] < qty:
                return jsonify(
                    success=False,
                    error=f"Insufficient stock for {prod['name']} (have {prod['qty']}, need {qty})",
                ), 400

    # Insert invoice
    cur.execute(
        "INSERT INTO invoices (invoice_no, year, customer_id, customer_name, customer_phone, date, total) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (invoice_no, year, customer_id, customer_name, customer_phone, inv_date, total),
    )
    invoice_id = cur.lastrowid

    # Insert items
    for it in items:
        pid = int(it.get("product_id") or 0)
        item_name = it.get("item") or ""
        qty = int(it.get("qty") or 0)
        price = float(it.get("price") or 0)
        discount = float(it.get("discount") or 0)
        tax = float(it.get("tax") or 0)
        amount = float(it.get("amount") or 0)

        cur.execute(
            "INSERT INTO invoice_items (invoice_id, product_id, item, qty, price, discount, tax, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (invoice_id, pid, item_name, qty, price, discount, tax, amount),
        )

        if pid:
            cur.execute("UPDATE products SET qty = qty - ? WHERE id=?", (qty, pid))

    db.commit()
    return jsonify(success=True, id=invoice_id, invoice_no=invoice_no)


# -----------------------
# UPDATE INVOICE
# -----------------------
@app.route("/api/invoice/update/<int:inv_id>", methods=["POST"])
def api_invoice_update(inv_id):
    data = request.get_json()
    customer_name = data.get("customer") or ""
    customer_phone = data.get("phone") or ""
    inv_date = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    total = float(data.get("total") or 0)
    items = data.get("items") or []

    db = get_db()
    cur = db.cursor()

    # Restore stock from old items
    old_items = cur.execute(
        "SELECT product_id, qty FROM invoice_items WHERE invoice_id=?", (inv_id,)
    ).fetchall()

    for oi in old_items:
        if oi["product_id"]:
            cur.execute(
                "UPDATE products SET qty = qty + ? WHERE id=?",
                (oi["qty"], oi["product_id"]),
            )

    # Delete old items
    cur.execute("DELETE FROM invoice_items WHERE invoice_id=?", (inv_id,))

    # Check stock for new items
    for it in items:
        pid = int(it.get("product_id") or 0)
        qty = int(it.get("qty") or 0)
        if pid:
            prod = cur.execute(
                "SELECT qty, name FROM products WHERE id=?", (pid,)
            ).fetchone()
            if prod and prod["qty"] < qty:
                db.rollback()
                return jsonify(
                    success=False,
                    error=f"Insufficient stock for {prod['name']} (have {prod['qty']}, need {qty})",
                ), 400

    # Insert new items + update stock
    for it in items:
        pid = int(it.get("product_id") or 0)
        item_name = it.get("item") or ""
        qty = int(it.get("qty") or 0)
        price = float(it.get("price") or 0)
        discount = float(it.get("discount") or 0)
        tax = float(it.get("tax") or 0)
        amount = float(it.get("amount") or 0)

        cur.execute(
            "INSERT INTO invoice_items (invoice_id, product_id, item, qty, price, discount, tax, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (inv_id, pid, item_name, qty, price, discount, tax, amount),
        )

        if pid:
            cur.execute("UPDATE products SET qty = qty - ? WHERE id=?", (qty, pid))

    # Update invoice header
    cur.execute(
        "UPDATE invoices SET customer_name=?, customer_phone=?, date=?, total=? WHERE id=?",
        (customer_name, customer_phone, inv_date, total, inv_id),
    )

    db.commit()
    return jsonify(success=True)


# -----------------------
# DELETE INVOICE
# -----------------------
@app.route("/api/invoice/delete/<int:inv_id>", methods=["POST"])
def api_invoice_delete(inv_id):
    db = get_db()
    cur = db.cursor()

    # Restore stock
    rows = cur.execute(
        "SELECT product_id, qty FROM invoice_items WHERE invoice_id=?", (inv_id,)
    ).fetchall()

    for it in rows:
        if it["product_id"]:
            cur.execute(
                "UPDATE products SET qty = qty + ? WHERE id=?",
                (it["qty"], it["product_id"]),
            )

    cur.execute("DELETE FROM invoice_items WHERE invoice_id=?", (inv_id,))
    cur.execute("DELETE FROM invoices WHERE id=?", (inv_id,))
    db.commit()

    return jsonify(success=True)


# -----------------------
# Reports
# -----------------------
@app.route("/api/reports/sales_summary", methods=["GET"])
def api_report_sales_summary():
    period = request.args.get("period", "day")
    db = get_db()
    cur = db.cursor()

    if period == "month":
        rows = cur.execute("""
            SELECT strftime('%Y-%m', date) AS period, SUM(total) AS total
            FROM invoices GROUP BY period ORDER BY period DESC
        """).fetchall()
    else:
        rows = cur.execute("""
            SELECT date AS period, SUM(total) AS total
            FROM invoices GROUP BY date ORDER BY date DESC
        """).fetchall()

    return jsonify([dict(r) for r in rows])

@app.route("/api/reports/top_products", methods=["GET"])
def api_report_top_products():
    db = get_db()
    rows = db.execute("""
        SELECT item, SUM(qty) as sold_qty, SUM(amount) as revenue
        FROM invoice_items GROUP BY item ORDER BY sold_qty DESC LIMIT 20
    """).fetchall()
    return jsonify([dict(r) for r in rows])


# -----------------------
# Print View
# -----------------------
@app.route("/invoice/<int:inv_id>/print", methods=["GET"])
def invoice_print_view(inv_id):
    db = get_db()

    inv = db.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv:
        return "Invoice not found", 404

    items = db.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (inv_id,)).fetchall()

    html = render_template_string("""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Invoice {{inv.invoice_no}}</title>
      <style>
        body { font-family:Arial; margin:20px; }
        table { width:100%; border-collapse:collapse; }
        th,td { border:1px solid #ccc; padding:8px; }
      </style>
    </head>
    <body>
      <h2>Invoice {{inv.invoice_no}}</h2>
      <p><strong>Customer:</strong> {{inv.customer_name}}</p>
      <p><strong>Phone:</strong> {{inv.customer_phone}}</p>
      <p><strong>Date:</strong> {{inv.date}}</p>
      <hr>
      <table>
        <thead>
          <tr>
            <th>Item</th><th>Qty</th><th>Price</th><th>Disc</th><th>Tax</th><th>Amount</th>
          </tr>
        </thead>
        <tbody>
          {% for it in items %}
          <tr>
            <td>{{it.item}}</td>
            <td>{{it.qty}}</td>
            <td>₹{{'%.2f'|format(it.price)}}</td>
            <td>{{'%.2f'|format(it.discount)}}</td>
            <td>{{'%.2f'|format(it.tax)}}</td>
            <td>₹{{'%.2f'|format(it.amount)}}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>

      <h3 style="text-align:right">Total: ₹{{'%.2f'|format(inv.total)}}</h3>

      <script>window.onload=function(){ window.print(); }</script>
    </body>
    </html>
    """, inv=dict(inv), items=[dict(r) for r in items])

    return html


# -----------------------
# PDF Generator
# -----------------------
@app.route("/invoice/<int:inv_id>/pdf", methods=["GET"])
def invoice_pdf(inv_id):
    db = get_db()

    inv = db.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv:
        return "Not found", 404

    items = db.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (inv_id,)).fetchall()

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    x = 20 * mm
    y = height - 20 * mm

    p.setFont("Helvetica-Bold", 16)
    p.drawString(x, y, f"Invoice {inv['invoice_no']}")

    p.setFont("Helvetica", 9)
    y -= 8 * mm
    p.drawString(x, y, f"Date: {inv['date']}")

    y -= 6 * mm
    p.drawString(x, y, f"Customer: {inv['customer_name']}")

    y -= 10 * mm
    p.setFont("Helvetica-Bold", 9)
    p.drawString(x, y, "Item")
    p.drawString(x + 90 * mm, y, "Qty")
    p.drawString(x + 110 * mm, y, "Price")
    p.drawString(x + 140 * mm, y, "Amount")

    p.setFont("Helvetica", 9)
    y -= 6 * mm

    for it in items:
        if y < 30 * mm:
            p.showPage()
            y = height - 20 * mm

        p.drawString(x, y, str(it["item"])[:45])
        p.drawRightString(x + 100 * mm, y, str(it["qty"]))
        p.drawRightString(x + 130 * mm, y, f"{it['price']:.2f}")
        p.drawRightString(width - x, y, f"{it['amount']:.2f}")

        y -= 6 * mm

    y -= 8 * mm
    p.setFont("Helvetica-Bold", 11)
    p.drawRightString(width - x, y, f"Total: ₹{inv['total']:.2f}")

    p.showPage()
    p.save()

    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"invoice_{inv['invoice_no']}.pdf"
    )


# -----------------------
# CSV EXPORTS
# -----------------------
@app.route("/export/products.csv", methods=["GET"])
def export_products_csv():
    db = get_db()
    rows = db.execute(
        "SELECT id, name, qty, price, category, default_tax, default_discount FROM products"
    ).fetchall()

    si = StringIO()
    cw = csv.writer(si)

    cw.writerow(["id", "name", "qty", "price", "category", "default_tax", "default_discount"])
    for r in rows:
        cw.writerow([
            r["id"], r["name"], r["qty"], r["price"], r["category"],
            r["default_tax"], r["default_discount"]
        ])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=products.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/export/invoices.csv", methods=["GET"])
def export_invoices_csv():
    db = get_db()
    rows = db.execute(
        "SELECT id, invoice_no, customer_name, customer_phone, date, total FROM invoices"
    ).fetchall()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["id", "invoice_no", "customer_name", "customer_phone", "date", "total"])

    for r in rows:
        cw.writerow([
            r["id"], r["invoice_no"], r["customer_name"],
            r["customer_phone"], r["date"], r["total"]
        ])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=invoices.csv"
    output.headers["Content-type"] = "text/csv"
    return output


# -----------------------
# Launch
# -----------------------
if __name__ == "__main__":
    with app.app_context():
        get_db()  # ensure DB is created

    app.run(debug=False, host="0.0.0.0", port=5000)
