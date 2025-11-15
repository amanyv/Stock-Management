from flask import Flask, render_template, request, jsonify, redirect, url_for
import pandas as pd
import os

app = Flask(__name__)

FILE = "products.xlsx"
LOW_STOCK_THRESHOLD = 10


def ensure_excel():
    """Create products.xlsx with correct headers if missing/invalid."""
    required_cols = ["id", "name", "quantity", "price", "category"]
    if not os.path.exists(FILE):
        df = pd.DataFrame(columns=required_cols)
        df.to_excel(FILE, index=False)
        return

    try:
        df = pd.read_excel(FILE)
        if df.empty or not all(col in df.columns for col in required_cols):
            df = pd.DataFrame(columns=required_cols)
            df.to_excel(FILE, index=False)
    except Exception:
        df = pd.DataFrame(columns=required_cols)
        df.to_excel(FILE, index=False)


def load_products():
    """Load and sanitize product data."""
    ensure_excel()
    df = pd.read_excel(FILE)
    products = []
    for row in df.to_dict(orient="records"):
        try:
            pid = str(row.get("id", "")).strip()
            name = str(row.get("name", "")) if pd.notna(row.get("name", "")) else ""
            raw_q = row.get("quantity", 0)
            raw_p = row.get("price", 0.0)
            quantity = int(raw_q) if not pd.isna(raw_q) and raw_q != "" else 0
            price = float(raw_p) if not pd.isna(raw_p) and raw_p != "" else 0.0
            category = str(row.get("category", "")) if pd.notna(row.get("category", "")) else ""
            products.append({
                "id": pid,
                "name": name,
                "quantity": quantity,
                "price": price,
                "category": category
            })
        except Exception:
            continue
    return products


def save_products(products):
    df = pd.DataFrame(products)
    df.to_excel(FILE, index=False)


@app.route("/")
def index():
    products = load_products()

    total_products = len(products)
    total_value = sum(p["quantity"] * p["price"] for p in products)
    low_stock = sum(1 for p in products if p["quantity"] < LOW_STOCK_THRESHOLD)

    categories = {}
    for p in products:
        cat = p["category"] or "Uncategorized"
        categories[cat] = categories.get(cat, 0) + p["quantity"]

    chart_data = {
        "labels": [p["name"] for p in products],
        "quantities": [p["quantity"] for p in products],
        "categories": list(categories.keys()),
        "category_values": list(categories.values())
    }

    return render_template(
        "index.html",
        products=products,
        total_products=total_products,
        total_value=total_value,
        low_stock=low_stock,
        low_stock_threshold=LOW_STOCK_THRESHOLD,
        chart_data=chart_data
    )


@app.route("/api/add", methods=["POST"])
def api_add():
    products = load_products()
    data = request.get_json(force=True) or {}

    existing_ids = []
    for p in products:
        try:
            existing_ids.append(int(p["id"]))
        except Exception:
            continue
    next_id = str(max(existing_ids) + 1) if existing_ids else "1"
    pid = str(data.get("id", "")).strip() or next_id

    try:
        product = {
            "id": pid,
            "name": str(data.get("name", "")).strip(),
            "quantity": int(data.get("quantity", 0)),
            "price": float(data.get("price", 0.0)),
            "category": str(data.get("category", "")).strip()
        }
    except Exception as e:
        return jsonify({"success": False, "message": f"Invalid input: {e}"}), 400

    products.append(product)
    save_products(products)
    return jsonify({"success": True, "product": product})


@app.route("/api/edit/<id>", methods=["POST"])
def api_edit(id):
    products = load_products()
    data = request.get_json(force=True) or {}
    updated = False
    for p in products:
        if str(p["id"]) == str(id):
            p["name"] = str(data.get("name", p["name"])).strip()
            p["quantity"] = int(data.get("quantity", p["quantity"]))
            p["price"] = float(data.get("price", p["price"]))
            p["category"] = str(data.get("category", p["category"])).strip()
            updated = True
            break
    if not updated:
        return jsonify({"success": False, "message": "Product not found"}), 404
    save_products(products)
    return jsonify({"success": True})


@app.route("/delete/<id>", methods=["POST"])
def delete(id):
    products = load_products()
    products = [p for p in products if str(p["id"]) != str(id)]
    save_products(products)
    return redirect(url_for("index"))


if __name__ == "__main__":
    ensure_excel()
    app.run(debug=True)

