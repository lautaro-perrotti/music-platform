from __future__ import annotations

from copilot.producer.parameter_registry import (
    get_device_spec,
    introspect_mix_parameter,
    limiter_ceiling_db_to_normalized,
    limiter_ceiling_normalized_to_db,
    resolve_parameter_index,
    track_volume_db_to_linear,
    track_volume_linear_to_db,
)


def test_track_volume_db_to_linear_roundtrip() -> None:
    db = -6.0
    linear = track_volume_db_to_linear(db)
    assert 0.42 < linear < 0.43
    back = track_volume_linear_to_db(linear)
    assert abs(back - db) < 1e-6


def test_track_volume_db_to_linear_clamps() -> None:
    assert track_volume_db_to_linear(24.0) == 1.0
    assert track_volume_db_to_linear(-200.0) >= 0.0


def test_limiter_ceiling_db_normalized_roundtrip() -> None:
    norm = limiter_ceiling_db_to_normalized(-0.3)
    assert 0.98 < norm < 1.0
    back = limiter_ceiling_normalized_to_db(norm)
    assert abs(back - (-0.3)) < 1e-6


def test_limiter_ceiling_clamps_bounds() -> None:
    assert limiter_ceiling_db_to_normalized(12.0) == 1.0
    assert limiter_ceiling_db_to_normalized(-100.0) == 0.0


def test_resolve_parameter_index_with_spanish_alias() -> None:
    params = [
        {"index": 0, "name": "Ganancia de entrada"},
        {"index": 2, "name": "Techo"},
    ]
    spec = get_device_spec("Limitador")
    assert spec is not None
    pidx = resolve_parameter_index(params, spec.parameters["ceiling"].aliases)
    assert pidx == 2


def test_get_device_spec_matches_english_and_spanish() -> None:
    assert get_device_spec("Limiter") is not None
    assert get_device_spec("Limitador") is not None


def test_typed_parameter_requires_explicit_physical_unit_and_unique_identity() -> None:
    row = {"index": 3, "name": "1 Frequency A", "value": 120.0, "min": 20.0, "max": 20000.0}
    assert introspect_mix_parameter("Eq8", [row], control="frequency", band=1)[1] == "PHYSICAL_UNIT_UNCERTIFIED"
    certified, error = introspect_mix_parameter("Eq8", [{**row, "unit": "Hz"}], control="frequency", band=1)
    assert error is None
    assert (certified.index, certified.unit, certified.value) == (3, "hz", 120.0)
    assert introspect_mix_parameter("Eq8", [{**row, "unit": "Hz"}, {**row, "unit": "Hz", "index": 4}], control="frequency", band=1)[1] == "PARAMETER_NOT_FOUND_OR_AMBIGUOUS"
    assert introspect_mix_parameter("Eq8", [{**row, "unit": "normalized"}], control="frequency", band=1)[0] is None


def test_limiter_ceiling_and_bypass_are_not_inferred_from_names_or_ranges() -> None:
    ceiling = {"index": 1, "name": "Ceiling", "value": -1.0, "min": -36.0, "max": 0.0}
    assert introspect_mix_parameter("Limiter", [ceiling], control="ceiling")[1] == "PHYSICAL_UNIT_UNCERTIFIED"
    assert introspect_mix_parameter("Limiter", [{**ceiling, "unit": "dB"}], control="ceiling")[0].unit == "db"
    assert introspect_mix_parameter("Limiter", [{**ceiling, "name": "Output", "unit": "dB"}], control="ceiling")[0] is None
    on = {"index": 0, "name": "Device On", "value": 1.0, "min": 0.0, "max": 1.0}
    assert introspect_mix_parameter("Limiter", [on], control="enabled")[1] == "PARAMETER_METADATA_UNCERTIFIED"
    assert introspect_mix_parameter("Limiter", [{**on, "is_quantized": True}], control="enabled")[0].unit == "boolean"
