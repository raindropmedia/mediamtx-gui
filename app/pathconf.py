"""Translates the stream edit form into a MediaMTX PathConf JSON object,
and back (best-effort) for prefilling the form from an existing config.

Only a curated subset of PathConf fields get dedicated form controls - the
"Erweiterte Parameter (JSON)" textarea is the escape hatch that gives access
to every other field MediaMTX's API accepts (hooks, SRT passphrases, WHEP
tokens, Raspberry Pi camera settings, ...) without this app having to grow a
UI control for each one individually. Values from the JSON box are merged
on top of the structured fields, so they can also override them.
"""
from __future__ import annotations

import json
import re

PATH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-/]{0,254}$")

KNOWN_SOURCE_SCHEMES = (
    "rtsp://", "rtsps://", "rtsp+http://", "rtsps+http://", "rtsp+ws://", "rtsps+ws://",
    "rtmp://", "rtmps://", "http://", "https://", "udp+mpegts://", "unix+mpegts://",
    "udp+rtp://", "srt://", "whep://", "wheps://",
)


class PathConfFormError(ValueError):
    pass


def validate_path_name(name: str) -> str:
    name = name.strip().strip("/")
    if not name or not PATH_NAME_RE.match(name):
        raise PathConfFormError(
            "Ungueltiger Stream-Name. Erlaubt sind Buchstaben, Ziffern, '-', '_' und '/' "
            "(z.B. 'kamera1' oder 'cams/eingang')."
        )
    return name


def build_path_conf(form: dict) -> dict:
    conf: dict = {}

    source_type = form.get("source_type", "publisher")
    if source_type == "publisher":
        conf["source"] = "publisher"
    elif source_type == "pull":
        url = (form.get("source_url") or "").strip()
        if not url:
            raise PathConfFormError("Bitte eine Quell-URL angeben.")
        conf["source"] = url
    elif source_type == "redirect":
        target = (form.get("redirect_target") or "").strip()
        if not target:
            raise PathConfFormError("Bitte ein Ziel fuer die Weiterleitung angeben.")
        conf["source"] = "redirect"
        conf["sourceRedirect"] = target
    elif source_type == "rpiCamera":
        conf["source"] = "rpiCamera"
    else:
        raise PathConfFormError(f"Unbekannter Quelltyp: {source_type}")

    if source_type in ("pull",):
        conf["sourceOnDemand"] = form.get("source_on_demand") == "on"

    conf["record"] = form.get("record") == "on"
    if conf["record"]:
        conf["recordFormat"] = form.get("record_format") or "fmp4"
        seg = (form.get("record_segment_duration") or "").strip()
        if seg:
            conf["recordSegmentDuration"] = seg
        delete_after = (form.get("record_delete_after") or "").strip()
        if delete_after:
            conf["recordDeleteAfter"] = delete_after

    max_readers = (form.get("max_readers") or "").strip()
    if max_readers:
        try:
            conf["maxReaders"] = int(max_readers)
        except ValueError as exc:
            raise PathConfFormError("Maximale Leserzahl muss eine Zahl sein.") from exc

    conf["alwaysAvailable"] = form.get("always_available") == "on"
    if conf["alwaysAvailable"]:
        # MediaMTX lehnt 'alwaysAvailable: true' ab, wenn weder
        # 'alwaysAvailableFile' noch eine nicht-leere 'alwaysAvailableTracks'
        # Liste mitgeschickt wird ("'alwaysAvailableTracks' must contain at
        # least one track"). Diese Checkbox bietet dafuer keine eigene
        # Codec-Auswahl, daher ein sinnvoller Default (ein H264-Videotrack,
        # passend zum eingebauten "STREAM IS OFFLINE"-Ersatzbild). Wer eine
        # eigene Ersatzdatei oder andere Codecs braucht, kann das ueber die
        # erweiterten JSON-Parameter mit 'alwaysAvailableFile' bzw. einer
        # eigenen 'alwaysAvailableTracks'-Liste ueberschreiben.
        conf["alwaysAvailableTracks"] = [{"codec": "H264"}]
    conf["overridePublisher"] = form.get("override_publisher", "on") == "on"

    raw_json = (form.get("raw_json_overrides") or "").strip()
    if raw_json:
        try:
            overrides = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise PathConfFormError(f"Ungueltiges JSON in den erweiterten Parametern: {exc}") from exc
        if not isinstance(overrides, dict):
            raise PathConfFormError("Die erweiterten Parameter muessen ein JSON-Objekt sein.")
        conf.update(overrides)

    return conf


def derive_form_defaults(conf: dict | None) -> dict:
    """Best-effort reverse mapping from an existing PathConf back to the
    structured form fields, used to prefill the edit form."""
    conf = conf or {}
    source = conf.get("source") or "publisher"

    if source == "publisher":
        source_type, source_url, redirect_target = "publisher", "", ""
    elif source == "redirect":
        source_type, source_url, redirect_target = "redirect", "", conf.get("sourceRedirect", "")
    elif source == "rpiCamera":
        source_type, source_url, redirect_target = "rpiCamera", "", ""
    else:
        source_type, source_url, redirect_target = "pull", source, ""

    return {
        "source_type": source_type,
        "source_url": source_url,
        "redirect_target": redirect_target,
        "source_on_demand": bool(conf.get("sourceOnDemand")),
        "record": bool(conf.get("record")),
        "record_format": conf.get("recordFormat", "fmp4"),
        "record_segment_duration": conf.get("recordSegmentDuration", ""),
        "record_delete_after": conf.get("recordDeleteAfter", ""),
        "max_readers": conf.get("maxReaders", 0) or "",
        "always_available": bool(conf.get("alwaysAvailable")),
        "override_publisher": conf.get("overridePublisher", True),
    }
