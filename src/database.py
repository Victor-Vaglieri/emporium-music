# SQLite database initialization, CSV ingestion, and PDF policy extraction.

import csv
import re
import sqlite3
import unicodedata
from functools import lru_cache
from pathlib import Path
from pypdf import PdfReader
from src.config import CSV_FILES, DB_PATH, POLICIES_PDF_PATH, validate_data_files


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


