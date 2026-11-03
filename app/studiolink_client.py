"""Async client for the StudioLink backend admin API.

The backend owns the signing keys and the reporter list (data/reporters.json);
the GUI only calls its admin API. Keys never leave the backend.
"""
from __future__ import annotations

from typing import Any

import httpx


class StudioLinkError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"StudioLink-Backend ({status_code}): {detail}")


class StudioLinkClient:
    def __init__(self, base_url: str, admin_token: str, timeout: float = 6.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.enabled = bool(self.base_url and admin_token)
        self._client = httpx.AsyncClient(
            base_url=self.base_url or "http://invalid",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if not self.enabled:
            raise StudioLinkError(0, "StudioLink ist nicht konfiguriert (STUDIOLINK_API_URL / STUDIOLINK_ADMIN_TOKEN).")
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise StudioLinkError(0, f"Backend unter {self.base_url} nicht erreichbar: {exc}") from exc
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("error", detail)
            except Exception:  # noqa: BLE001
                pass
            if resp.status_code == 401:
                detail = "Admin-Token abgelehnt (STUDIOLINK_ADMIN_TOKEN passt nicht zum Backend)."
            raise StudioLinkError(resp.status_code, detail)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ---- Uebersicht -------------------------------------------------------
    async def info(self) -> dict:
        return await self._request("GET", "/api/info")

    async def calls(self) -> list[dict]:
        return await self._request("GET", "/api/calls")

    # ---- Reporter ---------------------------------------------------------
    async def list_reporters(self) -> list[dict]:
        return await self._request("GET", "/api/reporters")

    async def get_reporter(self, rid: str) -> dict | None:
        try:
            return await self._request("GET", f"/api/reporters/{rid}")
        except StudioLinkError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def create_reporter(self, data: dict) -> dict:
        return await self._request("POST", "/api/reporters", json=data)

    async def update_reporter(self, rid: str, data: dict) -> dict:
        return await self._request("PUT", f"/api/reporters/{rid}", json=data)

    async def delete_reporter(self, rid: str) -> None:
        await self._request("DELETE", f"/api/reporters/{rid}")

    async def revoke_reporter(self, rid: str) -> dict:
        return await self._request("POST", f"/api/reporters/{rid}/revoke")

    # ---- Konfiguration / Tokens -------------------------------------------
    async def issue_config(self, rid: str, *, standalone: bool, hours: float) -> dict:
        params = {"hours": str(hours)}
        if standalone:
            params["mode"] = "standalone"
        return await self._request("POST", f"/api/reporters/{rid}/config", params=params)

    async def issue_encoder(self, rid: str, hours: float) -> dict:
        return await self._request("POST", f"/api/reporters/{rid}/encoder", params={"hours": str(hours)})

    # ---- Geraete (Studiogeraete mit langlebigem Token) ----------------------
    async def list_devices(self) -> list[dict]:
        return await self._request("GET", "/api/devices")

    async def get_device(self, device_id: str) -> dict | None:
        try:
            return await self._request("GET", f"/api/devices/{device_id}")
        except StudioLinkError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def create_device(self, *, name: str, action: str, path: str, days: int) -> dict:
        return await self._request("POST", "/api/devices", json={"name": name, "action": action, "path": path, "days": days})

    async def delete_device(self, device_id: str) -> None:
        await self._request("DELETE", f"/api/devices/{device_id}")
