"""Tests for UtcDateTime custom SQLAlchemy type."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer, create_engine, select
from sqlalchemy.orm import declarative_base, sessionmaker

from orion.infrastructure.persistence.types import UtcDateTime, utc_now

Base = declarative_base()


class SampleModel(Base):
    __tablename__ = "sample_timestamps"

    id = Column(Integer, primary_key=True)
    recorded_at = Column(UtcDateTime, nullable=False)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as sess:
        yield sess


def test_utc_now_returns_timezone_aware_utc():
    now = utc_now()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None
    assert now.tzinfo == UTC


def test_utc_datetime_round_trip(session):
    orig = datetime(2026, 11, 23, 10, 56, 0, 123456, tzinfo=UTC)
    obj = SampleModel(id=1, recorded_at=orig)
    session.add(obj)
    session.commit()

    retrieved = session.scalar(select(SampleModel).where(SampleModel.id == 1))
    assert retrieved is not None
    assert retrieved.recorded_at == orig
    assert retrieved.recorded_at.tzinfo == UTC
    assert retrieved.recorded_at.microsecond == 123456


def test_utc_datetime_normalizes_non_utc_timezone(session):
    # Non-UTC timezone (UTC+05:30)
    ist = timezone(timedelta(hours=5, minutes=30))
    ist_time = datetime(2026, 11, 23, 16, 26, 0, 500000, tzinfo=ist)

    obj = SampleModel(id=2, recorded_at=ist_time)
    session.add(obj)
    session.commit()

    retrieved = session.scalar(select(SampleModel).where(SampleModel.id == 2))
    assert retrieved is not None
    assert retrieved.recorded_at.tzinfo == UTC
    # 16:26 IST - 5:30 = 10:56 UTC
    assert retrieved.recorded_at.hour == 10
    assert retrieved.recorded_at.minute == 56
    assert retrieved.recorded_at.microsecond == 500000


def test_utc_datetime_rejects_naive_datetime(session):
    naive = datetime(2026, 11, 23, 10, 56, 0, 123456)
    type_decorator = UtcDateTime()
    with pytest.raises(ValueError, match="requires timezone-aware datetime"):
        type_decorator.process_bind_param(naive, None)  # type: ignore[arg-type]

    obj = SampleModel(id=3, recorded_at=naive)
    session.add(obj)
    with pytest.raises(Exception) as exc_info:
        session.commit()
    assert "requires timezone-aware datetime" in str(exc_info.value)


def test_utc_datetime_rejects_non_datetime_type(session):
    type_decorator = UtcDateTime()
    with pytest.raises(TypeError, match="Expected datetime object"):
        type_decorator.process_bind_param("2026-11-23T10:56:00Z", None)  # type: ignore[arg-type]

    obj = SampleModel(id=4, recorded_at="2026-11-23T10:56:00Z")  # type: ignore[arg-type]
    session.add(obj)
    with pytest.raises(Exception) as exc_info:
        session.commit()
    assert "Expected datetime object" in str(exc_info.value)


def test_utc_datetime_rejects_invalid_stored_string():
    type_decorator = UtcDateTime()
    with pytest.raises(ValueError, match="Invalid ISO 8601 timestamp"):
        type_decorator.process_result_value("not-a-timestamp", None)  # type: ignore[arg-type]
