"""Smoke flag `pm-erp.gb-smoke`, read through framith_growthbook (soft import).

When framith_growthbook is not installed, or anything fails, the flag is off.
"""

import frappe

APP_CODE = "pm-erp"
FLAG_NAME = "gb-smoke"


def flag_key() -> str:
	try:
		from framith_growthbook.config import KEY_SEPARATOR as sep
	except ImportError:
		sep = "."
	return f"{APP_CODE}{sep}{FLAG_NAME}"


def smoke_flag_on(user: str | None = None) -> bool:
	"""True when the smoke flag is on. Off when the helper is missing or fails."""
	try:
		from framith_growthbook.api import is_on
	except ImportError:
		return False
	try:
		return bool(is_on(flag_key(), user))
	except Exception:
		return False


@frappe.whitelist()
def ping_flags() -> dict:
	"""Report the smoke flag for the session user. Read only; needs a logged-in user."""
	return {"flag": flag_key(), "on": smoke_flag_on()}
