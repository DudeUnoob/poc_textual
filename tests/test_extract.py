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


def test_call_gemini_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda *_: None)
    client = _Client([RuntimeError("boom")] * extract.MAX_RETRIES_API)
    with pytest.raises(RuntimeError):
        extract.call_gemini(client, "m", "sys", "usr", b"img", "image/jpeg")


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
