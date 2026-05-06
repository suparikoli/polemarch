import json
import time

import frappe
import requests


class MedusaError(Exception):
    pass


class MedusaClient:
    def __init__(self, settings=None):
        self.settings = settings or frappe.get_single("Medusa Settings")
        if not self.settings.medusa_url:
            raise MedusaError("Medusa URL is not configured")
        self.base_url = self.settings.medusa_url.rstrip("/")
        self.timeout = int(self.settings.request_timeout_seconds or 30)
        self.max_retries = int(self.settings.max_retries or 3)
        self._admin_key = self.settings.get_password("medusa_admin_api_key", raise_exception=False)
        self._publishable_key = self.settings.get_password("medusa_publishable_api_key", raise_exception=False)

    def get(self, path, params=None, **kw):
        return self._request("GET", path, params=params, **kw)

    def post(self, path, json_body=None, idempotency_key=None, **kw):
        return self._request("POST", path, json=json_body, idempotency_key=idempotency_key, **kw)

    def put(self, path, json_body=None, idempotency_key=None, **kw):
        return self._request("PUT", path, json=json_body, idempotency_key=idempotency_key, **kw)

    def delete(self, path, **kw):
        return self._request("DELETE", path, **kw)

    def _request(self, method, path, *, params=None, json=None, idempotency_key=None, store=False):
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if store and self._publishable_key:
            headers["x-publishable-api-key"] = self._publishable_key
        elif not store and self._admin_key:
            headers["Authorization"] = f"Bearer {self._admin_key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_err = exc
                if attempt == self.max_retries:
                    raise MedusaError(f"{method} {path} failed: {exc}") from exc
                time.sleep(2 ** attempt)
                continue

            if resp.status_code >= 500 and attempt < self.max_retries:
                time.sleep(2 ** attempt)
                continue

            if resp.status_code >= 400:
                raise MedusaError(f"{method} {path} -> {resp.status_code}: {resp.text}")

            try:
                return resp.json()
            except ValueError:
                return {}

        raise MedusaError(f"{method} {path} exhausted retries: {last_err}")


def get_client():
    settings = frappe.get_single("Medusa Settings")
    if not settings.enable_sync:
        return None
    return MedusaClient(settings)
