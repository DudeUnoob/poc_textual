from __future__ import annotations

from datetime import datetime
from typing import Generator

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, JSON,
                        String, Text, create_engine, event, inspect, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .config import DATABASE_URL, ensure_directories


class Base(DeclarativeBase):
    pass


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    county: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(80))
    census_year: Mapped[int] = mapped_column(Integer)
    schedule_type: Mapped[str] = mapped_column(String(20), default="population")
    enumeration_district: Mapped[str] = mapped_column(String(80))
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    ground_truth_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    ground_truth_sheet: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ground_truth_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    pages: Mapped[list["Page"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="census")
    metadata_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    import_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    batch: Mapped[Batch] = relationship(back_populates="pages")
    candidates: Mapped[list["FieldCandidate"]] = relationship(back_populates="page", cascade="all, delete-orphan")


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(80), default="schema-v1")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FieldCandidate(Base):
    __tablename__ = "field_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    line_number: Mapped[int] = mapped_column(Integer, index=True)
    field_name: Mapped[str] = mapped_column(String(120), index=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_confidence: Mapped[str] = mapped_column(String(12))
    source_pass: Mapped[str] = mapped_column(String(40))
    row_legibility: Mapped[str] = mapped_column(String(16))
    has_conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    validation_warnings: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=9, index=True)
    status: Mapped[str] = mapped_column(String(24), default="review_required", index=True)
    auto_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    qc_sampled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    page: Mapped[Page] = relationship(back_populates="candidates")
    decisions: Mapped[list["ReviewDecision"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("field_candidates.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(24))
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    candidate: Mapped[FieldCandidate] = relationship(back_populates="decisions")


class CalibrationBand(Base):
    __tablename__ = "calibration_bands"
    id: Mapped[int] = mapped_column(primary_key=True)
    census_year: Mapped[int] = mapped_column(Integer, index=True)
    schedule_type: Mapped[str] = mapped_column(String(20), default="population")
    field_name: Mapped[str] = mapped_column(String(120), index=True)
    confidence: Mapped[str] = mapped_column(String(12))
    total: Mapped[int] = mapped_column(Integer, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def precision(self) -> float:
        return self.correct / self.total if self.total else 0.0


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    directory: Mapped[str] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


ensure_directories()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_settings(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    ensure_directories()
    Base.metadata.create_all(bind=engine)
    columns = {column["name"] for column in inspect(engine).get_columns("batches")}
    if "schedule_type" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE batches ADD COLUMN schedule_type "
                "VARCHAR(20) NOT NULL DEFAULT 'population'"
            ))
    calibration_columns = {
        column["name"]
        for column in inspect(engine).get_columns("calibration_bands")
    }
    if "schedule_type" not in calibration_columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE calibration_bands ADD COLUMN schedule_type "
                "VARCHAR(20) NOT NULL DEFAULT 'population'"
            ))


def session_scope() -> Generator:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
