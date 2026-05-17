import frappe
import traceback


def render():
    try:
        html = frappe.get_print(
            "Sales Invoice",
            "POL-2026-00008",
            print_format="Polemarch Sale Transfer Order",
            no_letterhead=1,
        )
        return f"OK len={len(html or '')}"
    except Exception as e:
        tb = traceback.format_exc()
        return f"ERROR: {type(e).__name__}: {e}\n\n{tb[-2000:]}"
