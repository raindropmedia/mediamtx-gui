"""Builds the human-readable ingest/egress URLs shown per stream, so they
can be copy-pasted directly into an encoder/decoder (OBS, VLC, ffmpeg,
hardware encoders, ...).

TLS-aware: HLS and WebRTC each have a single on/off encryption switch in
MediaMTX (hlsEncryption / webrtcEncryption) - once enabled, MediaMTX serves
that protocol as HTTPS-only on its existing port, so the plain http:// link
would stop working. RTSP and RTMP instead have a three-way switch ("no" /
"optional" / "strict") plus a *separate* port for the encrypted variant
(rtspsAddress / rtmpsAddress) - "optional" keeps the original plain port
working alongside the new encrypted one, so both links are still valid and
shown side by side.
"""
from __future__ import annotations

from .config import Settings


def _port(addr: object, default: int) -> int:
    if not addr:
        return default
    try:
        return int(str(addr).rsplit(":", 1)[-1])
    except ValueError:
        return default


def build_links(
    path_name: str,
    global_conf: dict,
    settings: Settings,
    cred_user: str | None = None,
    cred_pass: str | None = None,
) -> dict[str, str]:
    host = settings.public_host
    rtsp_port = _port(global_conf.get("rtspAddress"), 8554)
    rtsps_port = _port(global_conf.get("rtspsAddress"), 8322)
    rtmp_port = _port(global_conf.get("rtmpAddress"), 1935)
    rtmps_port = _port(global_conf.get("rtmpsAddress"), 1936)
    hls_port = _port(global_conf.get("hlsAddress"), 8888)
    webrtc_port = _port(global_conf.get("webrtcAddress"), 8889)
    srt_port = _port(global_conf.get("srtAddress"), 8890)
    playback_port = _port(global_conf.get("playbackAddress"), 9996)

    # "no" (Standard) = nur unverschluesselt, "optional" = beides parallel
    # auf getrennten Ports, "strict" = nur noch die verschluesselte Variante.
    rtsp_mode = str(global_conf.get("rtspEncryption") or "no").lower()
    rtmp_mode = str(global_conf.get("rtmpEncryption") or "no").lower()
    hls_tls = bool(global_conf.get("hlsEncryption"))
    webrtc_tls = bool(global_conf.get("webrtcEncryption"))

    auth_prefix = f"{cred_user}:{cred_pass}@" if cred_user else ""
    rtmp_query = f"?user={cred_user}&pass={cred_pass}" if cred_user else ""
    srt_suffix = f":{cred_user}:{cred_pass}" if cred_user else ""

    links: dict[str, str] = {}

    if rtsp_mode != "strict":
        links["RTSP (Publish/Read)"] = f"rtsp://{auth_prefix}{host}:{rtsp_port}/{path_name}"
    if rtsp_mode in ("optional", "strict"):
        links["RTSPS (Publish/Read, verschluesselt)"] = (
            f"rtsps://{auth_prefix}{host}:{rtsps_port}/{path_name}"
        )

    if rtmp_mode != "strict":
        links["RTMP Publish"] = f"rtmp://{host}:{rtmp_port}/{path_name}{rtmp_query}"
        links["RTMP Read"] = f"rtmp://{host}:{rtmp_port}/{path_name}"
    if rtmp_mode in ("optional", "strict"):
        links["RTMPS Publish (verschluesselt)"] = f"rtmps://{host}:{rtmps_port}/{path_name}{rtmp_query}"
        links["RTMPS Read (verschluesselt)"] = f"rtmps://{host}:{rtmps_port}/{path_name}"

    hls_scheme = "https" if hls_tls else "http"
    links["HLS Playlist (.m3u8)"] = f"{hls_scheme}://{host}:{hls_port}/{path_name}/index.m3u8"
    links["HLS Browser-Ansicht"] = f"{hls_scheme}://{host}:{hls_port}/{path_name}"

    webrtc_scheme = "https" if webrtc_tls else "http"
    links["WebRTC WHIP (Publish)"] = f"{webrtc_scheme}://{host}:{webrtc_port}/{path_name}/whip"
    links["WebRTC WHEP (Read)"] = f"{webrtc_scheme}://{host}:{webrtc_port}/{path_name}/whep"
    links["WebRTC Browser-Ansicht"] = f"{webrtc_scheme}://{host}:{webrtc_port}/{path_name}"

    links["SRT Publish"] = f"srt://{host}:{srt_port}?streamid=publish:{path_name}{srt_suffix}&pkt_size=1316"
    links["SRT Read"] = f"srt://{host}:{srt_port}?streamid=read:{path_name}{srt_suffix}&pkt_size=1316"
    links["Aufnahmen (Playback-API)"] = f"http://{host}:{playback_port}/list?path={path_name}"

    return links
