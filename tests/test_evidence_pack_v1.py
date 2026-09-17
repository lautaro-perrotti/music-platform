from copilot.audio.evidence_pack_v1 import build_evidence_pack


def test_pack_is_limited_and_deduped() -> None:
    built = build_evidence_pack(
        project_token="pt",
        audible_token="at",
        project_identity="proj",
        region_id="R1",
        region={"start_qn": 0.0, "end_qn": 32.0},
        main_capture={"ok": True, "signal_class": "SILENCE", "rms": 0.0, "quality": "LIMITED"},
        source_captures=[
            {
                "ok": True,
                "signal_class": "HAS_SIGNAL",
                "ref": {"content_fingerprint": "fp1", "role": "audio"},
                "why_included": "has_material_and_routable",
            }
        ],
        arrangement={"active_count": 1, "eligible_count": 1},
        routing={"main_final": True},
        alignment_claim="EXACT",
        alignment_envelope_ms=52.0,
        lowend={"ok": False, "missing": ["kick", "bass"]},
    )
    pack = built["pack"]
    assert pack["alignment_claim"] == "LIMITED"
    assert pack["alignment_envelope_ms"] == 52.0
    ids = [item["evidence_id"] for item in pack["items"]]
    assert len(ids) == len(set(ids))
    assert any(item["name"] == "main_capture" for item in pack["items"])
    codes = [row["code"] for row in pack["limitations"]]
    assert "ALIGNMENT_LIMITED" in codes
    assert "LOWEND_NOT_JUSTIFIED" in codes
    assert "MIDI_UNREAD" in codes
    assert built["NO HIDDEN ANALYZER CALLS"] is True
    reasons = [row["include"] for row in built["inclusion_reasons"]]
    assert "source_capture:0" in reasons
