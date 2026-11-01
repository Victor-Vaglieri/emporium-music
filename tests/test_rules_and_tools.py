# Automated test suite for database ingestion, business rules, and LangChain tools.

import pytest
from src.database import (
    fetch_categories_with_counts,
    fetch_order_with_details,
    fetch_products_with_promotions,
    find_customers_by_identifier,
    init_database,
    load_policy_sections,
    normalize_text,
    search_policy_sections,
    set_db_connection,
)
from src.rules import (
    build_payment_summary,
    calculate_installments,
    calculate_pricing,
    check_split_payment_eligibility,
    evaluate_defect_exchange,
    evaluate_legal_warranty,
    evaluate_preference_exchange,
    evaluate_right_of_withdrawal,
    get_policy_rules,
    validate_product_consistency,
)
from src.tools import (
    calculate_payment_options,
    check_order_status,
    consult_store_policies,
    search_catalog,
)


# Provide a fresh in-memory SQLite database for each test function.
@pytest.fixture(autouse=True)
def in_memory_db():
    conn = init_database(":memory:")
    set_db_connection(conn)
    yield conn
    set_db_connection(None)
    conn.close()


# Verify accent removal, lowercase conversion, and whitespace collapsing.
def test_normalize_text():
    assert normalize_text("  Violão Clássico   Empório da Música! ") == "violao classico emporio da musica!"
    assert normalize_text(None) == ""


# Verify that all 6 CSV files load the expected row counts into SQLite.
def test_database_table_counts(in_memory_db):
    expected = {
        "categories": 9,
        "customers": 50,
        "orders": 20,
        "order_items": 22,
        "products": 65,
        "promotions": 25,
    }
    for table, count in expected.items():
        actual = in_memory_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert actual == count


# Verify that the policy PDF splits into cover (0) plus 10 numbered sections and preserves contact divergences.
def test_pdf_policy_extraction_and_contact_divergences():
    sections = load_policy_sections()
    assert list(sections.keys()) == [str(i) for i in range(11)]

    contact_text = search_policy_sections("telefone whatsapp email")
    assert "(67) 3341-4444" in contact_text
    assert "(67) 3321-4500" in contact_text
    assert "contato@emporiodamusica.com.br" in contact_text
    assert "contato@empóriodamusica.com.br" in contact_text


# Verify that commercial parameters and post-sale deadlines are parsed from the policy PDF.
def test_policy_rules_parsed_from_pdf():
    rules = get_policy_rules()
    assert rules["pix_discount_rate"] == 0.05
    assert rules["min_installment_2_to_3x_brl"] == 50.0
    assert rules["min_installment_4_to_6x_brl"] == 80.0
    assert rules["min_installment_7_to_12x_brl"] == 100.0
    assert rules["split_payment_threshold_brl"] == 2000.0
    assert rules["withdrawal_days"] == 7
    assert rules["refund_business_days"] == 10
    assert rules["defect_exchange_days"] == 30
    assert rules["preference_exchange_days"] == 7
    assert rules["legal_warranty_days"] == 90


# Verify non-cumulative PIX discount rule (Section 6.2).
def test_pricing_and_non_cumulative_pix():
    # Without active promotion: 5% PIX discount applies.
    no_promo = calculate_pricing(2199.00, 0.0)
    assert no_promo["effective_price_brl"] == 2199.00
    assert no_promo["pix_price_brl"] == 2089.05
    assert no_promo["pix_discount_applied"] is True

    # With active promotion (8% on Taylor 110e): PIX 5% does NOT stack on top.
    with_promo = calculate_pricing(5999.00, 8.0)
    assert with_promo["effective_price_brl"] == 5519.08
    assert with_promo["pix_price_brl"] == 5519.08
    assert with_promo["pix_discount_applied"] is False


# Verify minimum installment thresholds across 1x-3x (>=50), 4x-6x (>=80), and 7x-12x (>=100).
def test_installment_minimum_thresholds():
    # R$ 159.90 (Shelby SU-21S): 3x is 53.30 (>=50), 4x would be 39.98 (<80).
    opts_159 = calculate_installments(159.90)
    assert [o["installments"] for o in opts_159] == [1, 2, 3]
    assert opts_159[-1]["installment_brl"] == 53.30

    # R$ 549.00 (Ohana CK-20): 6x is 91.50 (>=80), 7x would be 78.43 (<100).
    opts_549 = calculate_installments(549.00)
    assert [o["installments"] for o in opts_549] == [1, 2, 3, 4, 5, 6]

    # R$ 1200.00: 12x is 100.00 (>=100).
    opts_1200 = calculate_installments(1200.00)
    assert len(opts_1200) == 12


# Verify split payment threshold (> R$ 2,000.00) and Crafter HT-100 discount boundary case.
def test_split_payment_threshold_and_boundary():
    # Exactly R$ 2,000.00 is not > 2000.00.
    exact_2000 = check_split_payment_eligibility(2000.00)
    assert exact_2000["eligible"] is False

    # Product ID 90 (Crafter HT-100): R$ 2,399.00 list price, 18% active promo -> R$ 1,967.18.
    crafter_summary = build_payment_summary(2399.00, 18.0)
    assert crafter_summary["effective_price_brl"] == 1967.18
    assert crafter_summary["split_payment"]["eligible"] is False
    assert crafter_summary["split_payment"]["crossed_threshold_after_discount"] is True


# Verify Section 4.1 right of withdrawal rules (online channel, actual delivery date required, 7-day boundary).
def test_right_of_withdrawal_rules():
    # Missing actual_delivery_date must return indeterminate (never use estimated_delivery).
    missing_dt = evaluate_right_of_withdrawal("delivered", None, "2026-03-01", "online")
    assert missing_dt["status"] == "indeterminate_missing_delivery_date"
    assert missing_dt["eligible"] is None

    # In-store purchase does not qualify under Section 4.1.
    in_store = evaluate_right_of_withdrawal("delivered", "2026-02-20", "2026-02-22", "in_store")
    assert in_store["status"] == "not_eligible_in_store"
    assert in_store["eligible"] is False

    # Within 7 days, channel not yet confirmed -> eligible_if_online.
    unconfirmed_channel = evaluate_right_of_withdrawal("delivered", "2026-02-20", "2026-02-27", None)
    assert unconfirmed_channel["status"] == "eligible_if_online"
    assert unconfirmed_channel["channel_confirmation_required"] is True

    # Day 7 online -> eligible; Day 8 online -> expired.
    day_7 = evaluate_right_of_withdrawal("delivered", "2026-02-20", "2026-02-27", "online")
    assert day_7["status"] == "eligible"
    assert day_7["eligible"] is True

    day_8 = evaluate_right_of_withdrawal("delivered", "2026-02-20", "2026-02-28", "online")
    assert day_8["status"] == "expired"
    assert day_8["eligible"] is False


# Verify Section 4.3 preference exchange and Section 4.4 restrictions.
def test_preference_exchange_and_section_4_4_restrictions():
    restricted = evaluate_preference_exchange("delivered", "2026-02-20", "2026-02-22", is_ineligible_item=True)
    assert restricted["status"] == "not_eligible_restricted_item"
    assert restricted["eligible"] is False

    valid_7d = evaluate_preference_exchange("delivered", "2026-02-20", "2026-02-27", is_ineligible_item=False)
    assert valid_7d["status"] == "eligible"
    assert valid_7d["eligible"] is True
    assert "Operational assumption" in valid_7d["milestone_assumption"]


# Verify Section 4.2 defect exchange (30d from order_date) and Section 8.1 legal warranty (90d from receipt).
def test_defect_exchange_and_legal_warranty_boundaries():
    # Defect exchange: Day 30 vs Day 31 from order_date.
    defect_30 = evaluate_defect_exchange("delivered", "2026-01-01", "2026-01-31")
    assert defect_30["status"] == "eligible_store_exchange"
    assert defect_30["eligible"] is True

    defect_31 = evaluate_defect_exchange("delivered", "2026-01-01", "2026-02-01")
    assert defect_31["status"] == "manufacturer_warranty_only"
    assert defect_31["eligible"] is False

    # Legal warranty: Day 90 vs Day 91 from actual receipt date.
    warranty_90 = evaluate_legal_warranty("delivered", "2026-01-01", "2026-04-01")
    assert warranty_90["status"] == "within_legal_warranty"
    assert warranty_90["eligible"] is True

    warranty_91 = evaluate_legal_warranty("delivered", "2026-01-01", "2026-04-02")
    assert warranty_91["status"] == "legal_warranty_expired"
    assert warranty_91["eligible"] is False


# Verify individual catalog consistency across IDs 135-144 (138, 141, 143 coherent; 7 flagged).
def test_catalog_consistency_individual_products():
    products = {int(p["product_id"]): p for p in fetch_products_with_promotions()}

    coherent_ids = [138, 141, 143]
    for pid in coherent_ids:
        p = products[pid]
        res = validate_product_consistency(p["name"], p["description"], p["specs"])
        assert res["has_conflict"] is False, f"Product {pid} should be coherent"

    conflicting_ids = [135, 136, 137, 139, 140, 142, 144]
    for pid in conflicting_ids:
        p = products[pid]
        res = validate_product_consistency(p["name"], p["description"], p["specs"])
        assert res["has_conflict"] is True, f"Product {pid} should be flagged"


# Verify catalog search for out-of-stock (ID 96), discontinued with stock (ID 136), and empty categories (6, 7, 8).
def test_search_catalog_edge_cases():
    out_of_stock = search_catalog.invoke({"query": "GF-3D"})
    assert "OUT_OF_STOCK" in out_of_stock
    assert "Available Substitutes in Category:" in out_of_stock

    discontinued = search_catalog.invoke({"query": "Ibanez Bass 2X"})
    assert "DISCONTINUED" in discontinued
    assert "[ID 138] Music Man Bass 4X Electric Bass" in discontinued
    assert "[ID 135]" not in discontinued

    empty_cat = search_catalog.invoke({"query": "violino"})
    assert "0 instruments available" in empty_cat


# Verify ambiguous product payment lookup vs exact product lookup.
def test_calculate_payment_options_ambiguous_and_exact():
    ambiguous = calculate_payment_options.invoke({"product_query": "Yamaha"})
    assert "AMBIGUOUS_PRODUCT_QUERY" in ambiguous

    exact = calculate_payment_options.invoke({"product_query": "Takamine GD20"})
    assert "Payment Breakdown for [ID 95]" in exact
    assert "PIX (Upfront with 5% discount): R$ 2089.05" in exact
    assert "Promoção de Páscoa (12% OFF)" in exact or "Oferta Especial (12% OFF)" in exact


# Verify two-factor LGPD privacy rules, shared email (Bruno IDs 11/43), and homonym (Diego IDs 13/47) in check_order_status.
def test_check_order_status_lgpd_and_homonyms():
    # Single factor (only order_id) must be blocked.
    only_id = check_order_status.invoke({"order_id": 1})
    assert "VERIFICATION_REQUIRED" in only_id

    # Shared email between Bruno Carvalho Martins (ID 11) and Bruno Martins (ID 43) on Order #10.
    shared_email = check_order_status.invoke(
        {"order_id": 10, "customer_verification": "bruno.martins@jmail.com"}
    )
    assert "VERIFICATION_AMBIGUOUS" in shared_email

    # Disambiguated Bruno with phone number or full name succeeds.
    bruno_ok = check_order_status.invoke(
        {"order_id": 10, "customer_verification": "(67) 98543-2109"}
    )
    assert "Order Summary — Order #10" in bruno_ok
    assert "bruno.martins@jmail.com" not in bruno_ok

    # Partial homonym 'Diego Castro' (matches ID 13 and ID 47) on Order #14.
    diego_ambig = check_order_status.invoke(
        {"order_id": 14, "customer_verification": "Diego Castro"}
    )
    assert "VERIFICATION_AMBIGUOUS" in diego_ambig

    # Diego with unique email (diego.castro@coldmail.com) succeeds.
    diego_ok = check_order_status.invoke(
        {"order_id": 14, "customer_verification": "diego.castro@coldmail.com"}
    )
    assert "Order Summary — Order #14" in diego_ok
