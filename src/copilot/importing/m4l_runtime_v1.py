"""M4L_RUNTIME_PROVISIONING_V1 — install Copilot Audio Tap before Live launch."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.daw.detect import detect_ableton, default_user_library_candidates
from copilot.human_eval.store import now_iso
from copilot.platform.system import default_capture_dir
from devices.build_amxd import build_audio_effect_amxd_bytes

MILESTONE = "M4L_RUNTIME_PROVISIONING_V1"
DEVICE_NAME = "Copilot Audio Tap"
DEVICE_ALIAS_V4 = "Copilot Audio Tap 4"
EXPECTED_TAP_PROTOCOL = 3
ARTIFACT = "m4l_runtime_provisioning_v1.json"
MANIFEST_NAME = "copilot_m4l_runtime.json"
CANONICAL_SOURCE = (
    Path(__file__).resolve().parents[3] / "devices" / "Copilot Audio Tap.amxd"
)
CANONICAL_MAXPAT = CANONICAL_SOURCE.with_name("Copilot Audio Tap.maxpat")
CAPTURE_DIR_TOKEN = b"__COPILOT_CAPTURE_DIR__"
RUNTIME_REL = Path("Presets") / "Audio Effects" / "Max Audio Effect" / "Copilot"
LEGACY_REL = Path("Presets") / "Audio Effects" / "Max Audio Effect" / "Copilot Audio Tap.amxd"
BROWSER_RESOLUTION_TIMEOUT_S = 15.0
BROWSER_RESOLUTION_POLL_S = 0.25


def canonical_tap_asset(source: Path | None = None) -> dict[str, Any]:
    path = source or CANONICAL_SOURCE
    if not path.is_file():
        return {
            "milestone": MILESTONE,
            "status": "CANONICAL_ASSET_MISSING",
            "asset_path": str(path),
        }
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "milestone": MILESTONE,
        "status": "VERIFIED",
        "device_name": DEVICE_NAME,
        "tap_protocol_expected": EXPECTED_TAP_PROTOCOL,
        "asset_path": str(path.resolve()),
        "sha256": digest,
        "size": path.stat().st_size,
        "version": digest[:16],
    }


def _configured_tap_asset(
    *,
    template: dict[str, Any],
    capture_root: Path,
) -> tuple[dict[str, Any], bytes]:
    """Build a host-specific device from the portable patcher template."""
    if not CANONICAL_MAXPAT.is_file():
        raise FileNotFoundError(CANONICAL_MAXPAT)
    patcher = CANONICAL_MAXPAT.read_bytes()
    if CAPTURE_DIR_TOKEN not in patcher:
        raise ValueError("Copilot Audio Tap patcher has no capture-dir token")
    root = capture_root.expanduser().resolve().as_posix().replace(" ", "\\ ")
    configured_patcher = patcher.replace(CAPTURE_DIR_TOKEN, root.encode("utf-8"))
    payload = build_audio_effect_amxd_bytes(configured_patcher)
    digest = hashlib.sha256(payload).hexdigest()
    return (
        {
            **template,
            "source": str(CANONICAL_MAXPAT.resolve()),
            "sha256": digest,
            "version": digest[:16],
            "capture_dir": str(capture_root.resolve()),
            "configured_for_host": True,
            "template_sha256": template.get("sha256"),
        },
        payload,
    )


def discover_user_library(prefs_root: str | Path | None = None) -> dict[str, Any]:
    prefs = Path(prefs_root) if prefs_root else None
    if prefs is None:
        detection = detect_ableton(include_start_menu=False)
        prefs = Path(detection.prefs_root) if detection.prefs_root else None
    cfg = None
    if prefs is not None:
        nested = prefs / "Preferences" / "Library.cfg"
        flat = prefs / "Library.cfg"
        cfg = nested if nested.is_file() else flat if flat.is_file() else None
    if cfg is not None:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        project_path = _xml_attr(text, "ProjectPath")
        project_name = _xml_attr(text, "ProjectName") or "User Library"
        if project_path:
            base = Path(project_path)
            named = base / project_name if base.name.lower() != project_name.lower() else base
            for candidate, rule in (
                (named, "library_cfg_project"),
                (base / "User Library", "library_cfg_user_library_child"),
                (base, "library_cfg_project_path"),
            ):
                if _looks_like_user_library(candidate):
                    return {
                        "status": "VERIFIED",
                        "user_library": str(candidate.resolve()),
                        "rule": rule,
                        "config": str(cfg),
                    }
    for default in default_user_library_candidates():
        if _looks_like_user_library(default):
            return {
                "status": "VERIFIED",
                "user_library": str(default.resolve()),
                "rule": "documented_default",
            }
    return {
        "status": "USER_LIBRARY_UNRESOLVED",
        "user_library": None,
        "detail": "No authoritative or existing default User Library.",
    }


def runtime_paths(user_library: str | Path) -> dict[str, Path]:
    root = Path(user_library)
    folder = root / RUNTIME_REL
    return {
        "folder": folder,
        "device": folder / f"{DEVICE_NAME}.amxd",
        "manifest": folder / MANIFEST_NAME,
        "legacy": root / LEGACY_REL,
    }


def ensure_m4l_runtime(
    *,
    user_library: str | Path | None = None,
    source: Path | None = None,
    evidence: Path | None = None,
) -> dict[str, Any]:
    template = canonical_tap_asset(source)
    if template.get("status") != "VERIFIED":
        report = {
            **template,
            "status": "BLOCKED",
            "M4L_RUNTIME_PROVISIONING_V1": "BLOCKED",
            "reason": template.get("status"),
        }
        _persist(report, evidence)
        return report
    if user_library is None:
        library = discover_user_library()
        if library.get("status") != "VERIFIED":
            report = {
                "milestone": MILESTONE,
                "status": "BLOCKED",
                "M4L_RUNTIME_PROVISIONING_V1": "BLOCKED",
                "reason": "USER_LIBRARY_UNRESOLVED",
                "asset": asset,
                "library": library,
            }
            _persist(report, evidence)
            return report
        library_root = Path(str(library["user_library"]))
    else:
        library_root = Path(user_library)
        if not _looks_like_user_library(library_root) and not library_root.exists():
            library_root.mkdir(parents=True, exist_ok=True)
        library = {
            "status": "VERIFIED",
            "user_library": str(library_root),
            "rule": "explicit",
        }

    try:
        asset, payload = _configured_tap_asset(
            template=template,
            capture_root=default_capture_dir(),
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        report = {
            "milestone": MILESTONE,
            "status": "BLOCKED",
            "M4L_RUNTIME_PROVISIONING_V1": "BLOCKED",
            "reason": "CAPTURE_PATH_CONFIGURATION_FAILED",
            "detail": str(exc),
            "asset": template,
            "library": library,
        }
        _persist(report, evidence)
        return report

    paths = runtime_paths(library_root)
    dest = paths["device"]
    alias = dest.with_name(f"{DEVICE_ALIAS_V4}.amxd")
    manifest_path = paths["manifest"]
    expected = str(asset["sha256"])
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.is_file():
        current = hashlib.sha256(dest.read_bytes()).hexdigest()
        owned = _copilot_owned(manifest_path)
        if current == expected:
            status = "ALREADY_CURRENT"
        elif owned:
            _atomic_write_bytes(payload, dest)
            status = "UPDATED"
        else:
            report = {
                "milestone": MILESTONE,
                "status": "BLOCKED",
                "M4L_RUNTIME_PROVISIONING_V1": "BLOCKED",
                "reason": "USER_OWNED_CONFLICT",
                "detail": "Refusing to overwrite an unmanaged Copilot Audio Tap.amxd.",
                "dest": str(dest),
                "asset": asset,
                "library": library,
            }
            _persist(report, evidence)
            return report
    else:
        _atomic_write_bytes(payload, dest)
        status = "INSTALLED"

    _retire_legacy_duplicate(paths["legacy"], expected)
    _provision_identical_alias_bytes(payload, alias, expected)
    _retire_probe_aliases(dest.parent)
    manifest = {
        "milestone": MILESTONE,
        "device_name": DEVICE_NAME,
        "tap_protocol_expected": EXPECTED_TAP_PROTOCOL,
        "sha256": expected,
        "version": asset["version"],
        "source": asset["source"],
        "installed": str(dest),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "COPILOT_OWNED": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = {
        "milestone": MILESTONE,
        "status": status,
        "M4L_RUNTIME_PROVISIONING_V1": "VERIFIED",
        "ts": now_iso(),
        "asset": asset,
        "library": library,
        "installed": str(dest),
        "sha256": expected,
        "version": asset["version"],
        "M4L_ASSET_ON_DISK": "VERIFIED" if dest.is_file() else "BLOCKED",
        "IDEMPOTENT": status == "ALREADY_CURRENT",
    }
    _persist(report, evidence)
    return report


def _normalized_tap_name(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    if name.lower().endswith(".amxd"):
        name = name[:-5].strip()
    return name


def item_is_canonical_tap(item: dict[str, Any]) -> bool:
    name = _normalized_tap_name(item)
    return name in {DEVICE_NAME, DEVICE_ALIAS_V4} and bool(item.get("is_loadable"))


def find_canonical_tap_uri(daw: AbletonTcpAdapter) -> str | None:
    found: list[tuple[str, str]] = []
    for path in (
        ["user_library", "Presets", "Audio Effects", "Max Audio Effect", "Copilot"],
        ["user_library", "Presets", "Audio Effects", "Max Audio Effect"],
        ["audio_effects", "Max Audio Effect", "Copilot"],
        ["audio_effects", "Max Audio Effect"],
        ["maxforlive"],
        ["user_library"],
        ["audio_effects"],
    ):
        listing = daw.browse_path(list(path))
        for item in listing.get("items") or []:
            if item_is_canonical_tap(item):
                uri = item.get("uri")
                if uri:
                    found.append((_normalized_tap_name(item), str(uri)))
        if found:
            break
    if not found:
        searched = daw.search_browser(DEVICE_NAME, "audio_effects")
        for item in searched.get("results") or []:
            if item_is_canonical_tap(item):
                uri = item.get("uri")
                if uri:
                    found.append((_normalized_tap_name(item), str(uri)))
    for name, uri in found:
        if name == DEVICE_NAME:
            return uri
    return found[0][1] if found else None


def verify_live_browser(
    daw: AbletonTcpAdapter,
    *,
    wait: bool = False,
) -> dict[str, Any]:
    deadline = time.monotonic() + (
        BROWSER_RESOLUTION_TIMEOUT_S if wait else 0.0
    )
    uri = None
    while True:
        uri = find_canonical_tap_uri(daw)
        if uri:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(BROWSER_RESOLUTION_POLL_S, remaining))
    return {
        "milestone": MILESTONE,
        "LIVE_BROWSER_RESOLUTION": "VERIFIED" if uri else "BLOCKED",
        "uri": uri,
        "waited": wait,
    }


def _looks_like_user_library(path: Path) -> bool:
    if not path.is_dir():
        return False
    if path.name.lower() == "user library":
        return True
    names = {child.name.lower() for child in path.iterdir()}
    return "presets" in names or "ableton folder info" in names


def _xml_attr(text: str, tag: str) -> str | None:
    match = re.search(rf'<{tag}\s+Value="([^"]*)"', text)
    return match.group(1) if match else None


def _copilot_owned(manifest_path: Path) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("COPILOT_OWNED")) and payload.get("device_name") == DEVICE_NAME


def _atomic_copy(source: Path, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    shutil.copy2(source, tmp)
    tmp.replace(dest)


def _atomic_write_bytes(payload: bytes, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(dest)


def _provision_identical_alias(source: Path, alias: Path, expected_sha: str) -> None:
    """Same bytes, different User Library URI. Live caches compiled M4L by URI."""
    if alias.is_file() and hashlib.sha256(alias.read_bytes()).hexdigest() == expected_sha:
        return
    _atomic_copy(source, alias)


def _provision_identical_alias_bytes(payload: bytes, alias: Path, expected_sha: str) -> None:
    """Install the same host-configured bytes under Live's cache-busting URI."""
    if alias.is_file() and hashlib.sha256(alias.read_bytes()).hexdigest() == expected_sha:
        return
    _atomic_write_bytes(payload, alias)


def _retire_probe_aliases(folder: Path) -> None:
    if not folder.is_dir():
        return
    for leftover in folder.glob("Copilot Audio Tap *Probe.amxd"):
        leftover.unlink()


def _retire_legacy_duplicate(legacy: Path, expected_sha: str) -> None:
    if not legacy.is_file():
        return
    digest = hashlib.sha256(legacy.read_bytes()).hexdigest()
    if digest == expected_sha:
        legacy.unlink()


def _persist(report: dict[str, Any], evidence: Path | None) -> None:
    if evidence is None:
        return
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / ARTIFACT).write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
