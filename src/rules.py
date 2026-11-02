# Deterministic business rules for pricing, installments, post-sale deadlines, and catalog validation.

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from src.database import load_policy_sections, normalize_text

logger = logging.getLogger(__name__)

STORE_TIMEZONE = timezone(timedelta(hours=-4))
_ACTIVE_REFERENCE_DATE: date | None = None

# Business-rule fallback defaults used if PDF extraction fails or a section pattern changes.
DEFAULT_PIX_DISCOUNT_RATE = 0.05
DEFAULT_MIN_INSTALLMENT_2_TO_3X_BRL = 50.00
DEFAULT_MIN_INSTALLMENT_4_TO_6X_BRL = 80.00
DEFAULT_MIN_INSTALLMENT_7_TO_12X_BRL = 100.00
DEFAULT_SPLIT_PAYMENT_THRESHOLD_BRL = 2000.00

DEFAULT_WITHDRAWAL_DAYS = 7
DEFAULT_REFUND_BUSINESS_DAYS = 10
DEFAULT_DEFECT_EXCHANGE_DAYS = 30
DEFAULT_PREFERENCE_EXCHANGE_DAYS = 7
DEFAULT_LEGAL_WARRANTY_DAYS = 90

# Accepted purchase channel aliases in English (from tool schema) and Portuguese (from customer phrasing).
IN_STORE_CHANNELS: tuple[str, ...] = ("in_store", "presencial", "physical", "loja")
ONLINE_CHANNELS: tuple[str, ...] = ("online", "web", "site", "whatsapp")


# Convert a Brazilian currency number string (e.g., '2.000,00' or '50,00') to float.
def _parse_brl_number(raw_value: str, fallback: float) -> float:
    cleaned = raw_value.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return fallback


# Validate that a monetary price is non-negative and an optional discount percentage is between 0 and 100.
def _validate_financial_inputs(
    amount_brl: float, discount_percent: float | None = None
) -> None:
    if float(amount_brl) < 0.0:
        raise ValueError(f"Monetary amount cannot be negative: {amount_brl}")
    if discount_percent is not None:
        disc = float(discount_percent)
        if disc < 0.0 or disc > 100.0:
            raise ValueError(
                f"Discount percentage must be between 0 and 100, got: {discount_percent}"
            )


# Extract commercial and post-sale rule parameters from the store policy PDF with safe fallbacks.
@lru_cache(maxsize=1)
def get_policy_rules() -> dict[str, Any]:
    rules: dict[str, Any] = {
        "pix_discount_rate": DEFAULT_PIX_DISCOUNT_RATE,
        "min_installment_2_to_3x_brl": DEFAULT_MIN_INSTALLMENT_2_TO_3X_BRL,
        "min_installment_4_to_6x_brl": DEFAULT_MIN_INSTALLMENT_4_TO_6X_BRL,
        "min_installment_7_to_12x_brl": DEFAULT_MIN_INSTALLMENT_7_TO_12X_BRL,
        "split_payment_threshold_brl": DEFAULT_SPLIT_PAYMENT_THRESHOLD_BRL,
        "withdrawal_days": DEFAULT_WITHDRAWAL_DAYS,
        "refund_business_days": DEFAULT_REFUND_BUSINESS_DAYS,
        "defect_exchange_days": DEFAULT_DEFECT_EXCHANGE_DAYS,
        "preference_exchange_days": DEFAULT_PREFERENCE_EXCHANGE_DAYS,
        "legal_warranty_days": DEFAULT_LEGAL_WARRANTY_DAYS,
    }

    try:
        sections = load_policy_sections()
    except Exception as exc:
        logger.warning(
            "Failed to load store policy PDF; falling back to default business rules: %s",
            exc,
        )
        return rules

    sec3 = normalize_text(sections.get("3", ""))
    if sec3:
        pix_match = re.search(r"pix.*?(\d+(?:[.,]\d+)?)%\s*de\s*desconto", sec3)
        if pix_match:
            rules["pix_discount_rate"] = (
                _parse_brl_number(
                    pix_match.group(1), DEFAULT_PIX_DISCOUNT_RATE * 100.0
                )
                / 100.0
            )

        m_3x = re.search(r"ate\s*3x:.*?r\$\s*([\d.,]+)", sec3)
        if m_3x:
            rules["min_installment_2_to_3x_brl"] = _parse_brl_number(
                m_3x.group(1), DEFAULT_MIN_INSTALLMENT_2_TO_3X_BRL
            )

        m_6x = re.search(r"4x\s*a\s*6x:.*?r\$\s*([\d.,]+)", sec3)
        if m_6x:
            rules["min_installment_4_to_6x_brl"] = _parse_brl_number(
                m_6x.group(1), DEFAULT_MIN_INSTALLMENT_4_TO_6X_BRL
            )

        m_12x = re.search(r"7x\s*a\s*12x:.*?r\$\s*([\d.,]+)", sec3)
        if m_12x:
            rules["min_installment_7_to_12x_brl"] = _parse_brl_number(
                m_12x.group(1), DEFAULT_MIN_INSTALLMENT_7_TO_12X_BRL
            )

        m_split = re.search(r"acima\s*de\s*r\$\s*([\d.,]+)", sec3)
        if m_split:
            rules["split_payment_threshold_brl"] = _parse_brl_number(
                m_split.group(1), DEFAULT_SPLIT_PAYMENT_THRESHOLD_BRL
            )

    sec4 = normalize_text(sections.get("4", ""))
    if sec4:
        m_withdraw = re.search(
            r"4\.1\s*direito\s*de\s*arrependimento.*?em\s*ate\s*(\d+)", sec4
        )
        if m_withdraw:
            rules["withdrawal_days"] = int(m_withdraw.group(1))

        m_refund = re.search(r"reembolso.*?em\s*ate\s*(\d+)\s*dias\s*uteis", sec4)
        if m_refund:
            rules["refund_business_days"] = int(m_refund.group(1))

        m_defect = re.search(r"4\.2\s*trocas\s*por\s*defeito.*?em\s*ate\s*(\d+)", sec4)
        if m_defect:
            rules["defect_exchange_days"] = int(m_defect.group(1))

        m_pref = re.search(
            r"4\.3\s*trocas\s*por\s*preferencia.*?em\s*ate\s*(\d+)\s*dias", sec4
        )
        if m_pref:
            rules["preference_exchange_days"] = int(m_pref.group(1))

    sec8 = normalize_text(sections.get("8", ""))
    if sec8:
        m_warranty = re.search(
            r"8\.1\s*garantia\s*legal.*?garantia\s*legal\s*de\s*(\d+)", sec8
        )
        if m_warranty:
            rules["legal_warranty_days"] = int(m_warranty.group(1))

    return rules


# Calculate effective price and PIX price respecting non-cumulative promotion rules (Section 6.2).
def calculate_pricing(
    price_brl: float, active_discount_percent: float | None = None
) -> dict[str, Any]:
    _validate_financial_inputs(price_brl, active_discount_percent)
    policy = get_policy_rules()
    pix_rate = policy["pix_discount_rate"]

    original_price = round(float(price_brl), 2)
    discount = float(active_discount_percent or 0.0)
    has_promo = discount > 0.0

    if has_promo:
        effective_price = round(original_price * (1.0 - discount / 100.0), 2)
        # Section 6.2: PIX discount does not apply on top of already promotional prices.
        pix_price = effective_price
        pix_discount_applied = False
    else:
        effective_price = original_price
        pix_price = round(original_price * (1.0 - pix_rate), 2)
        pix_discount_applied = True

    return {
        "original_price_brl": original_price,
        "has_active_promotion": has_promo,
        "discount_percent": round(discount, 2),
        "effective_price_brl": effective_price,
        "pix_price_brl": pix_price,
        "pix_discount_applied": pix_discount_applied,
    }


# Calculate valid interest-free credit card installment options from 1x to 12x (Section 3.1).
def calculate_installments(amount_brl: float) -> list[dict[str, Any]]:
    _validate_financial_inputs(amount_brl)
    total = round(float(amount_brl), 2)
    if total <= 0:
        return []

    policy = get_policy_rules()
    min_2_3 = policy["min_installment_2_to_3x_brl"]
    min_4_6 = policy["min_installment_4_to_6x_brl"]
    min_7_12 = policy["min_installment_7_to_12x_brl"]

    options: list[dict[str, Any]] = [
        {"installments": 1, "installment_brl": total, "total_brl": total}
    ]

    for count in range(2, 13):
        installment_value = round(total / count, 2)

        if 2 <= count <= 3 and installment_value >= min_2_3:
            options.append(
                {
                    "installments": count,
                    "installment_brl": installment_value,
                    "total_brl": total,
                }
            )
        elif 4 <= count <= 6 and installment_value >= min_4_6:
            options.append(
                {
                    "installments": count,
                    "installment_brl": installment_value,
                    "total_brl": total,
                }
            )
        elif 7 <= count <= 12 and installment_value >= min_7_12:
            options.append(
                {
                    "installments": count,
                    "installment_brl": installment_value,
                    "total_brl": total,
                }
            )

    return options


# Check if a purchase qualifies for split payment based on final effective price (Section 3.1).
def check_split_payment_eligibility(
    effective_price_brl: float, original_price_brl: float | None = None
) -> dict[str, Any]:
    _validate_financial_inputs(effective_price_brl)
    if original_price_brl is not None:
        _validate_financial_inputs(original_price_brl)

    threshold = get_policy_rules()["split_payment_threshold_brl"]
    effective = round(float(effective_price_brl), 2)
    original = (
        round(float(original_price_brl), 2)
        if original_price_brl is not None
        else effective
    )
    eligible = effective > threshold

    crossed_threshold_after_discount = original > threshold >= effective

    if eligible:
        rule_note = (
            f"Eligible for split payment (e.g., PIX + credit card) because the final "
            f"effective price exceeds R$ {threshold:,.2f}. Card balance must still follow "
            f"minimum installment rules, and promotional discounts are non-cumulative."
        )
    elif crossed_threshold_after_discount:
        rule_note = (
            f"Not eligible for split payment under the adopted assumption: although the "
            f"original list price exceeds R$ {threshold:,.2f}, the final promotional price is "
            f"at or below R$ {threshold:,.2f}."
        )
    else:
        rule_note = (
            f"Not eligible for split payment because the purchase amount does not "
            f"exceed R$ {threshold:,.2f}."
        )

    return {
        "eligible": eligible,
        "threshold_brl": threshold,
        "evaluated_price_brl": effective,
        "crossed_threshold_after_discount": crossed_threshold_after_discount,
        "rule_note": rule_note,
    }


# Build a complete payment summary combining pricing, installments, and split payment rules.
def build_payment_summary(
    price_brl: float, active_discount_percent: float | None = None
) -> dict[str, Any]:
    pricing = calculate_pricing(price_brl, active_discount_percent)
    effective = pricing["effective_price_brl"]
    installments = calculate_installments(effective)
    max_opt = (
        installments[-1]
        if installments
        else {"installments": 1, "installment_brl": effective}
    )
    split_info = check_split_payment_eligibility(
        effective, pricing["original_price_brl"]
    )

    return {
        **pricing,
        "installment_options": installments,
        "max_installments": max_opt["installments"],
        "max_installment_brl": max_opt["installment_brl"],
        "split_payment": split_info,
    }


# Parse an ISO date string ('YYYY-MM-DD') or return an existing date object.
def _parse_iso_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return date.fromisoformat(cleaned[:10])


# Set or reset a default reference date for deterministic agent evaluations.
def set_reference_date(value: date | str | None) -> None:
    global _ACTIVE_REFERENCE_DATE
    _ACTIVE_REFERENCE_DATE = _parse_iso_date(value)


# Parse start and reference dates and return (parsed_start_date, calendar_days_elapsed).
def _calculate_days_elapsed(
    start_date: date | str | None, reference_date: date | str | None = None
) -> tuple[date | None, int | None]:
    start_dt = _parse_iso_date(start_date)
    if start_dt is None:
        return None, None
    ref_dt = (
        _parse_iso_date(reference_date)
        or _ACTIVE_REFERENCE_DATE
        or datetime.now(STORE_TIMEZONE).date()
    )
    return start_dt, (ref_dt - start_dt).days


# Evaluate Section 4.1 right of withdrawal (7 calendar days from actual receipt, online purchases only).
def evaluate_right_of_withdrawal(
    order_status: str,
    actual_delivery_date: date | str | None = None,
    reference_date: date | str | None = None,
    purchase_channel: str | None = None,
) -> dict[str, Any]:
    policy = get_policy_rules()
    limit_days = int(policy["withdrawal_days"])
    refund_days = int(policy["refund_business_days"])
    status_clean = (order_status or "").strip().lower()
    channel_clean = (purchase_channel or "").strip().lower() or None

    if channel_clean in IN_STORE_CHANNELS:
        return {
            "rule": "right_of_withdrawal_section_4_1",
            "status": "not_eligible_in_store",
            "eligible": False,
            "limit_days": limit_days,
            "channel_confirmation_required": False,
            "note": (
                "Section 4.1 right of withdrawal applies exclusively to online purchases. "
                "In-store purchases do not qualify under Section 4.1."
            ),
        }

    channel_confirmation_required = channel_clean not in ONLINE_CHANNELS

    if status_clean == "cancelled":
        return {
            "rule": "right_of_withdrawal_section_4_1",
            "status": "not_eligible_cancelled",
            "eligible": False,
            "limit_days": limit_days,
            "channel_confirmation_required": channel_confirmation_required,
            "note": "This order is already cancelled.",
        }

    if status_clean != "delivered":
        return {
            "rule": "right_of_withdrawal_section_4_1",
            "status": "not_yet_delivered",
            "eligible": None,
            "limit_days": limit_days,
            "channel_confirmation_required": channel_confirmation_required,
            "note": (
                f"Order status is '{status_clean}'. The {limit_days}-day withdrawal window "
                f"starts counting only after the customer actually receives the product."
            ),
        }

    delivery_dt, days_elapsed = _calculate_days_elapsed(
        actual_delivery_date, reference_date
    )
    if delivery_dt is None or days_elapsed is None:
        return {
            "rule": "right_of_withdrawal_section_4_1",
            "status": "indeterminate_missing_delivery_date",
            "eligible": None,
            "limit_days": limit_days,
            "channel_confirmation_required": channel_confirmation_required,
            "note": (
                "Cannot confirm withdrawal eligibility without the actual delivery date. "
                "Estimated delivery date from orders.csv is not a confirmation of receipt. "
                "Ask the customer when they actually received the product (and confirm if "
                "the purchase was made online)."
            ),
        }

    if days_elapsed < 0:
        return {
            "rule": "right_of_withdrawal_section_4_1",
            "status": "invalid_dates",
            "eligible": None,
            "days_since_delivery": days_elapsed,
            "limit_days": limit_days,
            "channel_confirmation_required": channel_confirmation_required,
            "note": "Reference date is earlier than the reported delivery date.",
        }

    if days_elapsed > limit_days:
        return {
            "rule": "right_of_withdrawal_section_4_1",
            "status": "expired",
            "eligible": False,
            "days_since_delivery": days_elapsed,
            "limit_days": limit_days,
            "channel_confirmation_required": channel_confirmation_required,
            "note": (
                f"The {limit_days}-day withdrawal window has expired ({days_elapsed} calendar "
                f"days since actual delivery on {delivery_dt.isoformat()})."
            ),
        }

    if channel_confirmation_required:
        return {
            "rule": "right_of_withdrawal_section_4_1",
            "status": "eligible_if_online",
            "eligible": None,
            "days_since_delivery": days_elapsed,
            "limit_days": limit_days,
            "refund_business_days": refund_days,
            "channel_confirmation_required": True,
            "note": (
                f"Within the {limit_days}-day window ({days_elapsed} days since receipt), "
                f"but orders.csv does not specify the purchase channel. Confirm with the "
                f"customer that the purchase was online. Product must be in original packaging, "
                f"unused, with all manuals/accessories. Return shipping is paid by the store "
                f"and refund takes up to {refund_days} business days."
            ),
        }

    return {
        "rule": "right_of_withdrawal_section_4_1",
        "status": "eligible",
        "eligible": True,
        "days_since_delivery": days_elapsed,
        "limit_days": limit_days,
        "refund_business_days": refund_days,
        "channel_confirmation_required": False,
        "note": (
            f"Eligible for online withdrawal ({days_elapsed} of {limit_days} calendar days "
            f"used since receipt). Product must be in original packaging without signs of use, "
            f"with all accessories and manuals. Store covers return shipping and processes "
            f"refund in the original payment method within {refund_days} business days."
        ),
    }


# Evaluate Section 4.3 preference exchange (7 calendar days from actual receipt as operational assumption + Section 4.4 restrictions).
def evaluate_preference_exchange(
    order_status: str,
    actual_delivery_date: date | str | None = None,
    reference_date: date | str | None = None,
    is_ineligible_item: bool = False,
) -> dict[str, Any]:
    policy = get_policy_rules()
    limit_days = int(policy["preference_exchange_days"])
    status_clean = (order_status or "").strip().lower()
    milestone_assumption = (
        "Operational assumption: 7-day preference exchange window is counted from actual "
        "product receipt (Section 4.3 states 'up to 7 days' without specifying the starting milestone)."
    )

    if is_ineligible_item:
        return {
            "rule": "preference_exchange_section_4_3",
            "status": "not_eligible_restricted_item",
            "eligible": False,
            "limit_days": limit_days,
            "milestone_assumption": milestone_assumption,
            "note": (
                "Not eligible for exchange under Section 4.4: custom-setup instruments, "
                "explicit 'final sale' clearance items, and wind instrument mouthpieces "
                "(for hygiene reasons) cannot be exchanged."
            ),
        }

    if status_clean == "cancelled":
        return {
            "rule": "preference_exchange_section_4_3",
            "status": "not_eligible_cancelled",
            "eligible": False,
            "limit_days": limit_days,
            "milestone_assumption": milestone_assumption,
            "note": "This order is already cancelled.",
        }

    if status_clean != "delivered":
        return {
            "rule": "preference_exchange_section_4_3",
            "status": "not_yet_delivered",
            "eligible": None,
            "limit_days": limit_days,
            "milestone_assumption": milestone_assumption,
            "note": f"Order status is '{status_clean}' and has not been delivered yet.",
        }

    delivery_dt, days_elapsed = _calculate_days_elapsed(
        actual_delivery_date, reference_date
    )
    if delivery_dt is None or days_elapsed is None:
        return {
            "rule": "preference_exchange_section_4_3",
            "status": "indeterminate_missing_delivery_date",
            "eligible": None,
            "limit_days": limit_days,
            "milestone_assumption": milestone_assumption,
            "note": (
                "Cannot confirm preference exchange eligibility without the actual receipt date. "
                "Ask the customer when they received the item."
            ),
        }

    if days_elapsed < 0:
        return {
            "rule": "preference_exchange_section_4_3",
            "status": "invalid_dates",
            "eligible": None,
            "days_since_delivery": days_elapsed,
            "limit_days": limit_days,
            "milestone_assumption": milestone_assumption,
            "note": "Reference date is earlier than the reported delivery date.",
        }

    eligible = days_elapsed <= limit_days
    return {
        "rule": "preference_exchange_section_4_3",
        "status": "eligible" if eligible else "expired",
        "eligible": eligible,
        "days_since_delivery": days_elapsed,
        "limit_days": limit_days,
        "milestone_assumption": milestone_assumption,
        "note": (
            f"Eligible for preference exchange ({days_elapsed} of {limit_days} days used). "
            f"Subject to stock availability of the desired model, perfect condition, original "
            f"packaging, and Section 4.4 exclusions."
            if eligible
            else f"Preference exchange window of {limit_days} days has expired ({days_elapsed} days since receipt)."
        ),
    }


# Evaluate Section 4.2 defect exchange at the store (30 calendar days from order_date).
def evaluate_defect_exchange(
    order_status: str,
    order_date: date | str | None,
    reference_date: date | str | None = None,
) -> dict[str, Any]:
    policy = get_policy_rules()
    limit_days = int(policy["defect_exchange_days"])
    status_clean = (order_status or "").strip().lower()

    if status_clean == "cancelled":
        return {
            "rule": "defect_exchange_section_4_2",
            "status": "not_eligible_cancelled",
            "eligible": False,
            "limit_days": limit_days,
            "note": "This order is cancelled.",
        }

    purchase_dt, days_elapsed = _calculate_days_elapsed(order_date, reference_date)
    if purchase_dt is None or days_elapsed is None:
        return {
            "rule": "defect_exchange_section_4_2",
            "status": "indeterminate_missing_order_date",
            "eligible": None,
            "limit_days": limit_days,
            "note": "Missing purchase date (order_date) to evaluate the 30-day store defect exchange window.",
        }

    if days_elapsed < 0:
        return {
            "rule": "defect_exchange_section_4_2",
            "status": "invalid_dates",
            "eligible": None,
            "days_since_purchase": days_elapsed,
            "limit_days": limit_days,
            "note": "Reference date is earlier than the order purchase date.",
        }

    if days_elapsed <= limit_days:
        return {
            "rule": "defect_exchange_section_4_2",
            "status": "eligible_store_exchange",
            "eligible": True,
            "days_since_purchase": days_elapsed,
            "limit_days": limit_days,
            "note": (
                f"Eligible for direct defect exchange with the store ({days_elapsed} of "
                f"{limit_days} calendar days since purchase on {purchase_dt.isoformat()}). "
                f"Covers manufacturing defects only (excludes misuse, drops, excessive humidity, "
                f"or unauthorized modifications)."
            ),
        }

    return {
        "rule": "defect_exchange_section_4_2",
        "status": "manufacturer_warranty_only",
        "eligible": False,
        "days_since_purchase": days_elapsed,
        "limit_days": limit_days,
        "note": (
            f"The {limit_days}-day store exchange window from purchase date ({purchase_dt.isoformat()}) "
            f"has passed ({days_elapsed} days elapsed). Per Section 4.2, after {limit_days} days the "
            f"customer must contact the manufacturer directly for warranty service, and Empório da "
            f"Música can mediate the process upon request."
        ),
    }


# Evaluate Section 8.1 legal warranty (90 calendar days from actual receipt) and Section 8.2 manufacturer warranty.
def evaluate_legal_warranty(
    order_status: str,
    actual_delivery_date: date | str | None = None,
    reference_date: date | str | None = None,
) -> dict[str, Any]:
    policy = get_policy_rules()
    limit_days = int(policy["legal_warranty_days"])
    status_clean = (order_status or "").strip().lower()

    if status_clean == "cancelled":
        return {
            "rule": "legal_warranty_section_8",
            "status": "not_eligible_cancelled",
            "eligible": False,
            "limit_days": limit_days,
            "note": "This order is cancelled.",
        }

    if status_clean != "delivered":
        return {
            "rule": "legal_warranty_section_8",
            "status": "not_yet_delivered",
            "eligible": None,
            "limit_days": limit_days,
            "note": (
                f"Order status is '{status_clean}'. The {limit_days}-day legal warranty (CDC) "
                f"starts counting from the date the customer receives the product."
            ),
        }

    delivery_dt, days_elapsed = _calculate_days_elapsed(
        actual_delivery_date, reference_date
    )
    if delivery_dt is None or days_elapsed is None:
        return {
            "rule": "legal_warranty_section_8",
            "status": "indeterminate_missing_delivery_date",
            "eligible": None,
            "limit_days": limit_days,
            "note": (
                f"Cannot verify the {limit_days}-day legal warranty window without the actual "
                f"receipt date. Estimated delivery is not confirmation of receipt. Ask the "
                f"customer when they received the product."
            ),
        }

    if days_elapsed < 0:
        return {
            "rule": "legal_warranty_section_8",
            "status": "invalid_dates",
            "eligible": None,
            "days_since_delivery": days_elapsed,
            "limit_days": limit_days,
            "note": "Reference date is earlier than the reported delivery date.",
        }

    eligible = days_elapsed <= limit_days
    return {
        "rule": "legal_warranty_section_8",
        "status": "within_legal_warranty" if eligible else "legal_warranty_expired",
        "eligible": eligible,
        "days_since_delivery": days_elapsed,
        "limit_days": limit_days,
        "note": (
            f"Within the {limit_days}-day CDC legal warranty against manufacturing defects "
            f"({days_elapsed} days since receipt on {delivery_dt.isoformat()}). In addition, "
            f"most manufacturers offer contractual warranty from 6 months to 2 years (Section 8.2). "
            f"Excludes natural wear (strings, frets, felts, reeds), misuse, unauthorized repairs, "
            f"and purely cosmetic damage (Section 8.3)."
            if eligible
            else f"The {limit_days}-day CDC legal warranty has expired ({days_elapsed} days since "
            f"receipt on {delivery_dt.isoformat()}). Check if the product is still covered by the "
            f"manufacturer contractual warranty (6 months to 2 years per Section 8.2)."
        ),
    }


# Known instrument manufacturers referenced across products.csv names and descriptions.
# Used to detect when a product title states one brand while its description states another.
CATALOG_BRANDS: tuple[str, ...] = (
    "music man",
    "yamaha",
    "tagima",
    "giannini",
    "rozini",
    "shelby",
    "crafter",
    "martin",
    "taylor",
    "takamine",
    "kala",
    "ohana",
    "kalani",
    "fender",
    "gibson",
    "ibanez",
    "prs",
    "pearl",
    "tama",
    "korg",
    "nord",
    "roland",
)


# Validate consistency across product name, description, and JSON specs (Sections 7.1 and 10).
def validate_product_consistency(
    name: str, description: str | None, specs_raw: str | dict[str, Any] | None
) -> dict[str, Any]:
    norm_name = normalize_text(name)
    norm_desc = normalize_text(description or "")
    conflicts: list[str] = []

    specs: dict[str, Any] = {}
    if isinstance(specs_raw, dict):
        specs = specs_raw
    elif isinstance(specs_raw, str) and specs_raw.strip():
        try:
            parsed = json.loads(specs_raw)
            if isinstance(parsed, dict):
                specs = parsed
        except json.JSONDecodeError:
            conflicts.append("Invalid JSON format in specs column.")

    # 1. Check brand mismatch between product name and description.
    name_brands = [
        b for b in CATALOG_BRANDS if re.search(rf"\b{re.escape(b)}\b", norm_name)
    ]
    desc_brands = [
        b for b in CATALOG_BRANDS if re.search(rf"\b{re.escape(b)}\b", norm_desc)
    ]

    if name_brands and desc_brands:
        if not set(name_brands).intersection(desc_brands):
            conflicts.append(
                f"Brand mismatch: name states '{name_brands[0].title()}' but description "
                f"states '{desc_brands[0].title()}'."
            )

    # Check Fender Precision model name paired with non-Fender bass title (e.g., ID 136).
    if "ibanez" in name_brands and "contrabaixo precision" in norm_desc:
        conflicts.append(
            "Model mismatch: name states 'Ibanez' but description refers to a 'Precision' bass."
        )

    # 2. Check string count mismatch between description (e.g. '5 cordas') and specs['strings'].
    spec_strings = normalize_text(str(specs.get("strings", "")))
    if spec_strings.isdigit():
        desc_strings_match = re.search(r"\b(\d+)\s*cordas\b", norm_desc)
        if desc_strings_match and desc_strings_match.group(1) != spec_strings:
            conflicts.append(
                f"String count mismatch: description states '{desc_strings_match.group(1)} strings' "
                f"but specs state '{spec_strings} strings'."
            )

    # 3. Check key count mismatch between description (e.g. '88 teclas') and specs['keys'].
    spec_keys = normalize_text(str(specs.get("keys", "")))
    if spec_keys.isdigit():
        desc_keys_match = re.search(r"\b(\d+)\s*teclas\b", norm_desc)
        if desc_keys_match and desc_keys_match.group(1) != spec_keys:
            conflicts.append(
                f"Key count mismatch: description states '{desc_keys_match.group(1)} keys' "
                f"but specs state '{spec_keys} keys'."
            )

    # 4. Check body wood mismatch between description ('corpo em X') and specs['body'].
    spec_body = normalize_text(str(specs.get("body", "")))
    if spec_body:
        body_match = re.search(r"\bcorpo\s+em\s+([a-z]+)\b", norm_desc)
        if body_match:
            desc_wood = body_match.group(1)
            if desc_wood not in spec_body:
                conflicts.append(
                    f"Body wood mismatch: description states '{desc_wood.title()}' "
                    f"but specs state '{specs.get('body')}'."
                )

    # 5. Check drum shell wood mismatch between description ('cascos em/100% X') and specs['shells'].
    spec_shells = normalize_text(str(specs.get("shells", "")))
    if spec_shells:
        shell_match = re.search(r"\bcascos\s+(?:em|100%)\s+([a-z]+)\b", norm_desc)
        if shell_match:
            desc_shell = shell_match.group(1)
            if desc_shell not in spec_shells:
                conflicts.append(
                    f"Drum shell wood mismatch: description states '{desc_shell.title()}' "
                    f"but specs state '{specs.get('shells')}'."
                )

    has_conflict = len(conflicts) > 0
    guidance = (
        "Technical discrepancy detected in catalog record. Do not guess or pick one "
        "specification over the other. Inform the customer about the catalog discrepancy "
        "and offer to confirm the exact specification with the store team/management (Section 10)."
        if has_conflict
        else None
    )

    return {
        "has_conflict": has_conflict,
        "conflicts": conflicts,
        "guidance": guidance,
    }


# Engineering review notes (documented for README.md reference):
# 1. Resolved during refactoring:
#    - Added warning logs in get_policy_rules() when PDF extraction falls back to defaults.
#    - Added _validate_financial_inputs() to reject negative prices or invalid discount ranges (0-100%).
#    - Evaluated installment minimum thresholds against the rounded 2-decimal installment amount.
#    - Extracted _calculate_days_elapsed() to avoid repeating date parsing across post-sale evaluators.
#    - Defined explicit IN_STORE_CHANNELS and ONLINE_CHANNELS constants for bilingual channel matching.
# 2. Known trade-offs left out to avoid over-engineering for this challenge:
#    - Business-rule fallback defaults stay in rules.py instead of .env because store deadlines,
#      PIX rates, and installment thresholds are domain rules rather than environment config.
#    - The four post-sale evaluators remain separate functions with inline result messages instead
#      of using a generic rule engine or external message-template layer, keeping each legal/store
#      rule easy to read and test in isolation.
#    - Brand and spec conflict checks in validate_product_consistency() target the actual structure
#      and manufacturers of products.csv rather than using open-ended NLP heuristics.
