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
from functools import lru_cache

from pydantic import BaseModel, Field, create_model

from census_schemas import field_mapping, get_census_schema


class Legibility(str, Enum):
    """Model's self-reported confidence for a single transcribed line."""

    clear = "clear"
    partial = "partial"
    illegible = "illegible"


class FieldConfidence(str, Enum):
    """Self-reported certainty for one value, kept separate from legibility."""

    high = "high"
    medium = "medium"
    low = "low"


class FieldConfidence1950(BaseModel):
    """Fixed-shape confidence object accepted by the Gemini Developer API.

    A ``dict[str, FieldConfidence]`` becomes JSON Schema
    ``additionalProperties``, which is unsupported by the Developer API.
    Keep these names aligned with ``PersonRecord1950``.
    """

    street_name: FieldConfidence | None = None
    house_number: FieldConfidence | None = None
    dwelling_number: FieldConfidence | None = None
    surname: FieldConfidence | None = None
    given_name: FieldConfidence | None = None
    relation_to_head: FieldConfidence | None = None
    race: FieldConfidence | None = None
    gender: FieldConfidence | None = None
    age: FieldConfidence | None = None
    marital_status: FieldConfidence | None = None
    birth_place: FieldConfidence | None = None
    occupation: FieldConfidence | None = None
    industry: FieldConfidence | None = None
    worker_class: FieldConfidence | None = None


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
    field_confidence: FieldConfidence1950 = Field(
        default_factory=FieldConfidence1950,
        description=(
            "Per-field confidence using the fixed schema field names. Include "
            "every non-blank field: high only when "
            "the handwriting is unambiguous, medium for a plausible read, low "
            "for a guess."
        ),
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
    line_sources: dict[str, str] = Field(default_factory=dict)
    conflict_fields_by_line: dict[str, list[str]] = Field(default_factory=dict)

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


@lru_cache(maxsize=None)
def get_year_models(
    year: int,
    schedule_type: str = "population",
) -> tuple[type[BaseModel], type[BaseModel], dict[str, str]]:
    """Build Gemini-compatible fixed-shape models for one census form."""
    if year == 1950 and schedule_type == "population":
        return PersonRecord1950, ExtractionBatch, FIELD_TO_GT_COLUMN_1950

    schema = get_census_schema(year, schedule_type)
    gt_columns = field_mapping(schema)
    confidence_model = create_model(
        f"FieldConfidence{year}{schedule_type.title()}",
        __base__=BaseModel,
        **{
            field_name: (FieldConfidence | None, None)
            for field_name in gt_columns
        },
    )
    record_model = create_model(
        f"PersonRecord{year}{schedule_type.title()}",
        __base__=BaseModel,
        line_number=(
            int | None,
            Field(
                default=None,
                description="Physical-form row number used to align extraction passes.",
            ),
        ),
        **{
            field_name: (
                str | int | None,
                Field(default=None, description=f"{column} exactly as written."),
            )
            for field_name, column in gt_columns.items()
        },
        legibility=(Legibility, Field(default=Legibility.clear)),
        field_confidence=(
            confidence_model,
            Field(default_factory=confidence_model),
        ),
    )
    batch_model = create_model(
        f"ExtractionBatch{year}{schedule_type.title()}",
        __base__=BaseModel,
        records=(list[record_model], Field(default_factory=list)),
    )
    return record_model, batch_model, gt_columns


def to_year_gt_record(
    person: BaseModel,
    year: int,
    schedule_type: str = "population",
) -> dict:
    """Map a typed record to exact ground-truth spreadsheet headings."""
    schema = get_census_schema(year, schedule_type)
    _, _, gt_columns = get_year_models(year, schedule_type)
    dumped = person.model_dump()
    output = {
        column: dumped.get(field_name)
        for field_name, column in gt_columns.items()
    }
    if schema.has_ground_truth_line_number:
        output["Line Number"] = dumped.get("line_number")
    else:
        output["_line_number"] = dumped.get("line_number")
    return output
