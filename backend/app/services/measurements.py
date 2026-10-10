from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.schemas.circuit import Measurement, MeasurementCreate
from app.services.storage import Session, SessionStore
from app.services.units import unit_to_si

MAX_MEASUREMENTS_PER_SESSION = 500


class MeasurementService:
    def __init__(self, store: SessionStore):
        self.store = store

    def record(self, session: Session, data: MeasurementCreate) -> Measurement:
        si_value, si_unit = unit_to_si(data.value, data.unit, data.quantity)
        if data.quantity == "resistance" and si_value < 0:
            raise ValueError("resistance cannot be negative")
        if len(session.measurements) >= MAX_MEASUREMENTS_PER_SESSION:
            raise ValueError(f"measurement limit reached ({MAX_MEASUREMENTS_PER_SESSION} per session)")
        m = Measurement(
            **data.model_dump(),
            id=f"m_{uuid.uuid4().hex[:12]}",
            circuit_id=session.circuit_id,
            recorded_at=datetime.now(timezone.utc),
            si_value=si_value,
            si_unit=si_unit,
        )
        session.measurements.append(m)
        self.store.save(session)
        return m
