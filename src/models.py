"""
Typed extraction contracts for the Gemini census OCR pipeline.

These Pydantic models are used two ways:
1. As the Gemini ``response_schema`` so the model returns schema-valid JSON
   (no markdown fences, no structural drift).
2. As the internal representation the rest of the pipeline reconciles,
   validates, and normalizes.

Design notes:
- Every field is optional/nullable. A blank census cell is a real signal
  (vacant dwelling, no occupation, etc.), so ``None`` must be representable.
- ``raw_*`` transcriptions are kept separate from normalized values. The
  research team's ground truth applies a controlled vocabulary that is NOT
  always visible on the form (see CLAUDE.md), so we never overwrite what the
  enumerator actually wrote.
- ``legibility`` lets the model flag a guessed/unclear row instead of emitting
  a confident hallucination. Low-confidence lines drive the targeted retry.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Legibility(str, Enum):
    """Model's self-reported confidence for a single transcribed line."""

    clear = "clear"
    partial = "partial"
    illegible = "illegible"


class PersonRecord1950(BaseModel):
    """One line (person) on a 1950 Form P1 population schedule.

    Field names deliberately mirror the ground-truth XLSX column names so that
    downstream comparison in ``compare.py`` can align without a translation
    layer. Do not rename these without updating COMPARE_FIELDS_1950.
    """

    line_number: int | None = Field(
        default=None,
        description="Printed line number in the far-left margin (1-30). Must match the form exactly.",
    )
    street_name: str | None = Field(default=None, description="Street name; may be written once per block in the margin.")
    house_number: int | None = Field(default=None, description="House number in the street column. NOT the dwelling serial.")
    dwelling_number: int | None = Field(default=None, description="Serial number of dwelling unit (small integer).")
    surname: str | None = Field(default=None, description="Surname exactly as written. Resolve ditto marks to the value above.")
    given_name: str | None = Field(default=None, description="Given name(s) exactly as written.")
    relation_to_head: str | None = Field(default=None, description="Relation to head of household (Head, Wife, Son, Boarder, etc.).")
    race: str | None = Field(default=None, description="Literal race mark on the form (W, Neg, etc.). Do NOT expand or map.")
    gender: str | None = Field(default=None, description="Male or Female.")
    age: int | None = Field(default=None, description="Age in years as written.")
    marital_status: str | None = Field(default=None, description="Literal marital mark (Mar, Wd, D, Sep, S, Nev) or null.")
    birth_place: str | None = Field(default=None, description="Birthplace as written. Resolve ditto marks.")
    occupation: str | None = Field(default=None, description="Occupation as written, or null if blank.")
    industry: str | None = Field(default=None, description="Industry as written, or null if blank.")
    worker_class: str | None = Field(default=None, description="Class of worker (P, G, O, NP) or null.")
    legibility: Legibility = Field(
        default=Legibility.clear,
        description="clear if confidently read; partial if some fields guessed; illegible if the line could not be read.",
    )


# Maps the schema-safe snake_case field names above to the exact ground-truth
# XLSX column names used by compare.py / utils.py. Single source of truth so
# extraction and comparison never drift apart.
FIELD_TO_GT_COLUMN_1950: dict[str, str] = {
    "line_number": "Line Number",
    "street_name": "Street Name",
    "house_number": "House Number",
    "dwelling_number": "Dwelling Number",
    "surname": "Surname",
    "given_name": "Given Name",
    "relation_to_head": "Relation to Head of House",
    "race": "Race",
    "gender": "Gender",
    "age": "Age",
    "marital_status": "Marital Status",
    "birth_place": "Birth Place",
    "occupation": "Occupation",
    "industry": "Industry",
    "worker_class": "Worker Class",
}


class ExtractionBatch(BaseModel):
    """A batch of person records returned for one image or crop."""

    records: list[PersonRecord1950] = Field(default_factory=list)


class LineDiagnostic(BaseModel):
    """Per-line reconciliation/validation outcome for auditability."""

    line_number: int
    source: str = Field(description="Which crop/pass produced the winning record (e.g. 'crop_2', 'retry').")
    legibility: Legibility = Legibility.clear
    duplicate_of_crops: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PageDiagnostics(BaseModel):
    """Coverage and quality diagnostics for a whole extracted page."""

    expected_lines: list[int] = Field(default_factory=list)
    extracted_lines: list[int] = Field(default_factory=list)
    missing_lines: list[int] = Field(default_factory=list)
    duplicate_lines: list[int] = Field(default_factory=list)
    out_of_range_lines: list[int] = Field(default_factory=list)
    illegible_lines: list[int] = Field(default_factory=list)
    retry_attempted: bool = False
    retry_recovered_lines: list[int] = Field(default_factory=list)
    crops_used: int = 1

    @property
    def coverage(self) -> float:
        if not self.expected_lines:
            return 0.0
        found = len(set(self.expected_lines) & set(self.extracted_lines))
        return found / len(self.expected_lines)


def to_gt_record(person: PersonRecord1950, gt_columns: dict[str, str] = FIELD_TO_GT_COLUMN_1950) -> dict:
    """Convert a typed person record to a ground-truth-column-keyed dict.

    Keeps only fields that have a GT column mapping; ``line_number`` becomes the
    "Line Number" key that compare.py joins on. ``None`` values are preserved so
    blanks stay blanks.
    """
    dumped = person.model_dump()
    out: dict = {}
    for field_name, gt_col in gt_columns.items():
        out[gt_col] = dumped.get(field_name)
    return out
