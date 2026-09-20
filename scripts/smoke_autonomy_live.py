from __future__ import annotations

import json
from pathlib import Path

from copilot.daw.ableton_tcp import AbletonTcpAdapter
from copilot.schemas.session import MidiNote
from copilot.producer.chain_ops import ensure_chain, ensure_order
from copilot.producer.soniq_surface import apply_patch_contract


def first_relative_sample() -> str | None:
    idx_path = Path("logs/sample_library_index.json")
    if not idx_path.is_file():
        return None
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    for v in (idx.get("assets") or {}).values():
        rp = v.get("relative_path")
        if rp and rp.lower().endswith(".wav"):
            return rp
    return None


def run() -> dict:
    rep = {"ok": True, "scenarios": []}

    def add(name: str, ok: bool, **data):
        rep["scenarios"].append({"name": name, "ok": bool(ok), **data})
        if not ok:
            rep["ok"] = False

    daw = AbletonTcpAdapter()
    daw.connect()
    try:
        s = daw.snapshot()
        sample = first_relative_sample()

        # Scenario 1: MIDI autonomy
        midi_name = "ASTRAL_SMOKE_MIDI"
        if s.track_by_name(midi_name) is None:
            daw.create_midi_track(midi_name)
        s = daw.snapshot()
        mt = s.track_by_name(midi_name)
        ensure_chain(daw, session=s, track_name=midi_name, devices=["Arpeggiator", "Auto Filter"])
        ensure_order(daw, session=daw.snapshot(), track_name=midi_name, desired_order=["Arpeggiator", "Auto Filter"])
        try:
            daw.delete_clip(int(mt.index), 0)
        except Exception:
            pass
        daw.create_midi_clip(int(mt.index), 0, 4.0)
        daw.replace_clip_notes(
            int(mt.index),
            0,
            [
                MidiNote(pitch=36, start_time=0.0, duration=0.5, velocity=105),
                MidiNote(pitch=36, start_time=1.0, duration=0.5, velocity=95),
                MidiNote(pitch=36, start_time=2.0, duration=0.5, velocity=110),
                MidiNote(pitch=36, start_time=3.0, duration=0.5, velocity=100),
            ],
        )
        p1 = apply_patch_contract(
            daw,
            session=daw.snapshot(),
            contract={
                "track": midi_name,
                "device": "Arpeggiator",
                "writes": [
                    {"name": "Style", "value": 0.4},
                    {"name": "Repeats", "value": 0.8},
                ],
                "constraints": {"max_delta_norm": 0.5, "max_writes": 8, "forbid_device_on_toggle": True},
            },
            throttle_ms=40,
        )
        add("midi_fx_batch", ok=bool(p1.get("ok")), contract=p1)

        # Scenario 2: Audio autonomy
        audio_name = "ASTRAL_SMOKE_AUDIO"
        if daw.snapshot().track_by_name(audio_name) is None:
            daw.create_audio_track(audio_name)
        s = daw.snapshot()
        at = s.track_by_name(audio_name)
        ensure_chain(daw, session=s, track_name=audio_name, devices=["Compressor", "EQ Eight"])
        ensure_order(daw, session=daw.snapshot(), track_name=audio_name, desired_order=["Compressor", "EQ Eight"])
        if sample:
            daw.load_browser_item(int(at.index), sample, clip_index=0)
        p2 = apply_patch_contract(
            daw,
            session=daw.snapshot(),
            contract={
                "track": audio_name,
                "device": "Compressor",
                "writes": [
                    {"name": "Threshold", "value": 0.35},
                    {"name": "Ratio", "value": 0.55},
                    {"name": "Release", "value": 0.5},
                ],
                "constraints": {"max_delta_norm": 0.4, "max_writes": 8, "forbid_device_on_toggle": True},
            },
            throttle_ms=40,
        )
        add("audio_fx_batch", ok=bool(p2.get("ok")), sample_loaded=bool(sample), contract=p2)

        # Scenario 3: Cross-track autonomous pass
        p3a = apply_patch_contract(
            daw,
            session=daw.snapshot(),
            contract={
                "track": midi_name,
                "device": "Auto Filter",
                "writes": [
                    {"name": "Frequency", "value": 0.42},
                    {"name": "Resonance", "value": 0.28},
                ],
                "constraints": {"max_delta_norm": 0.25, "max_writes": 6, "forbid_device_on_toggle": True},
            },
            throttle_ms=40,
        )
        p3b = apply_patch_contract(
            daw,
            session=daw.snapshot(),
            contract={
                "track": audio_name,
                "device": "EQ Eight",
                "writes": [
                    {"name": "1 Gain A", "value": 0.46},
                    {"name": "2 Gain A", "value": 0.52},
                ],
                "constraints": {"max_delta_norm": 0.2, "max_writes": 6, "forbid_device_on_toggle": True},
            },
            throttle_ms=40,
        )
        add("cross_track_patch_contracts", ok=bool(p3a.get("ok") and p3b.get("ok")), midi=p3a, audio=p3b)

        # final verify
        sf = daw.snapshot()
        mtf = sf.track_by_name(midi_name)
        atf = sf.track_by_name(audio_name)
        add(
            "final_verify",
            ok=bool(mtf and atf and len(mtf.clips) >= 1 and len(atf.clips) >= 1),
            midi_devices=[d.name for d in (mtf.devices if mtf else [])],
            audio_devices=[d.name for d in (atf.devices if atf else [])],
            midi_clips=len(mtf.clips) if mtf else 0,
            audio_clips=len(atf.clips) if atf else 0,
        )

    finally:
        daw.disconnect()

    out_dir = Path("logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "autonomy_smoke_live.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return rep


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
