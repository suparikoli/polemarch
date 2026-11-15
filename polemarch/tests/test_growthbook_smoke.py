"""Smoke flag `pm-erp.gb-smoke`: off without the helper, on with a mocked dev payload."""

import sys
from unittest import skipUnless
from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from polemarch.utils import growthbook_smoke as smoke

try:
	from framith_growthbook.tests.helpers import configured, make_settings
except ImportError:
	configured = make_settings = None

PAYLOAD = {
	"features": {
		"pm-erp.gb-smoke": {
			"defaultValue": False,
			"rules": [{"condition": {"app": "pm-erp"}, "force": True}],
		},
	}
}


class TestGrowthbookSmoke(IntegrationTestCase):
	def test_key(self):
		self.assertEqual(smoke.flag_key(), "pm-erp.gb-smoke")

	def test_helper_missing_returns_off(self):
		with patch.dict(sys.modules, {"framith_growthbook.api": None}):
			self.assertFalse(smoke.smoke_flag_on())
			self.assertEqual(smoke.ping_flags(), {"flag": "pm-erp.gb-smoke", "on": False})

	def test_helper_error_returns_off(self):
		with patch("framith_growthbook.api.is_on", side_effect=RuntimeError("boom")):
			self.assertFalse(smoke.smoke_flag_on())

	@skipUnless(configured, "framith_growthbook is not installed")
	def test_dev_value_when_helper_present(self):
		with configured(settings=make_settings(app_code="pm-erp"), payload=PAYLOAD):
			self.assertTrue(smoke.smoke_flag_on())
			self.assertEqual(smoke.ping_flags(), {"flag": "pm-erp.gb-smoke", "on": True})

	@skipUnless(configured, "framith_growthbook is not installed")
	def test_default_off_when_disabled(self):
		with configured(settings=make_settings(app_code="pm-erp", enabled=False), payload=PAYLOAD):
			self.assertFalse(smoke.smoke_flag_on())
