# LangChain tools for catalog search, payment calculation, order lookup, and store policy lookup.

from typing import Any
from langchain_core.tools import tool
from src.database import (
    fetch_categories_with_counts,
    fetch_order_with_details,
    fetch_products_with_promotions,
    find_customers_by_identifier,
    get_db_connection,
    normalize_text,
    search_policy_sections,
    set_db_connection,
)
from src.rules import (
    build_payment_summary,
    evaluate_defect_exchange,
    evaluate_legal_warranty,
    evaluate_preference_exchange,
    evaluate_right_of_withdrawal,
    validate_product_consistency,
)


# Attach payment summary and consistency check to a raw product record once.
def _enrich_product(product: dict[str, Any]) -> dict[str, Any]:
    active_promo = product.get("active_promotion")
    discount = float(active_promo["discount_percent"]) if active_promo else 0.0
    payment = build_payment_summary(float(product["price_brl"]), discount)
    consistency = validate_product_consistency(
        product["name"], product.get("description"), product.get("specs")
    )
    return {
        **product,
        "payment": payment,
        "consistency": consistency,
    }


# Find up to 3 active, in-stock, conflict-free substitute products in the same category.
def _find_dynamic_substitutes(
    category_id: int,
    excluded_product_id: int,
    target_price: float,
    limit: int = 3,
) -> list[dict[str, Any]]:
    candidates = [
        _enrich_product(p)
        for p in fetch_products_with_promotions(
            category_id=category_id, only_available=True
        )
        if int(p["product_id"]) != excluded_product_id
    ]
    valid_candidates = [
        c for c in candidates if not c["consistency"]["has_conflict"]
    ]
    valid_candidates.sort(
        key=lambda c: abs(c["payment"]["effective_price_brl"] - float(target_price))
    )

    return [
        {
            "product_id": int(c["product_id"]),
            "name": c["name"],
            "effective_price_brl": c["payment"]["effective_price_brl"],
            "pix_price_brl": c["payment"]["pix_price_brl"],
            "stock_quantity": int(c["stock_quantity"]),
        }
        for c in valid_candidates[:limit]
    ]


# Format an already enriched product dictionary into a readable catalog entry.
def _format_product_entry(item: dict[str, Any]) -> str:
    pid = int(item["product_id"])
    name = item["name"]
    cat_id = int(item["category_id"])
    cat_name = item["category_name"]
    status = item["status"].strip().lower()
    stock = int(item["stock_quantity"])
    description = item.get("description") or ""
    specs = item.get("specs") or "{}"

    active_promo = item.get("active_promotion")
    expired_promos = item.get("expired_promotions", [])
    pay = item["payment"]
    consistency = item["consistency"]

    if status == "discontinued":
        avail_label = "DISCONTINUED (No longer part of the active catalog; cannot be sold even if legacy stock count is > 0)"
        needs_substitutes = True
    elif status == "coming_soon":
        avail_label = "COMING_SOON (Pre-release item; not yet available for immediate purchase/delivery)"
        needs_substitutes = True
    elif stock <= 0:
        avail_label = "OUT_OF_STOCK (Temporarily unavailable; 0 units in stock)"
        needs_substitutes = True
    else:
        avail_label = f"AVAILABLE ({stock} units in stock)"
        needs_substitutes = False

    lines = [
        f"[ID {pid}] {name} | Category: {cat_name} | Status: {avail_label}",
    ]

    if pay["has_active_promotion"] and active_promo:
        lines.append(
            f"  Pricing: Original R$ {pay['original_price_brl']:.2f} | "
            f"Active Promo ({active_promo['description']} - {pay['discount_percent']:.0f}% OFF): "
            f"R$ {pay['effective_price_brl']:.2f} | PIX: R$ {pay['pix_price_brl']:.2f} "
            f"(5% PIX discount not cumulative with active promo) | "
            f"Up to {pay['max_installments']}x of R$ {pay['max_installment_brl']:.2f} interest-free"
        )
    else:
        lines.append(
            f"  Pricing: List Price R$ {pay['original_price_brl']:.2f} | "
            f"PIX (5% discount): R$ {pay['pix_price_brl']:.2f} | "
            f"Up to {pay['max_installments']}x of R$ {pay['max_installment_brl']:.2f} interest-free"
        )

    if expired_promos:
        exp_desc = ", ".join(
            f"{p['description']} ({float(p['discount_percent']):.0f}% OFF)"
            for p in expired_promos
        )
        lines.append(f"  Expired Promotions (Inactive): {exp_desc}")

    if consistency["has_conflict"]:
        issues = "; ".join(consistency["conflicts"])
        lines.append(
            f"  [TECHNICAL_CONFLICT_WARNING]: {issues} -> {consistency['guidance']}"
        )
    else:
        lines.append(f"  Description: {description}")
        lines.append(f"  Specs: {specs}")

    if needs_substitutes:
        subs = _find_dynamic_substitutes(cat_id, pid, pay["effective_price_brl"])
        if subs:
            sub_str = "; ".join(
                f"[ID {s['product_id']}] {s['name']} (R$ {s['effective_price_brl']:.2f}, "
                f"PIX R$ {s['pix_price_brl']:.2f}, Stock: {s['stock_quantity']})"
                for s in subs
            )
            lines.append(f"  Available Substitutes in Category: {sub_str}")

    return "\n".join(lines)


# Search the store catalog by keyword, category, price range, and availability.
@tool(
    "search_catalog",
    description=(
        "Search the musical instrument catalog by keyword/model/brand (query), "
        "category name (category), minimum effective price (min_price), maximum "
        "effective price (max_price), or filter only available in-stock items "
        "(only_available=True). Returns product availability, stock, promotional "
        "prices, PIX prices, installment limits, technical conflict alerts, and "
        "dynamic substitutes for unavailable/discontinued items."
    ),
)
def search_catalog(
    query: str | None = None,
    category: str | None = None,
    max_price: float | None = None,
    min_price: float | None = None,
    only_available: bool = False,
) -> str:
    norm_q = normalize_text(query)
    norm_cat = normalize_text(category)

    # Check if the query or category matches a registered category that has 0 products in products.csv.
    for cat_row in fetch_categories_with_counts():
        if int(cat_row["prod_count"]) > 0:
            continue
        cat_name_norm = normalize_text(cat_row["name"])
        cat_desc_norm = normalize_text(cat_row["description"])
        if (norm_cat and (norm_cat in cat_name_norm or norm_cat in cat_desc_norm)) or (
            norm_q
            and any(
                tok in cat_name_norm or tok in cat_desc_norm
                for tok in norm_q.split()
                if len(tok) >= 4
            )
        ):
            return (
                f"No products available: There are currently 0 instruments available "
                f"in the store catalog for the category '{cat_row['name']}' "
                f"({cat_row['description']}). Do not claim that the store has these "
                f"items in stock."
            )

    raw_products = fetch_products_with_promotions(
        category_query=category, only_available=only_available
    )

    if norm_q:
        stop_words = {"de", "da", "do", "para", "com", "sem", "ate", "em", "um", "uma", "os", "as"}
        tokens = [t for t in norm_q.split() if len(t) >= 2 and t not in stop_words]
        matched_products: list[dict[str, Any]] = []

        for p in raw_products:
            searchable = normalize_text(
                f"{p['product_id']} {p['name']} {p['description']} {p['specs']} {p['category_name']}"
            )
            token_matches = True
            for tok in tokens:
                stem = tok[:-3] + "ao" if tok.endswith("oes") else tok.rstrip("s")
                if tok not in searchable and (len(stem) < 2 or stem not in searchable):
                    token_matches = False
                    break
            if token_matches:
                matched_products.append(p)
        raw_products = matched_products

    # Enrich each matched product once and apply effective price filters.
    final_items: list[dict[str, Any]] = []
    for p in raw_products:
        enriched = _enrich_product(p)
        effective_price = enriched["payment"]["effective_price_brl"]

        if min_price is not None and effective_price < float(min_price) - 1e-9:
            continue
        if max_price is not None and effective_price > float(max_price) + 1e-9:
            continue
        final_items.append(enriched)

    if not final_items:
        return (
            "No matching instruments found in the catalog for the given filters. "
            "Remember that Empório da Música works exclusively with musical instruments "
            "(no accessories such as strings, picks, cables, cases, pedals, or amplifiers)."
        )

    formatted = [_format_product_entry(item) for item in final_items]
    return f"Found {len(formatted)} product(s):\n\n" + "\n\n".join(formatted)


# Calculate detailed payment, PIX, promotion, and installment options for a product.
@tool(
    "calculate_payment_options",
    description=(
        "Calculate detailed pricing, active/expired promotions, PIX discount, "
        "all valid interest-free installment options (1x to 12x), and split payment "
        "eligibility (> R$ 2,000.00) for a specific product ID or product name. "
        "If the query matches multiple products, returns a list asking the customer "
        "to clarify which model they want."
    ),
)
def calculate_payment_options(product_query: str) -> str:
    cleaned = (product_query or "").strip()
    if not cleaned:
        return "Please provide a product name or product ID to calculate payment options."

    if cleaned.isdigit():
        matches = fetch_products_with_promotions(product_id=int(cleaned))
    else:
        norm_q = normalize_text(cleaned)
        all_products = fetch_products_with_promotions()
        exact = [p for p in all_products if normalize_text(p["name"]) == norm_q]
        if len(exact) == 1:
            matches = exact
        else:
            tokens = [t for t in norm_q.split() if len(t) >= 2]
            matches = [
                p
                for p in all_products
                if all(t in normalize_text(p["name"]) for t in tokens)
            ]

    if not matches:
        return f"No product found matching '{product_query}'."

    if len(matches) > 1:
        options_list = "\n".join(
            f"  - [ID {p['product_id']}] {p['name']} (List price: R$ {float(p['price_brl']):.2f}, Status: {p['status']})"
            for p in matches
        )
        return (
            f"AMBIGUOUS_PRODUCT_QUERY: Found {len(matches)} products matching '{product_query}'. "
            f"Ask the customer to specify which exact model they want:\n{options_list}"
        )

    item = _enrich_product(matches[0])
    pid = int(item["product_id"])
    name = item["name"]
    status = item["status"].strip().lower()
    stock = int(item["stock_quantity"])
    active_promo = item.get("active_promotion")
    expired_promos = item.get("expired_promotions", [])
    summary = item["payment"]
    consistency = item["consistency"]

    lines = [
        f"Payment Breakdown for [ID {pid}] {name}",
        f"Catalog Status: {status.upper()} | Stock: {stock} unit(s)",
        f"Original List Price: R$ {summary['original_price_brl']:.2f}",
    ]

    if summary["has_active_promotion"] and active_promo:
        lines.append(
            f"Active Promotion: YES ({active_promo['description']} - "
            f"{summary['discount_percent']:.0f}% OFF) -> Promotional Price: "
            f"R$ {summary['effective_price_brl']:.2f}"
        )
        lines.append(
            f"PIX / Debit / Boleto (Upfront): R$ {summary['pix_price_brl']:.2f} "
            f"(Per Section 6.2, promotions are not cumulative; the 5% PIX discount "
            f"does not apply on top of an already promotional price)."
        )
    else:
        lines.append("Active Promotion: NONE")
        lines.append(
            f"PIX (Upfront with 5% discount): R$ {summary['pix_price_brl']:.2f}"
        )
        lines.append(
            f"Debit Card / Boleto (Upfront list price): R$ {summary['effective_price_brl']:.2f}"
        )

    if expired_promos:
        exp_str = ", ".join(
            f"{p['description']} ({float(p['discount_percent']):.0f}% OFF)"
            for p in expired_promos
        )
        lines.append(
            f"Expired Promotions on Record (Inactive): {exp_str}. "
            f"Per Section 7.3, inform the customer transparently that this promotion "
            f"has expired and present the current price."
        )

    inst_lines = [
        f"  - {opt['installments']}x of R$ {opt['installment_brl']:.2f} interest-free (Total: R$ {opt['total_brl']:.2f})"
        for opt in summary["installment_options"]
    ]
    lines.append("Valid Credit Card Installment Options (Section 3.1):")
    lines.extend(inst_lines)

    split = summary["split_payment"]
    lines.append(f"Split Payment (Section 3.1): {split['rule_note']}")

    if consistency["has_conflict"]:
        lines.append(
            f"[TECHNICAL_CONFLICT_WARNING]: {'; '.join(consistency['conflicts'])} -> {consistency['guidance']}"
        )

    if status != "active" or stock <= 0:
        subs = _find_dynamic_substitutes(
            int(item["category_id"]), pid, summary["effective_price_brl"]
        )
        if subs:
            sub_str = "; ".join(
                f"[ID {s['product_id']}] {s['name']} (R$ {s['effective_price_brl']:.2f})"
                for s in subs
            )
            lines.append(
                f"Note: This item is not currently available for sale ({status}, stock={stock}). "
                f"Suggested available substitutes: {sub_str}"
            )

    return "\n".join(lines)


# Retrieve relevant sections from the store policy manual PDF.
@tool(
    "consult_store_policies",
    description=(
        "Look up official store policies from políticas_da_loja.pdf by topic "
        "(e.g., 'horario funcionamento', 'endereco telefone whatsapp email', "
        "'formas de pagamento parcelamento pix', 'troca devolucao arrependimento "
        "defeito boquilha', 'frete entrega rastreamento', 'promocoes', 'garantia', "
        "'lgpd privacidade'). Always use this tool before answering questions "
        "about store rules, hours, contact info, shipping, returns, or warranty."
    ),
)
def consult_store_policies(topic: str) -> str:
    return search_policy_sections(topic)


# Verify if customer_verification uniquely matches the titular customer of an order.
def _verify_order_owner(
    order: dict[str, Any], customer_verification: str
) -> tuple[str, str | None]:
    verif_clean = (customer_verification or "").strip()
    if not verif_clean:
        return "missing", None

    matched_customers = find_customers_by_identifier(verif_clean)
    order_cid = int(order["customer_id"])

    # Check if the order owner is among the matched customer records.
    if not any(int(c["customer_id"]) == order_cid for c in matched_customers):
        return "mismatch", None

    # If the verification string matches multiple customer profiles (e.g. shared email or homonym),
    # allow it only if the user provided the exact full name of the order owner and the other profile has a different full name.
    if len(matched_customers) > 1:
        norm_verif = normalize_text(verif_clean)
        owner_name_norm = normalize_text(order["customer_name"])
        exact_full_name_matches = [
            c for c in matched_customers if normalize_text(c["name"]) == norm_verif
        ]
        if not (len(exact_full_name_matches) == 1 and owner_name_norm == norm_verif):
            return (
                "ambiguous",
                (
                    f"VERIFICATION_AMBIGUOUS: The identifier '{verif_clean}' matches multiple "
                    f"customer profiles in our records (homonym or shared email). Under LGPD "
                    f"privacy rules (Section 9), ask the customer to provide their registered "
                    f"phone number or full complete name to confirm ownership of Order #{order['order_id']}."
                ),
            )

    return "verified", None


# Check order status, tracking, and post-sale eligibility with two-factor LGPD verification.
@tool(
    "check_order_status",
    description=(
        "Check order status, items, tracking code, and post-sale return/exchange/warranty "
        "eligibility. Under LGPD privacy rules (Section 9), BOTH 'order_id' AND "
        "'customer_verification' (customer's registered full name, email, or phone number) "
        "are required. Optionally pass 'actual_delivery_date' ('YYYY-MM-DD' confirmed by "
        "the customer) and 'purchase_channel' ('online' or 'in_store') when evaluating "
        "withdrawal, preference exchange, or legal warranty deadlines."
    ),
)
def check_order_status(
    order_id: int | None = None,
    customer_verification: str | None = None,
    actual_delivery_date: str | None = None,
    purchase_channel: str | None = None,
    reference_date: str | None = None,
) -> str:
    verif_clean = (customer_verification or "").strip()

    if order_id is None and not verif_clean:
        return (
            "VERIFICATION_REQUIRED: Under LGPD privacy rules (Section 9), both the "
            "Order ID (order_id) and a customer verification detail (registered full "
            "name, email, or phone number) are required before looking up an order."
        )

    if order_id is None and verif_clean:
        matched_customers = find_customers_by_identifier(verif_clean)
        if len(matched_customers) > 1:
            return (
                f"VERIFICATION_REQUIRED (MULTIPLE_PROFILES): Multiple customer profiles "
                f"match '{verif_clean}'. Under LGPD privacy rules (Section 9), do not "
                f"disclose any personal or order information. Ask the customer for their "
                f"Order ID (order_id) along with their registered phone number or full name."
            )
        return (
            "VERIFICATION_REQUIRED: An Order ID (order_id) is required alongside "
            "customer verification to access order details. Please ask the customer "
            "for their Order ID."
        )

    if not verif_clean:
        return (
            f"VERIFICATION_REQUIRED: Order ID #{order_id} alone is not sufficient to "
            f"verify identity under LGPD rules (Section 9). Ask the customer to also "
            f"confirm their registered full name, email address, or phone number."
        )

    order = fetch_order_with_details(int(order_id))
    if order is None:
        return f"No order found with Order ID #{order_id}."

    verif_status, verif_msg = _verify_order_owner(order, verif_clean)
    if verif_status == "ambiguous" and verif_msg:
        return verif_msg

    if verif_status != "verified":
        return (
            f"VERIFICATION_FAILED: The provided verification detail ('{verif_clean}') "
            f"does not match the titular customer record for Order #{order_id}. "
            f"Under LGPD privacy rules (Section 9), do not disclose any information "
            f"about this order. Ask the customer to verify their Order ID and registered "
            f"name, email, or phone number."
        )

    first_name = (order.get("customer_name") or "Customer").split()[0]
    status = order["status"].strip().lower()
    order_date = order["order_date"]
    total_brl = float(order["total_brl"])
    payment_method = order["payment_method"]
    tracking = order.get("tracking_code") or "Not yet assigned"
    est_delivery = order.get("estimated_delivery") or "Not scheduled"
    notes = order.get("notes") or ""

    item_lines = [
        f"  - {it['quantity']}x [ID {it['product_id']}] {it['product_name']} "
        f"(Category: {it['category_name']}, Unit List Price: R$ {float(it['price_brl']):.2f})"
        for it in order.get("items", [])
    ]

    lines = [
        f"Order Summary — Order #{order['order_id']} (Verified Customer First Name: {first_name})",
        f"Status: {status.upper()} | Order Date: {order_date}",
        f"Total Paid: R$ {total_brl:.2f} | Payment Method: {payment_method}",
        f"Tracking Code: {tracking}",
        f"Estimated Delivery Date: {est_delivery} (Note: Forecast only; not confirmation of actual receipt)",
    ]
    if notes:
        lines.append(f"Order Notes / Cancellation Reason: {notes}")

    lines.append("Purchased Items:")
    lines.extend(item_lines)

    # Evaluate all 4 independent post-sale rules.
    withdrawal = evaluate_right_of_withdrawal(
        status, actual_delivery_date, reference_date, purchase_channel
    )
    preference = evaluate_preference_exchange(
        status, actual_delivery_date, reference_date
    )
    defect = evaluate_defect_exchange(status, order_date, reference_date)
    warranty = evaluate_legal_warranty(
        status, actual_delivery_date, reference_date
    )

    lines.extend(
        [
            "",
            "Post-Sale Eligibility Assessment:",
            f"  1. Right of Withdrawal (Section 4.1 - 7d from receipt, online only): [{withdrawal['status'].upper()}] {withdrawal['note']}",
            f"  2. Preference Exchange (Section 4.3 - 7d from receipt assumption): [{preference['status'].upper()}] {preference['note']}",
            f"  3. Store Defect Exchange (Section 4.2 - 30d from order_date): [{defect['status'].upper()}] {defect['note']}",
            f"  4. Legal Warranty (Section 8.1 - 90d from receipt): [{warranty['status'].upper()}] {warranty['note']}",
        ]
    )

    return "\n".join(lines)


ALL_TOOLS = [
    search_catalog,
    calculate_payment_options,
    check_order_status,
    consult_store_policies,
]
