"""Thin async client around the MediaMTX Control API (v3).

Endpoint reference: https://mediamtx.org/docs/references/control-api
(generated from https://github.com/bluenviron/mediamtx/blob/main/api/openapi.yaml)

Only the endpoints this GUI actually relies on are wrapped here. The core
path-config CRUD and global-config get/patch endpoints are stable/confirmed;
the various per-protocol "list active connections" endpoints are treated as
best-effort (wrapped so a 404/unexpected-shape response degrades gracefully
instead of breaking the whole page), since their exact spelling can change
between MediaMTX releases.
"""
from __future__ import annotations

from typing import Any

import httpx


class MediaMTXError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"MediaMTX-API-Fehler ({status_code}): {detail}")


class MediaMTXClient:
    def __init__(
        self,
        base_url: str,
        api_user: str = "",
        api_pass: str = "",
        timeout: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        auth = (api_user, api_pass) if api_user else None
        self._client = httpx.AsyncClient(base_url=self.base_url, auth=auth, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise MediaMTXError(0, f"Verbindung zu {self.base_url} fehlgeschlagen: {exc}") from exc

        if resp.status_code >= 400:
            detail = resp.text
            try:
                data = resp.json()
                detail = data.get("error", detail)
            except Exception:
                pass
            raise MediaMTXError(resp.status_code, detail)

        if not resp.content:
            return None
        try:
            return resp.json()
        except Exception:
            return None

    async def _best_effort(self, method: str, path: str, **kwargs) -> Any:
        """Like _request, but returns None instead of raising on failure.
        Used for auxiliary/less-stable endpoints (per-protocol connection
        listings) so one unavailable endpoint doesn't break a whole page."""
        try:
            return await self._request(method, path, **kwargs)
        except MediaMTXError:
            return None

    # ---- health -----------------------------------------------------

    async def ping(self) -> bool:
        try:
            await self._request("GET", "/v3/paths/list", params={"page": 0, "itemsPerPage": 1})
            return True
        except MediaMTXError:
            return False

    # ---- runtime paths ------------------------------------------------

    async def list_paths(self, page: int = 0, items_per_page: int = 100) -> dict:
        return await self._request(
            "GET", "/v3/paths/list", params={"page": page, "itemsPerPage": items_per_page}
        )

    async def get_path(self, name: str) -> dict:
        return await self._request("GET", f"/v3/paths/get/{name}")

    # ---- path configuration (CRUD) ------------------------------------

    async def list_path_configs(self, page: int = 0, items_per_page: int = 100) -> dict:
        return await self._request(
            "GET", "/v3/config/paths/list", params={"page": page, "itemsPerPage": items_per_page}
        )

    async def get_path_config(self, name: str) -> dict | None:
        try:
            return await self._request("GET", f"/v3/config/paths/get/{name}")
        except MediaMTXError as exc:
            if exc.status_code == 404:
                return None
            raise

    async def add_path_config(self, name: str, conf: dict) -> None:
        await self._request("POST", f"/v3/config/paths/add/{name}", json=conf)

    async def patch_path_config(self, name: str, conf: dict) -> None:
        await self._request("PATCH", f"/v3/config/paths/patch/{name}", json=conf)

    async def replace_path_config(self, name: str, conf: dict) -> None:
        await self._request("POST", f"/v3/config/paths/replace/{name}", json=conf)

    async def delete_path_config(self, name: str) -> None:
        await self._request("DELETE", f"/v3/config/paths/delete/{name}")

    # ---- global configuration ------------------------------------------

    async def get_global_config(self) -> dict:
        return await self._request("GET", "/v3/config/global/get")

    async def patch_global_config(self, conf: dict) -> None:
        await self._request("PATCH", "/v3/config/global/patch", json=conf)

    # ---- best-effort live connection/session listings -------------------

    async def list_rtsp_conns(self) -> dict | None:
        return await self._best_effort("GET", "/v3/rtsp/conns/list")

    async def list_rtmp_conns(self) -> dict | None:
        return await self._best_effort("GET", "/v3/rtmp/conns/list")

    async def list_webrtc_sessions(self) -> dict | None:
        return await self._best_effort("GET", "/v3/webrtc/sessions/list")

    async def list_srt_conns(self) -> dict | None:
        return await self._best_effort("GET", "/v3/srt/conns/list")

    async def list_hls_muxers(self) -> dict | None:
        return await self._best_effort("GET", "/v3/hls/muxers/list")


class PlaybackClient:
    """Client for MediaMTX's separate playback/recordings HTTP server
    (default port 9996, enabled via `playback: yes`)."""

    def __init__(self, base_url: str, timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/") if base_url else ""
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout) if self.base_url else None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()

    async def list_recordings(self, path: str) -> list[dict] | None:
        if not self._client:
            return None
        try:
            resp = await self._client.get("/list", params={"path": path})
            if resp.status_code >= 400:
                return None
            return resp.json()
        except httpx.RequestError:
            return None

    def download_url(self, path: str, start: str, duration: str, fmt: str = "mp4") -> str:
        return (
            f"{self.base_url}/get?path={path}&start={start}"
            f"&duration={duration}&format={fmt}"
        )
