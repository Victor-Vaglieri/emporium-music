# SQLite database initialization, CSV ingestion, query helpers, and PDF policy extraction.

import csv
import re
import sqlite3
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any
from pypdf import PdfReader
from src.config import CSV_FILES, DB_PATH, POLICIES_PDF_PATH, validate_data_files

_ACTIVE_DB_CONN: sqlite3.Connection | None = None


# Remove accents, convert to lowercase, and collapse extra spaces.
def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.lower().split())


# Open a SQLite connection with Row access and custom NORM() SQL function.
def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.create_function("NORM", 1, normalize_text)
    return conn


# Return the shared SQLite connection (initializing tables from CSVs on first use).
def get_db_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    global _ACTIVE_DB_CONN
    if db_path != DB_PATH:
        return init_database(db_path)
    if _ACTIVE_DB_CONN is None:
        _ACTIVE_DB_CONN = init_database(DB_PATH)
    return _ACTIVE_DB_CONN


# Set or reset the shared SQLite connection (used by automated tests).
def set_db_connection(conn: sqlite3.Connection | None) -> None:
    global _ACTIVE_DB_CONN
    _ACTIVE_DB_CONN = conn


# Create fresh tables and load all 6 CSV files into SQLite.
def init_database(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    validate_data_files()
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.executescript(
        "DROP TABLE IF EXISTS order_items;\n"
        "DROP TABLE IF EXISTS orders;\n"
        "DROP TABLE IF EXISTS promotions;\n"
        "DROP TABLE IF EXISTS products;\n"
        "DROP TABLE IF EXISTS customers;\n"
        "DROP TABLE IF EXISTS categories;\n"
        "CREATE TABLE categories ("
        "category_id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT);\n"
        "CREATE TABLE customers ("
        "customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, email TEXT, city TEXT);\n"
        "CREATE TABLE orders ("
        "order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_date TEXT NOT NULL, "
        "status TEXT NOT NULL, total_brl REAL NOT NULL, payment_method TEXT NOT NULL, "
        "tracking_code TEXT, estimated_delivery TEXT, notes TEXT);\n"
        "CREATE TABLE order_items ("
        "order_id INTEGER NOT NULL, quantity INTEGER NOT NULL, product_id INTEGER NOT NULL);\n"
        "CREATE TABLE products ("
        "product_id INTEGER PRIMARY KEY, price_brl REAL NOT NULL, name TEXT NOT NULL, "
        "category_id INTEGER NOT NULL, description TEXT, stock_quantity INTEGER NOT NULL, "
        "status TEXT NOT NULL, specs TEXT, created_at TEXT);\n"
        "CREATE TABLE promotions ("
        "promotion_id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, "
        "discount_percent REAL NOT NULL, description TEXT, is_active INTEGER NOT NULL);"
    )

    _load_categories(cursor, CSV_FILES["categories"])
    _load_customers(cursor, CSV_FILES["customers"])
    _load_orders(cursor, CSV_FILES["orders"])
    _load_order_items(cursor, CSV_FILES["order_items"])
    _load_products(cursor, CSV_FILES["products"])
    _load_promotions(cursor, CSV_FILES["promotions"])

    conn.commit()
    return conn


# Load categories.csv rows into SQLite.
def _load_categories(cursor: sqlite3.Cursor, csv_path: Path) -> None:
    with csv_path.open(mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (int(r["category_id"]), r["name"].strip(), r["description"].strip())
            for r in reader
        ]
    cursor.executemany("INSERT INTO categories VALUES (?, ?, ?)", rows)


# Load customers.csv rows into SQLite.
def _load_customers(cursor: sqlite3.Cursor, csv_path: Path) -> None:
    with csv_path.open(mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                int(r["customer_id"]),
                r["name"].strip(),
                r["phone"].strip(),
                r["email"].strip(),
                r["city"].strip(),
            )
            for r in reader
        ]
    cursor.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", rows)


# Load orders.csv rows into SQLite.
def _load_orders(cursor: sqlite3.Cursor, csv_path: Path) -> None:
    with csv_path.open(mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                int(r["order_id"]),
                int(r["customer_id"]),
                r["order_date"].strip(),
                r["status"].strip(),
                float(r["total_brl"]),
                r["payment_method"].strip(),
                r["tracking_code"].strip(),
                r["estimated_delivery"].strip(),
                r["notes"].strip(),
            )
            for r in reader
        ]
    cursor.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


# Load order_items.csv rows into SQLite.
def _load_order_items(cursor: sqlite3.Cursor, csv_path: Path) -> None:
    with csv_path.open(mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (int(r["order_id"]), int(r["quantity"]), int(r["product_id"]))
            for r in reader
        ]
    cursor.executemany("INSERT INTO order_items VALUES (?, ?, ?)", rows)


# Load products.csv rows into SQLite.
def _load_products(cursor: sqlite3.Cursor, csv_path: Path) -> None:
    with csv_path.open(mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                int(r["product_id"]),
                float(r["price_brl"]),
                r["name"].strip(),
                int(r["category_id"]),
                r["description"].strip(),
                int(r["stock_quantity"]),
                r["status"].strip(),
                r["specs"].strip(),
                r["created_at"].strip(),
            )
            for r in reader
        ]
    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


# Load promotions.csv rows into SQLite.
def _load_promotions(cursor: sqlite3.Cursor, csv_path: Path) -> None:
    with csv_path.open(mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [
            (
                int(r["promotion_id"]),
                int(r["product_id"]),
                float(r["discount_percent"]),
                r["description"].strip(),
                int(r["is_active"]),
            )
            for r in reader
        ]
    cursor.executemany("INSERT INTO promotions VALUES (?, ?, ?, ?, ?)", rows)


# Fetch all categories along with how many products belong to each category.
def fetch_categories_with_counts(
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    db = conn or get_db_connection()
    rows = db.execute(
        "SELECT c.category_id, c.name, c.description, COUNT(p.product_id) AS prod_count "
        "FROM categories c LEFT JOIN products p ON c.category_id = p.category_id "
        "GROUP BY c.category_id ORDER BY c.category_id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


# Fetch products joined with category names and batch-loaded promotions (avoids N+1 queries).
def fetch_products_with_promotions(
    conn: sqlite3.Connection | None = None,
    product_id: int | None = None,
    category_id: int | None = None,
    category_query: str | None = None,
    only_available: bool = False,
) -> list[dict[str, Any]]:
    db = conn or get_db_connection()
    sql = (
        "SELECT p.*, c.name AS category_name "
        "FROM products p "
        "JOIN categories c ON p.category_id = c.category_id "
        "WHERE 1=1"
    )
    params: list[Any] = []

    if product_id is not None:
        sql += " AND p.product_id = ?"
        params.append(int(product_id))

    if category_id is not None:
        sql += " AND p.category_id = ?"
        params.append(int(category_id))

    norm_cat = normalize_text(category_query)
    if norm_cat:
        sql += " AND (NORM(c.name) LIKE ? OR NORM(c.description) LIKE ?)"
        params.extend([f"%{norm_cat}%", f"%{norm_cat}%"])

    if only_available:
        sql += " AND p.status = 'active' AND p.stock_quantity > 0"

    product_rows = db.execute(sql + " ORDER BY p.price_brl ASC", params).fetchall()
    if not product_rows:
        return []

    # Batch-load all promotions in a single query and group by product_id.
    promo_rows = db.execute(
        "SELECT * FROM promotions ORDER BY promotion_id DESC"
    ).fetchall()

    active_by_pid: dict[int, dict[str, Any]] = {}
    expired_by_pid: dict[int, list[dict[str, Any]]] = {}

    for pr in promo_rows:
        promo = dict(pr)
        pid = int(promo["product_id"])
        if int(promo.get("is_active", 0)) == 1:
            active_by_pid.setdefault(pid, promo)
        else:
            expired_by_pid.setdefault(pid, []).append(promo)

    results: list[dict[str, Any]] = []
    for row in product_rows:
        item = dict(row)
        pid = int(item["product_id"])
        item["active_promotion"] = active_by_pid.get(pid)
        item["expired_promotions"] = expired_by_pid.get(pid, [])
        results.append(item)

    return results


# Fetch a single order by ID joined with customer verification fields and order items.
def fetch_order_with_details(
    order_id: int, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    db = conn or get_db_connection()
    row = db.execute(
        "SELECT o.*, c.name AS customer_name, c.email AS customer_email, "
        "c.phone AS customer_phone, c.city AS customer_city "
        "FROM orders o "
        "JOIN customers c ON o.customer_id = c.customer_id "
        "WHERE o.order_id = ?",
        (int(order_id),),
    ).fetchone()

    if row is None:
        return None

    item_rows = db.execute(
        "SELECT oi.quantity, oi.product_id, p.name AS product_name, "
        "p.price_brl, p.category_id, c.name AS category_name "
        "FROM order_items oi "
        "JOIN products p ON oi.product_id = p.product_id "
        "JOIN categories c ON p.category_id = c.category_id "
        "WHERE oi.order_id = ? ORDER BY oi.product_id ASC",
        (int(order_id),),
    ).fetchall()

    order_dict = dict(row)
    order_dict["items"] = [dict(ir) for ir in item_rows]
    return order_dict


# Search customer records by name, email, or phone digits to detect homonyms or shared emails.
def find_customers_by_identifier(
    identifier: str, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    db = conn or get_db_connection()
    norm_id = normalize_text(identifier)
    digits_id = "".join(ch for ch in (identifier or "") if ch.isdigit())

    if not norm_id and not digits_id:
        return []

    rows = db.execute(
        "SELECT c.*, COUNT(o.order_id) AS order_count "
        "FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id "
        "GROUP BY c.customer_id ORDER BY c.customer_id ASC"
    ).fetchall()

    matches: list[dict[str, Any]] = []
    name_tokens = [t for t in norm_id.split() if len(t) >= 2]

    for row in rows:
        cust = dict(row)
        cust_name_norm = normalize_text(cust["name"])
        cust_email_norm = normalize_text(cust["email"])
        cust_digits = "".join(ch for ch in (cust["phone"] or "") if ch.isdigit())

        if norm_id and norm_id == cust_email_norm:
            matches.append(cust)
            continue

        if len(digits_id) >= 8 and digits_id in cust_digits:
            matches.append(cust)
            continue

        if name_tokens and all(
            re.search(rf"\b{re.escape(tok)}\b", cust_name_norm) for tok in name_tokens
        ):
            matches.append(cust)

    return matches



# Extract text from the store policy PDF and split it into numbered sections.
@lru_cache(maxsize=4)
def load_policy_sections(pdf_path: Path = POLICIES_PDF_PATH) -> dict[str, str]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"Policy PDF not found at: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    raw_lines: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text(extraction_mode="layout") or ""
        for line in page_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "Empório da Música Manual de Políticas e Procedimentos":
                continue
            if re.match(r"^Página\s+\d+$", stripped):
                continue
            raw_lines.append(stripped)

    sections: dict[str, list[str]] = {"0": ["0. Cover and Store Identification"]}
    current_key = "0"

    for line in raw_lines:
        match = re.match(r"^(10|[1-9])\. [A-ZÁÉÍÓÚÂÊÔÃÕÇ]", line)
        if match:
            current_key = match.group(1)
            sections.setdefault(current_key, [])
        sections[current_key].append(line)

    return {key: "\n".join(lines) for key, lines in sections.items()}


# Retrieve the policy sections that match a given topic or question.
def search_policy_sections(topic: str, pdf_path: Path = POLICIES_PDF_PATH) -> str:
    sections = load_policy_sections(pdf_path)
    norm_topic = normalize_text(topic)

    if not norm_topic:
        return "\n\n".join(sections.values())

    matched_keys: list[str] = []

    # Check if a specific section number was requested directly.
    for sec_num in sections:
        if re.search(rf"\b(?:secao|section)\s*{sec_num}\b", norm_topic) or norm_topic == sec_num:
            matched_keys.append(sec_num)

    # Search normalized query tokens directly inside the extracted PDF sections.
    tokens = [t for t in norm_topic.split() if len(t) >= 3]
    scored_sections: list[tuple[int, str]] = []

    for sec_num, content in sections.items():
        if sec_num in matched_keys:
            continue
        lines = content.splitlines()
        heading_norm = normalize_text(lines[0]) if lines else ""
        body_norm = normalize_text(content)

        # Give higher weight when a token appears in the section title.
        score = sum(3 for t in tokens if t in heading_norm) + sum(
            1 for t in tokens if t in body_norm
        )
        if score > 0:
            scored_sections.append((score, sec_num))

    scored_sections.sort(key=lambda item: item[0], reverse=True)
    matched_keys.extend(sec_num for _, sec_num in scored_sections)

    # Include cover (section 0) and WhatsApp (section 7) together when company/contact info is matched.
    if any(k in matched_keys for k in ("0", "1", "7")):
        for contact_sec in ("0", "1", "7"):
            if contact_sec not in matched_keys and any(
                t in norm_topic for t in ("telefone", "whatsapp", "contato", "email", "e-mail", "endereco")
            ):
                matched_keys.append(contact_sec)

    if not matched_keys:
        return "\n\n".join(sections.values())

    ordered_keys = sorted(set(matched_keys), key=lambda k: int(k))
    return "\n\n---\n\n".join(sections[k] for k in ordered_keys if k in sections)

