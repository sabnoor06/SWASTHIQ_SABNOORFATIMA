"""
money.py — all money is integer paise. Never float.

Rupee conversion happens only at the presentation boundary (formatting for
display or for the LLM prompt). Arithmetic is always on ints.
"""


def paise_to_rupee_str(paise: int) -> str:
    """1234567 -> '₹12,345.67'. Presentation only — never feed back into math."""
    sign = "-" if paise < 0 else ""
    p = abs(int(paise))
    rupees, sub = divmod(p, 100)
    return f"{sign}₹{rupees:,}.{sub:02d}"


def paise_to_rupee_compact(paise: int) -> str:
    """1234500 -> '₹12,345'. Drops paise when they are zero, for narrative text."""
    p = int(paise)
    if p % 100 == 0:
        sign = "-" if p < 0 else ""
        return f"{sign}₹{abs(p) // 100:,}"
    return paise_to_rupee_str(p)
