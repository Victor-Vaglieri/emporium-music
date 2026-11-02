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
_ACTIVE_DB_KEY: str | None = None

_POLICY_STOPWORDS = { # TODO isso aqui é necessario?
    "como",
    "para",
    "qual",
    "quais",
    "loja",
    "sobre",
    "pelo",
    "pela",
    "onde",
    "quando",
    "tem",
    "uma",
    "uns",
    "umas",
    "dos",
    "das",
    "nos",
    "nas",
    "por",
    "com",
    "sem",
    "que",
    "isso",
    "esse",
    "essa",
    "emporio",
    "musica",
}


# Normalize a database path into a canonical key so str and Path compare equally.
def _normalize_db_path(db_path: Path | str) -> str:
    raw = str(db_path).strip()
    if raw == ":memory:" or raw.startswith("file:"):
        return raw
    return str(Path(raw).resolve())


# Remove accents, convert to lowercase, and collapse extra spaces.
def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.lower().split())


# Open a SQLite connection with Row access, foreign keys enabled, and NORM() function.
def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.create_function("NORM", 1, normalize_text)
    return conn


# Return the shared SQLite connection, reusing it when the normalized path matches.
def get_db_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    global _ACTIVE_DB_CONN, _ACTIVE_DB_KEY
    target_key = _normalize_db_path(db_path)

    if _ACTIVE_DB_CONN is not None and (
        _ACTIVE_DB_KEY == target_key or _ACTIVE_DB_KEY == "__custom_injected__"
    ):
        return _ACTIVE_DB_CONN

    if _ACTIVE_DB_CONN is not None:
        try:
            _ACTIVE_DB_CONN.close()
        except sqlite3.Error:
            pass

    _ACTIVE_DB_CONN = init_database(db_path)
    _ACTIVE_DB_KEY = target_key
    return _ACTIVE_DB_CONN


# Set or reset the shared SQLite connection (used by automated tests).
def set_db_connection(conn: sqlite3.Connection | None) -> None:
    global _ACTIVE_DB_CONN, _ACTIVE_DB_KEY
    if _ACTIVE_DB_CONN is not None and _ACTIVE_DB_CONN is not conn:
        try:
            _ACTIVE_DB_CONN.close()
        except sqlite3.Error:
            pass
    _ACTIVE_DB_CONN = conn
    _ACTIVE_DB_KEY = "__custom_injected__" if conn is not None else None


# Create fresh tables with foreign keys and atomically load all 6 CSV files into SQLite.
def init_database(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    validate_data_files()
    conn = get_connection(db_path)
    cursor = conn.cursor()

    schema_Statements = [
        "DROP TABLE IF EXISTS order_items",
        "DROP TABLE IF EXISTS orders",
        "DROP TABLE IF EXISTS promotions",
        "DROP TABLE IF EXISTS products",
        "DROP TABLE IF EXISTS customers",
        "DROP TABLE IF EXISTS categories",
        (
            "CREATE TABLE categories ("
            "category_id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT)"
        ),
        (
            "CREATE TABLE customers ("
            "customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "phone TEXT, email TEXT, city TEXT)"
        ),
        (
            "CREATE TABLE products ("
            "product_id INTEGER PRIMARY KEY, price_brl REAL NOT NULL, name TEXT NOT NULL, "
            "category_id INTEGER NOT NULL, description TEXT, stock_quantity INTEGER NOT NULL, "
            "status TEXT NOT NULL, specs TEXT, created_at TEXT, "
            "FOREIGN KEY (category_id) REFERENCES categories(category_id))"
        ),
        (
            "CREATE TABLE promotions ("
            "promotion_id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, "
            "discount_percent REAL NOT NULL, description TEXT, is_active INTEGER NOT NULL, "
            "FOREIGN KEY (product_id) REFERENCES products(product_id))"
        ),
        (
            "CREATE TABLE orders ("
            "order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_date TEXT NOT NULL, "
            "status TEXT NOT NULL, total_brl REAL NOT NULL, payment_method TEXT NOT NULL, "
            "tracking_code TEXT, estimated_delivery TEXT, notes TEXT, "
            "FOREIGN KEY (customer_id) REFERENCES customers(customer_id))"
        ),
        (
            "CREATE TABLE order_items ("
            "order_id INTEGER NOT NULL, quantity INTEGER NOT NULL, product_id INTEGER NOT NULL, "
            "FOREIGN KEY (order_id) REFERENCES orders(order_id), "
            "FOREIGN KEY (product_id) REFERENCES products(product_id))"
        ),
    ]

    try:
        for stmt in schema_Statements:
            cursor.execute(stmt)

        _load_categories(cursor, CSV_FILES["categories"])
        _load_customers(cursor, CSV_FILES["customers"])
        _load_products(cursor, CSV_FILES["products"])
        _load_promotions(cursor, CSV_FILES["promotions"])
        _load_orders(cursor, CSV_FILES["orders"])
        _load_order_items(cursor, CSV_FILES["order_items"])

        conn.commit()
        return conn
    except Exception:
        conn.rollback()
        conn.close()
        raise


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


# Search customer records by email, phone number, or full/surname tokens.
def find_customers_by_identifier(
    identifier: str, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    db = conn or get_db_connection()
    raw = (identifier or "").strip()
    norm_id = normalize_text(raw)
    digits_id = "".join(ch for ch in raw if ch.isdigit())

    if not norm_id and not digits_id:
        return []

    rows = db.execute(
        "SELECT c.*, COUNT(o.order_id) AS order_count "
        "FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id "
        "GROUP BY c.customer_id ORDER BY c.customer_id ASC"
    ).fetchall()

    matches: list[dict[str, Any]] = []
    is_email = "@" in raw
    has_letters = any(ch.isalpha() for ch in norm_id)

    # 1. Email strategy: exact match on normalized email only.
    if is_email:
        for row in rows:
            cust = dict(row)
            if norm_id == normalize_text(cust["email"]):
                matches.append(cust)
        return matches

    # 2. Phone strategy: numeric input with at least 8 digits (matches full phone or local number).
    if not has_letters and len(digits_id) >= 8:
        for row in rows:
            cust = dict(row)
            cust_digits = "".join(ch for ch in (cust["phone"] or "") if ch.isdigit())
            if digits_id == cust_digits or (
                len(cust_digits) > 8 and digits_id == cust_digits[2:]
            ):
                matches.append(cust)
        return matches

    # 3. Name strategy: require at least two name tokens (e.g. first + last name) matched as whole words.
    name_tokens = [t for t in norm_id.split() if len(t) >= 2]
    if len(name_tokens) < 2:
        return []

    for row in rows:
        cust = dict(row)
        cust_name_norm = normalize_text(cust["name"])
        if all(
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
        if (
            re.search(rf"\b(?:secao|section)\s*{sec_num}\b", norm_topic)
            or norm_topic == sec_num
        ):
            matched_keys.append(sec_num)

    # Score normalized query tokens (excluding common stopwords) against headings and body text.
    tokens = [
        t for t in norm_topic.split() if len(t) >= 3 and t not in _POLICY_STOPWORDS
    ]
    scored_sections: list[tuple[int, str]] = []

    for sec_num, content in sections.items():
        if sec_num in matched_keys:
            continue
        lines = content.splitlines()
        heading_norm = normalize_text(lines[0]) if lines else ""
        body_norm = normalize_text(content)

        score = sum(3 for t in tokens if t in heading_norm) + sum(
            1 for t in tokens if t in body_norm
        )
        if score > 0:
            scored_sections.append((score, sec_num))

    scored_sections.sort(key=lambda item: item[0], reverse=True)
    matched_keys.extend(sec_num for _, sec_num in scored_sections[:3])

    # Include cover (section 0) and WhatsApp (section 7) together when company/contact info is matched.
    if any(k in matched_keys for k in ("0", "1", "7")):
        for contact_sec in ("0", "1", "7"):
            if contact_sec not in matched_keys and any(
                t in norm_topic
                for t in (
                    "telefone",
                    "whatsapp",
                    "contato",
                    "email",
                    "e-mail",
                    "endereco",
                )
            ):
                matched_keys.append(contact_sec)

    # Return a compact section directory + Section 1 if no keywords matched, avoiding full-PDF dumps.
    if not matched_keys:
        headings = [
            sec.splitlines()[0] for sec in sections.values() if sec.splitlines()
        ]
        summary = "No exact policy section matched the query. Available sections:\n" + "\n".join(
            f"- {h}" for h in headings
        )
        sec_one = sections.get("1", "")
        return f"{summary}\n\n---\n\n{sec_one}".strip()

    ordered_keys = sorted(set(matched_keys), key=lambda k: int(k))
    return "\n\n---\n\n".join(sections[k] for k in ordered_keys if k in sections)


# Engineering review notes (documented for README.md reference):
# 1. Resolved during refactoring:
#    - Enabled check_same_thread=False in get_connection() so LangGraph's ThreadPoolExecutor
#      ToolNode workers can safely query the shared SQLite connection initialized in the main thread.
#    - Separated customer lookup strategies by input type (exact email, full/local phone digits,
#      or >= 2 whole-word name tokens) so emails with digits never match phone numbers and single
#      first names are rejected while preserving homonym detection (Bruno/Diego).
#    - Normalized database paths (_normalize_db_path) and managed connection closing on switch/reset.
#    - Added foreign key constraints (PRAGMA foreign_keys = ON), parent-before-child table loading,
#      and atomic rollback/close if CSV ingestion fails.
#    - Filtered common Portuguese stopwords in search_policy_sections() and replaced full-PDF
#      fallback dumps with a compact section directory + Section 1 overview.
# 2. Known trade-offs left out to avoid over-engineering for this challenge:
#    - init_database() drops and recreates tables from the static CSV files on initialization
#      because SQLite is used here as a local query layer over the challenge dataset, not as
#      a persistent production write store.
#    - CSV rows are loaded directly without a heavy schema-validation layer since the 6 CSV files
#      are fixed challenge inputs and relational integrity is already checked by SQLite foreign keys.
#    - Promotions are filtered via the existing is_active flag and ordered by promotion_id DESC
#      because promotions.csv has 6 static rows and no start_date/end_date columns.
#    - load_policy_sections() uses @lru_cache without runtime file-watcher invalidation, and no extra
#      SQL indexes are created because the dataset is small (61 products, 45 customers, 20 orders).
