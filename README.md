# Empório da Música — AI Customer Support Agent

Conversational AI customer support assistant built in Python for **Empório da Música**, a musical instrument store in Campo Grande, MS, Brazil.

---

## 1. Overview & Core Architectural Principle

The central design principle of this project is the **strict separation between Deterministic Business Logic and Conversational Intelligence**:

- **The LLM never calculates prices, discounts, installments, or deadlines in its head, nor does it parse raw CSV files freely.** All commercial calculations (5% PIX discount vs. active promotions, minimum installment thresholds, split-payment eligibility, post-sale return/exchange/warranty deadlines), order ownership verification, and technical catalog conflict detection are handled by deterministic Python functions and SQLite queries across `src/database.py`, `src/rules.py`, and `src/tools.py`, backed by 23 automated `pytest` tests (`tests/test_rules_and_tools.py`).
- **Role of the LLM**: Understand customer intent, select the appropriate `@tool` (`src/tools.py`), follow the privacy and scope instructions defined in `src/prompts.py` (while the tools enforce verification checks), and transform structured tool outputs into accurate Brazilian Portuguese responses aligned with the store persona (*"a knowledgeable musician friend"*).

---

## 2. Project Structure

```text
music-emporium/
├── data/                                            # Original challenge dataset (untouched)
│   ├── desafio_tecnico_ai_eng - categories.csv      # 9 instrument categories (3 with 0 products)
│   ├── desafio_tecnico_ai_eng - customers.csv       # 50 customer records (homonyms & shared emails)
│   ├── desafio_tecnico_ai_eng - orders.csv          # 20 customer orders
│   ├── desafio_tecnico_ai_eng - order_items.csv     # 22 order line items
│   ├── desafio_tecnico_ai_eng - products.csv        # 65 products (active, out-of-stock, discontinued, coming_soon)
│   ├── desafio_tecnico_ai_eng - promotions.csv      # 25 promotion records (active and inactive)
│   └── políticas_da_loja.pdf                        # Official 8-page store policy manual (10 sections)
├── src/
│   ├── __init__.py
│   ├── config.py                                    # Paths, data file checks, and Gemini/Groq provider settings
│   ├── database.py                                  # SQLite ingestion, batch relational queries, and PDF section parser
│   ├── rules.py                                     # Pure deterministic functions (pricing, PIX, installments, deadlines, conflicts)
│   ├── tools.py                                     # 4 LangChain @tool functions connecting SQLite/PDF + rules.py
│   ├── prompts.py                                   # Persona system prompt, tool-calling rules, and security guardrails
│   └── agent.py                                     # LangGraph ReAct agent builder + in-memory session state
├── tests/
│   └── test_rules_and_tools.py                      # 23 deterministic unit and integration tests
├── main.py                                          # Interactive Rich terminal CLI and single-query runner
├── .env.example                                     # Environment variable template
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 3. Quick Start & Usage

### 3.1. Environment Setup

Requires **Python 3.11+**.

```powershell
# 1. Create and activate a virtual environment (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# (On Linux/macOS: source .venv/bin/activate)

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
Copy-Item .env.example .env
```

Edit `.env` and configure an API key for **Google Gemini** (default) or **Groq**:

```ini
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_google_ai_studio_api_key
GEMINI_MODEL=gemini-2.5-flash

# Optional alternative provider:
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.3-70b-versatile
```

> **Note on Gemini models**: `src/config.py` defaults to `gemini-2.5-flash` when `GEMINI_MODEL` is not set, and accepts any compatible model configured in `.env` (such as `gemini-2.5-flash-lite` or `gemini-3.1-flash-lite`, which were used when recording the live interaction transcripts under Google AI Studio Free Tier daily quotas).

### 3.2. Running the Interactive CLI (`main.py`)

```powershell
# Start the interactive terminal chat (uses default provider from .env)
python main.py

# Override the LLM provider via CLI flag ('gemini' or 'groq')
python main.py --provider gemini
python main.py --provider groq

# Run a single non-interactive query and exit
python main.py --query "Quais violões Yamaha vocês têm em estoque?"
```

Inside the interactive CLI:
- Type `/reset` to start a new conversation session (`thread_id`).
- Type `exit`, `quit`, `/exit`, `/quit`, or `/sair` to close the assistant.

### 3.3. Running the Automated Test Suite

The test suite (`23` tests in `tests/test_rules_and_tools.py`) runs entirely offline using an in-memory SQLite database (`:memory:`) and a deterministic `_ScriptedChatModel` that replays multi-tool `AIMessage` sequences through LangGraph's `create_react_agent`:

```powershell
pytest -v
```

---

## 4. Technical Decisions & Trade-offs

| Component | Technical Choice | Technical Rationale |
| :--- | :--- | :--- |
| **Agent Orchestration** | **LangGraph + LangChain** (`create_react_agent`) | Provides a clean, inspectable ReAct loop with explicit tool execution (`ToolNode`), configurable recursion limits (`DEFAULT_RECURSION_LIMIT = 15` in `src/agent.py`), and built-in state management. |
| **LLM Provider Layer** | **Configurable Gemini / Groq** (`src/config.py`) | Supports `gemini-2.5-flash` (default) and `llama-3.3-70b-versatile` via environment variables or `--provider` CLI flag. Note: this is a clean configuration switch for evaluation flexibility, not a runtime failover cluster. |
| **Tabular Data (`*.csv`)** | **SQLite + Batch Relational Queries** (`src/database.py`) | Loads all 6 CSVs into SQLite with `PRAGMA foreign_keys = ON` and custom accent-insensitive normalization (`NORM()`). Centralizes all SQL inside `src/database.py` with batch promotion loading so `src/tools.py` contains zero raw SQL queries and avoids N+1 query overhead. |
| **Policy Manual (`políticas_da_loja.pdf`)** | **Structured Section Extraction (`pypdf`)** | The policy manual is an 8-page document with 10 numbered sections (`1` to `10`) plus a cover (`0`). Splitting by section headers preserves tables and rules intact and retrieves relevant sections via keyword scoring (`len >= 4`) without the unnecessary weight of a Vector DB or embedding pipeline. |
| **Conversation Memory** | **`MemorySaver` (In-Memory Checkpointer)** | Preserves multi-turn context across follow-up questions during a CLI session via `thread_id`. State is kept in memory per process run rather than persisted to disk after exit. |
| **User Interface** | **Rich Interactive CLI** (`main.py`) | Focuses 100% on a reliable, well-formatted terminal experience instead of splitting effort across a secondary web frontend. |

---

## 5. Business Rules, Operational Assumptions & Data Quality Edge Cases

During the analysis of `data/*.csv` and `data/políticas_da_loja.pdf`, domain behavior was divided into explicit manual rules, operational assumptions where the manual is silent, and data quality / privacy edge cases.

### 5.1. Business Rules (`políticas_da_loja.pdf`)

1. **Store Scope (Section 1.1)**: Empório da Música sells **musical instruments only**. It does **not** sell isolated accessories (strings, picks, cables, cases/bags, straps, tuners, stands, pedals, amplifiers, or replacement parts).
2. **Non-Cumulative PIX Discount & Promotions (Sections 6.2 & 7.3)**:
   - Upfront payment via **PIX** grants a **5% discount** (`price_brl * 0.95`) **only when the product does not have an active promotion**.
   - If a product has an **active promotion** (`is_active == 1`), promotions are **not cumulative**: the active promotional price prevails and no extra 5% PIX discount is stacked on top.
   - Records with `is_active == 0` in `promotions.csv` represent **inactive promotions** (since the CSV schema has no `start_date`/`end_date` columns to distinguish manually disabled offers from date-expired ones). When a product has an inactive promotion record, the tool surfaces it as inactive/unavailable and applies the current pricing rules (Section 7.3).
   - Upfront payment via **Debit Card or Boleto** uses the list price (or active promotional price) without the 5% PIX discount.
3. **Interest-Free Credit Card Installments (Section 3.1)**:
   - Up to **12 interest-free installments**, subject to minimum installment amounts (evaluated on the rounded 2-decimal installment value in `calculate_installments()`):
     - **1x**: Always allowed.
     - **2x–3x**: Minimum **R$ 50.00** per installment.
     - **4x–6x**: Minimum **R$ 80.00** per installment.
     - **7x–12x**: Minimum **R$ 100.00** per installment.
4. **Post-Sale Deadlines & Conditions (Sections 4.1, 4.2, 4.3, 4.4 & 8.1–8.3)**:
   - **Right of Withdrawal (Section 4.1)**: Up to **7 calendar days from actual product receipt**, applicable **exclusively to online purchases**. Item must be in original packaging without signs of use, with manuals and accessories. Return shipping is covered by the store and refund is processed within 10 business days.
   - **Store Defect Exchange (Section 4.2)**: Up to **30 calendar days from the purchase date (`order_date`)** for manufacturing defects. After 30 days, warranty claims are handled directly with the manufacturer (with store mediation if requested).
   - **Preference Exchange (Section 4.3)**: Up to **7 calendar days from actual product receipt** (operational assumption documented in Section 5.2) for model, color, or size changes, subject to stock availability, perfect condition, original packaging, and **Section 4.4 restrictions**.
   - **Non-Exchangeable Items (Section 4.4)**: Custom-adjusted/setup instruments, explicit "final sale" clearance items, and wind instrument mouthpieces (for hygiene reasons) are not eligible for exchange. **Section 4.4 restrictions apply to exchanges (Section 4.3) and are not automatically applied to block the statutory right of withdrawal (Section 4.1).**
   - **Legal & Contractual Warranty (Sections 8.1–8.3)**: The system implements a **90-calendar-day operational reference** from actual receipt for legal warranty evaluation (Section 8.1), while recognizing that hidden defects (*vícios ocultos*) may require case-specific assessment under applicable Brazilian consumer protection legislation (CDC). In addition, manufacturers provide contractual warranty (6 months to 2 years, Section 8.2). Natural wear (strings, frets, felts, reeds), misuse, unauthorized repairs, and cosmetic damage are excluded (Section 8.3).

---

### 5.2. Operational Assumptions

Where the manual or CSV schema leaves a rule open to interpretation, `src/rules.py` implements and documents explicit operational assumptions:

1. **Split Payment Threshold (`> R$ 2,000.00` — Section 3.1)**:
   - *Ambiguity*: Section 3.1 allows combining two payment methods (e.g., PIX + credit card, or two credit cards) *"for purchases above R$ 2,000.00"*, without specifying whether the threshold is checked before or after promotional discounts.
   - *Operational Assumption*: The condition `effective_price_brl > 2000.00` is evaluated using the **final effective price after active promotional discounts**. For example, Product ID `90` (*Crafter HT-100*) has a list price of `R$ 2,399.00` (`> R$ 2,000.00`), but its active 18% promotion brings the effective price to `R$ 1,967.18` (`<= R$ 2,000.00`), making it ineligible for split payment on its own.
2. **Starting Milestone for Preference Exchange (Section 4.3)**:
   - *Ambiguity*: Section 4.3 states that preference exchanges are allowed *"within 7 days"* without specifying whether the count starts on the purchase date or the delivery date.
   - *Operational Assumption*: Counted from the **actual product receipt date (`actual_delivery_date`)** confirmed by the customer (or purchase date if picked up in-store).
3. **`estimated_delivery` Is Never Treated as Actual Delivery**:
   - `orders.csv` only stores `estimated_delivery` (forecast), not the actual receipt date. Whenever `actual_delivery_date` is required to evaluate withdrawal (7d), preference exchange (7d), or legal warranty (90d) and has not yet been provided by the customer, the evaluators return `indeterminate_missing_delivery_date` and instruct the agent to ask the customer when the order was actually received.
4. **Purchase Channel Confirmation for Right of Withdrawal (Section 4.1)**:
   - Because `orders.csv` has no `purchase_channel` column (`online` vs. `in_store`), `evaluate_right_of_withdrawal` returns `eligible_if_online` when the channel is unknown, prompting the agent to confirm whether the order was placed online.

---

### 5.3. Data Quality, Catalog Inconsistencies & Privacy Edge Cases

| Category | Dataset Finding | Implemented Agent & Tool Behavior |
| :--- | :--- | :--- |
| **Catalog Consistency Audit (`IDs 135–144`)** | Out of products `135` to `144`, **3 items (`138`, `141`, `143`)** have fully consistent brand, description, and JSON `specs`, while **7 items (`135`, `136`, `137`, `139`, `140`, `142`, `144`)** contain contradictions across brand names, wood types, string counts, or key counts. | `validate_product_consistency()` inspects each item individually: `138`, `141`, and `143` are cleared for normal sale, whereas the 7 conflicting items emit `[TECHNICAL_CONFLICT_WARNING]`. Per Section 10, the agent transparently reports the discrepancy, offers to check the physical item with store management, and suggests conflict-free alternatives. |
| **Unavailable & Discontinued Stock** | Active items with `stock_quantity == 0` (e.g., ID `96`), pre-release items (`status='coming_soon'`, ID `130`), and discontinued items that still show positive legacy stock (`status='discontinued'` with `stock_quantity=13`, ID `136`). | Positive stock on a `discontinued` product is **never** treated as authorization to sell. The tool blocks the sale and dynamically queries up to 3 active, in-stock, conflict-free substitutes in the same category ordered by price proximity. |
| **Empty Categories (`IDs 6, 7, 8`)** | Categories 6 (*Sopro Madeiras*), 7 (*Sopro Metais*), and 8 (*Cordas com Arco*) exist in `categories.csv` but have **0 products** registered in `products.csv`. | `search_catalog` detects queries targeting these categories (e.g., violin, cello, saxophone, flute, trumpet) and explicitly reports that no products are registered in the queried category. |
| **Order Ownership Verification & Privacy (LGPD — Section 9)** | `order_id` values (`1–20`) are sequential integers. `customers.csv` contains **shared emails** (two distinct customer profiles sharing the same email address) and **homonyms / similar names** (customers sharing first and last name tokens with different contact details). | `check_order_status` requires `order_id` plus a verification identifier (full name, email, or phone). If an identifier matches multiple customer profiles (`VERIFICATION_AMBIGUOUS`), no order data is disclosed until the customer provides a disambiguating detail (such as phone number or complete full name). |
| **Manual Contact & Tracking Format Divergences** | `políticas_da_loja.pdf` lists `(67) 3341-4444` on the Cover/Section 1.2 and `(67) 3321-4500` in Section 7, plus two email spellings (with and without an accent). Section 5.3 describes tracking codes as `BR` + 9 digits + `BR`, whereas `orders.csv` uses `BR` + 2 letters + 7 digits + `BR` (13 chars, e.g., `BRAB1234567BR`). | Policy search preserves both official phone/WhatsApp numbers and uses the standard ASCII email (`contato@emporiodamusica.com.br`). `check_order_status` returns the exact tracking code stored in `orders.csv` without rejecting alphanumeric prefixes. |

---

## 6. Engineering Refactoring & Live Testing Notes

During code review and live multi-turn execution with Gemini, several real-world integration issues were identified and resolved (documented at the bottom of each source file) while consciously avoiding over-engineering:

### 6.1. Real-World Bugs Discovered & Resolved
- **Serializing Concurrent Tool Queries in SQLite (`src/database.py`)**: LangGraph's `ToolNode` executes parallel tool calls (such as 3 simultaneous `search_catalog` calls) across worker threads in a `ThreadPoolExecutor`. `check_same_thread=False` was enabled in `get_connection()` and all shared connection and query helpers in `src/database.py` were serialized with a reentrant lock (`_DB_LOCK = threading.RLock()`) so concurrent tool calls do not interleave cursors on the shared SQLite connection.
- **Portuguese Singular/Plural Stemming (`-ão` ↔ `-ões`) (`src/database.py`, `src/tools.py`)**: LLMs frequently translate a customer's singular query (`"violão"`) into `category="violão"`, whereas `categories.csv` uses the plural `"Violões"` (`"violoes"`). Bidirectional `-ao`/`-oes` and `-s` stem expansion was added in both SQL category filtering (`fetch_products_with_promotions`) and Python token matching (`search_catalog`).
- **Preserving Unavailable Matches When `only_available=True` (`src/tools.py`)**: When a customer asks if a specific model (`"Violão Giannini GF-3D"`) is in stock, the LLM often passes `only_available=True`. Filtering out `stock_quantity == 0` in SQL before checking the model caused the tool to return 0 results instead of reporting `OUT_OF_STOCK` and suggesting substitutes. `search_catalog` fetches category candidates first and preserves matched unavailable items when a specific query matches only out-of-stock or discontinued products.
- **Prefixed Product IDs in `calculate_payment_options` (`src/tools.py`)**: LLMs often pass `product_query="ID 90"`, `"ID #90"`, or `"#90"` instead of `"90"`. Pattern extraction for optional `ID` / `#` prefixes was added alongside `cleaned.isdigit()` so both plain numeric IDs (`"90"`) and prefixed IDs (`"ID 90"`, `"ID #90"`, `"#90"`) resolve directly by primary key.
- **Composite Customer Verification Strings (`src/database.py`)**: When a customer provides multiple verification details in one turn (e.g., full name + email + phone), the LLM may concatenate them into a single `customer_verification` argument. `find_customers_by_identifier()` extracts and strips email addresses first before parsing phone digits and alphabetic name tokens, verifying all provided criteria (`has_email`, `has_phone`, `has_name`) together and preventing digits inside an email address from matching phone numbers.
- **Injected `reference_date` Propagation & Fixed `UTC-4` Offset (`src/rules.py`, `src/prompts.py`, `src/agent.py`)**: Because LLMs routinely omit optional `reference_date` arguments when calling `check_order_status`, `build_agent(reference_date=...)` registers the active reference date in `src/rules.py` via `set_reference_date()` (defaulting to a fixed `UTC-4` offset via `timezone(timedelta(hours=-4))`, matching Campo Grande/MS local time).
- **Preventing Mid-Loop Premature Replies (`src/prompts.py`, `src/agent.py`)**: An explicit `CRITICAL TOOL-CALLING RULE` was added in the system prompt and `extract_last_ai_reply()` was updated to prefer the final `AIMessage` without `tool_calls`, preventing preliminary *"Wait a moment while I check..."* messages from being returned without the final tool findings.

### 6.2. Conscious Trade-offs to Avoid Over-Engineering
- **Static CSV Reload on Startup (`src/database.py`)**: `init_database()` recreates the SQLite tables from `data/*.csv` on initialization because SQLite serves as an in-memory/local relational query layer over the challenge's static dataset rather than a mutable production write store.
- **No Vector DB / Heavy RAG Pipeline (`src/database.py`)**: With an 8-page manual divided into 10 numbered sections, deterministic section splitting + token scoring (`len >= 4`) is faster, simpler to debug, and requires zero external embedding API calls.
- **Independent Post-Sale Evaluator Functions (`src/rules.py`)**: The four post-sale rules (`evaluate_right_of_withdrawal`, `evaluate_preference_exchange`, `evaluate_defect_exchange`, `evaluate_legal_warranty`) are kept as separate, self-contained functions rather than abstracted into a complex rule-engine DSL.
