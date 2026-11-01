# Deterministic business rules for pricing, installments, post-sale deadlines, and catalog validation.

import re
from functools import lru_cache
from typing import Any
from src.database import load_policy_sections, normalize_text

DEFAULT_PIX_DISCOUNT_RATE = 0.05
DEFAULT_MIN_INSTALLMENT_2_TO_3X_BRL = 50.00
DEFAULT_MIN_INSTALLMENT_4_TO_6X_BRL = 80.00
DEFAULT_MIN_INSTALLMENT_7_TO_12X_BRL = 100.00
DEFAULT_SPLIT_PAYMENT_THRESHOLD_BRL = 2000.00


# Convert a Brazilian currency number string (e.g., '2.000,00' or '50,00') to float.
def _parse_brl_number(raw_value: str, fallback: float) -> float:
    cleaned = raw_value.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return fallback


# Extract commercial rule parameters from the store policy PDF with safe fallbacks.
@lru_cache(maxsize=1)
def get_policy_rules() -> dict[str, float]:
    rules = {
        "pix_discount_rate": DEFAULT_PIX_DISCOUNT_RATE,
        "min_installment_2_to_3x_brl": DEFAULT_MIN_INSTALLMENT_2_TO_3X_BRL,
        "min_installment_4_to_6x_brl": DEFAULT_MIN_INSTALLMENT_4_TO_6X_BRL,
        "min_installment_7_to_12x_brl": DEFAULT_MIN_INSTALLMENT_7_TO_12X_BRL,
        "split_payment_threshold_brl": DEFAULT_SPLIT_PAYMENT_THRESHOLD_BRL,
    }

    try:
        sections = load_policy_sections()
    except Exception:
        return rules

    sec3 = normalize_text(sections.get("3", ""))
    if not sec3:
        return rules

    pix_match = re.search(r"pix.*?(\d+(?:[.,]\d+)?)%\s*de\s*desconto", sec3)
    if pix_match:
        rules["pix_discount_rate"] = _parse_brl_number(
            pix_match.group(1), DEFAULT_PIX_DISCOUNT_RATE * 100.0
        ) / 100.0

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

    return rules


# Calculate effective price and PIX price respecting non-cumulative promotion rules (Section 6.2).
def calculate_pricing(
    price_brl: float, active_discount_percent: float | None = None
) -> dict[str, Any]:
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
        raw_installment = total / count
        installment_value = round(raw_installment, 2)

        if 2 <= count <= 3 and raw_installment >= min_2_3 - 1e-9:
            options.append(
                {
                    "installments": count,
                    "installment_brl": installment_value,
                    "total_brl": total,
                }
            )
        elif 4 <= count <= 6 and raw_installment >= min_4_6 - 1e-9:
            options.append(
                {
                    "installments": count,
                    "installment_brl": installment_value,
                    "total_brl": total,
                }
            )
        elif 7 <= count <= 12 and raw_installment >= min_7_12 - 1e-9:
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
    threshold = get_policy_rules()["split_payment_threshold_brl"]
    effective = round(float(effective_price_brl), 2)
    original = round(float(original_price_brl), 2) if original_price_brl is not None else effective
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
    max_opt = installments[-1] if installments else {"installments": 1, "installment_brl": effective}
    split_info = check_split_payment_eligibility(effective, pricing["original_price_brl"])

    return {
        **pricing,
        "installment_options": installments,
        "max_installments": max_opt["installments"],
        "max_installment_brl": max_opt["installment_brl"],
        "split_payment": split_info,
    }
