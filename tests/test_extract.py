import json
import types as _t

import pytest

import extract
from models import ExtractionBatch, Legibility, PersonRecord1950


class _Resp:
    def __init__(self, parsed=None, text=None):
        self.parsed = parsed
        self.text = text


class _Models:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Client:
    def __init__(self, script):
        self.models = _Models(script)


def test_call_gemini_parses_structured_batch():
    batch = ExtractionBatch(records=[PersonRecord1950(line_number=1, surname="Lee")])
    client = _Client([_Resp(parsed=batch)])
    recs = extract.call_gemini(client, "m", "sys", "usr", b"img", "image/jpeg")
    assert recs[0].surname == "Lee"
    assert client.models.calls == 1


def test_pro_model_keeps_required_thinking_when_fast_mode_disables_it(monkeypatch):
    monkeypatch.setattr(extract, "DEFAULT_THINKING_BUDGET", 0)
    config = extract._config("sys", "gemini-3.1-pro-preview")
    assert config.thinking_config.thinking_budget == 1024
    fast = extract._config("sys", "gemini-3.1-flash-lite")
    assert fast.thinking_config.thinking_budget == 0


def test_call_gemini_falls_back_to_text_json():
    payload = json.dumps({"records": [{"line_number": 2, "surname": "Ng"}]})
    client = _Client([_Resp(parsed=None, text=payload)])
    recs = extract.call_gemini(client, "m", "sys", "usr", b"img", "image/jpeg")
    assert recs[0].line_number == 2
    assert recs[0].surname == "Ng"


def test_call_gemini_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda *_: None)
    batch = ExtractionBatch(records=[PersonRecord1950(line_number=1)])
    client = _Client([RuntimeError("429"), _Resp(parsed=batch)])
    recs = extract.call_gemini(client, "m", "sys", "usr", b"img", "image/jpeg")
    assert len(recs) == 1
    assert client.models.calls == 2


def test_call_gemini_reports_attempt_retry_and_success(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda *_: None)
    batch = ExtractionBatch(records=[PersonRecord1950(line_number=1)])
    client = _Client([RuntimeError("temporary 429"), _Resp(parsed=batch)])
    events = []
    extract.call_gemini(
        client, "m", "sys", "usr", b"img", "image/jpeg",
        event_callback=events.append, source_label="crop_1",
    )
    assert [event["type"] for event in events] == [
        "api_attempt", "api_error", "api_attempt", "api_success",
    ]
    assert events[1]["retry_in_seconds"] == 2
    assert events[-1]["source"] == "crop_1"


def test_call_gemini_does_not_report_success_before_json_validation(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda *_: None)
    batch = ExtractionBatch(records=[PersonRecord1950(line_number=1)])
    client = _Client([_Resp(parsed=None, text="{truncated"), _Resp(parsed=batch)])
    events = []
    extract.call_gemini(
        client, "m", "sys", "usr", b"img", "image/jpeg",
        event_callback=events.append,
    )
    assert [event["type"] for event in events] == [
        "api_attempt", "api_error", "api_attempt", "api_success",
    ]
    assert events[1]["next_max_output_tokens"] == extract.DEFAULT_MAX_OUTPUT_TOKENS * 2
    assert events[2]["max_output_tokens"] == extract.DEFAULT_MAX_OUTPUT_TOKENS * 2


def test_call_gemini_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda *_: None)
    client = _Client([RuntimeError("boom")] * extract.MAX_RETRIES_API)
    with pytest.raises(RuntimeError):
        extract.call_gemini(client, "m", "sys", "usr", b"img", "image/jpeg")


def test_call_gemini_does_not_retry_billing_or_credential_failures(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda *_: None)
    client = _Client([RuntimeError("429: prepayment credits are depleted")])
    events = []
    with pytest.raises(RuntimeError, match="after 1 attempt"):
        extract.call_gemini(
            client, "m", "sys", "usr", b"img", "image/jpeg",
            event_callback=events.append,
        )
    assert client.models.calls == 1
    assert events[-1]["retry_in_seconds"] is None


def test_post_process_normalizes_and_propagates_dittos():
    records = [
        {"Surname": "Flores", "Birth Place": "Texas", "Race": "W", "Gender": "M", "Marital Status": "M"},
        {"Surname": '"', "Birth Place": '"', "Race": "Neg", "Gender": "F", "Marital Status": "Wd"},
    ]
    out = extract._post_process(records, 1950)
    assert out[1]["Surname"] == "Flores"          # ditto propagated
    assert out[1]["Birth Place"] == "Texas"       # ditto propagated
    assert out[0]["Race"] == "White"              # normalized
    assert out[1]["Race"] == "Negro (Black)"
    assert out[0]["Gender"] == "Male"
    assert out[0]["Marital Status"] == "Married"
    assert out[1]["Marital Status"] == "Widowed"


def test_missing_candidate_is_low_confidence_even_if_model_claims_high():
    person = PersonRecord1950(line_number=1, surname=None)
    person.field_confidence.surname = "high"
    candidates = extract._review_candidates(
        [person], [{"Line Number": 1, "Surname": None}],
        [{"Line Number": 1, "Surname": None}],
        {"conflict_fields_by_line": {}, "line_sources": {"1": "crop_1"}}, 30,
    )
    surname = next(item for item in candidates if item["field"] == "Surname")
    assert surname["model_confidence"] == "low"


def test_get_client_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        extract.get_client()


def _full_batch(records):
    return _Resp(parsed=ExtractionBatch(records=records))


def test_extract_from_image_skips_crops_when_full_page_complete(monkeypatch):
    monkeypatch.setattr(extract, "load_prompt", lambda year: ("sys", "usr"))
    monkeypatch.setattr(extract, "prepare_full_page", lambda path: (b"img", "image/jpeg"))

    def _boom(*a, **kw):
        raise AssertionError("make_targeted_crop should not be called")
    monkeypatch.setattr(extract, "make_targeted_crop", _boom)

    full_records = [
        PersonRecord1950(line_number=ln, surname=f"S{ln}", legibility=Legibility.clear)
        for ln in range(1, 4)
    ]
    client = _Client([_full_batch(full_records)])

    records, diagnostics = extract.extract_from_image(
        "sheet.jpg", 1950, client=client, expected_lines=3
    )
    assert client.models.calls == 1
    assert diagnostics["crops_used"] == 1
    assert diagnostics["missing_lines"] == []


def test_row_block_strategy_skips_slow_full_page_response(monkeypatch):
    monkeypatch.setattr(extract, "load_prompt", lambda year: ("sys", "usr"))
    monkeypatch.setattr(
        extract, "prepare_full_page",
        lambda path: (_ for _ in ()).throw(AssertionError("full page should not run")),
    )
    crops = [
        extract.Crop(index=i, image_bytes=f"crop{i}".encode(), mime_type="image/jpeg", y_start=i, y_end=i + 1, label=f"crop_{i + 1}")
        for i in range(3)
    ]
    monkeypatch.setattr(extract, "make_row_block_crops", lambda *args, **kwargs: crops)
    client = _Client([
        _full_batch([PersonRecord1950(line_number=1, surname="A")]),
        _full_batch([PersonRecord1950(line_number=2, surname="B")]),
        _full_batch([PersonRecord1950(line_number=3, surname="C")]),
    ])
    records, diagnostics = extract.extract_from_image(
        "sheet.jpg", 1950, client=client, expected_lines=3,
        n_blocks=3, strategy="row_blocks", use_crops=False,
    )
    assert client.models.calls == 3
    assert [record["Line Number"] for record in records] == [1, 2, 3]
    assert diagnostics["missing_lines"] == []


def test_extract_from_image_targets_only_flagged_band(monkeypatch):
    monkeypatch.setattr(extract, "load_prompt", lambda year: ("sys", "usr"))
    monkeypatch.setattr(extract, "prepare_full_page", lambda path: (b"img", "image/jpeg"))

    captured = {}

    def _fake_targeted_crop(image_path, lo, hi, label="retry"):
        captured.setdefault("calls", []).append((lo, hi, label))
        crop_line = 2  # the flagged line lives inside this band
        return extract.Crop(
            index=-1, image_bytes=b"crop", mime_type="image/jpeg", y_start=0, y_end=1, label=label
        )
    monkeypatch.setattr(extract, "make_targeted_crop", _fake_targeted_crop)

    full_records = [
        PersonRecord1950(line_number=1, surname="A", legibility=Legibility.clear),
        PersonRecord1950(line_number=2, surname="B", legibility=Legibility.partial),
        PersonRecord1950(line_number=3, surname="C", legibility=Legibility.clear),
    ]
    crop_records = [PersonRecord1950(line_number=2, surname="B-fixed", legibility=Legibility.clear)]
    client = _Client([_full_batch(full_records), _full_batch(crop_records)])

    records, diagnostics = extract.extract_from_image(
        "sheet.jpg", 1950, client=client, expected_lines=3
    )
    assert client.models.calls == 2  # full page + exactly one targeted crop
    assert len(captured["calls"]) == 1
    assert diagnostics["crops_used"] == 2


def test_extract_from_image_caps_clusters_at_n_blocks(monkeypatch):
    monkeypatch.setattr(extract, "load_prompt", lambda year: ("sys", "usr"))
    monkeypatch.setattr(extract, "prepare_full_page", lambda path: (b"img", "image/jpeg"))

    calls = []

    def _fake_targeted_crop(image_path, lo, hi, label="retry"):
        calls.append(label)
        return extract.Crop(
            index=-1, image_bytes=b"crop", mime_type="image/jpeg", y_start=0, y_end=1, label=label
        )
    monkeypatch.setattr(extract, "make_targeted_crop", _fake_targeted_crop)

    # Scattered gaps across a 30-line page: lines 1, 10, 20, 29 all flagged,
    # far apart from each other -- more than n_blocks=3 clusters would form
    # without the safety cap. Crops come back with the flagged line filled
    # in, and the retry safety-net (also routed through the same mocked
    # make_targeted_crop) is queued but need not fire once crops succeed.
    full_records = [
        PersonRecord1950(line_number=ln, surname=f"S{ln}", legibility=Legibility.clear)
        for ln in range(1, 31) if ln not in (1, 10, 20, 29)
    ]
    recovered = [
        PersonRecord1950(line_number=ln, surname=f"S{ln}", legibility=Legibility.clear)
        for ln in (1, 10, 20, 29)
    ]
    client = _Client(
        [_full_batch(full_records)] + [_full_batch(recovered) for _ in range(3)]
    )

    extract.extract_from_image("sheet.jpg", 1950, client=client, expected_lines=30, n_blocks=3)
    crop_calls = [label for label in calls if label.startswith("crop_")]
    assert len(crop_calls) <= 3


def test_review_sidecar_preserves_raw_value_confidence_and_conflict():
    person = PersonRecord1950(
        line_number=1, surname="Wite", race="W",
        field_confidence={"surname": "low", "race": "high"},
    )
    candidates = extract._review_candidates(
        [person],
        [{"Line Number": 1, "Surname": "Wite", "Race": "W"}],
        [{"Line Number": 1, "Surname": "Wite", "Race": "White"}],
        {"line_sources": {"1": "crop_1"}, "conflict_fields_by_line": {"1": ["surname"]}},
        expected_lines=30,
    )
    surname = next(item for item in candidates if item["field"] == "Surname")
    race = next(item for item in candidates if item["field"] == "Race")
    assert surname["raw_value"] == "Wite"
    assert surname["model_confidence"] == "low"
    assert surname["conflict"] is True
    assert race["normalized_value"] == "White"
    assert race["source_pass"] == "crop_1"
