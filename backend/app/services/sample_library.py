"""Bundled sample circuits (reference netlist + drawn schematic + exact annotations)."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.schemas.common import SampleCircuitInfo

logger = logging.getLogger(__name__)


@dataclass
class SampleCircuit:
    id: str
    name: str
    description: str
    directory: Path
    netlist_path: Path
    schematic_path: Path
    pdf_path: Path | None
    expected_behavior: str
    example_questions: list[str]
    test_questions: list[dict[str, Any]]
    annotations: dict[str, Any] = field(default_factory=dict)
    circuit_type: str = "unknown"

    def info(self) -> SampleCircuitInfo:
        return SampleCircuitInfo(
            id=self.id,
            name=self.name,
            description=self.description,
            circuit_type=self.circuit_type,
            example_questions=self.example_questions,
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SampleLibrary:
    def __init__(self, directory: Path):
        self.samples: dict[str, SampleCircuit] = {}
        self._hashes: dict[str, tuple[str, str]] = {}
        if not directory.exists():
            logger.warning("sample circuit directory %s does not exist", directory)
            return
        for meta_path in sorted(directory.glob("*/circuit.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                d = meta_path.parent
                ann_path = d / "annotations.json"
                sample = SampleCircuit(
                    id=meta["id"],
                    name=meta["name"],
                    description=meta["description"],
                    directory=d,
                    netlist_path=d / meta["netlist"],
                    schematic_path=d / meta["schematic"],
                    pdf_path=(d / meta["schematic_pdf"]) if meta.get("schematic_pdf") else None,
                    expected_behavior=meta.get("expected_behavior", ""),
                    example_questions=meta.get("example_questions", []),
                    test_questions=meta.get("test_questions", []),
                    annotations=json.loads(ann_path.read_text(encoding="utf-8")) if ann_path.exists() else {},
                )
            except (KeyError, json.JSONDecodeError, OSError) as exc:
                logger.warning("skipping invalid sample %s: %s", meta_path, exc)
                continue
            self.samples[sample.id] = sample
            if sample.schematic_path.exists():
                self._hashes[_sha256(sample.schematic_path)] = (sample.id, "png")
            if sample.pdf_path and sample.pdf_path.exists():
                self._hashes[_sha256(sample.pdf_path)] = (sample.id, "pdf")

    def list(self) -> list[SampleCircuit]:
        return list(self.samples.values())

    def get(self, sample_id: str) -> SampleCircuit | None:
        return self.samples.get(sample_id)

    def match_upload(self, data: bytes) -> tuple[SampleCircuit, str] | None:
        """Exact byte match against a bundled sample file (used only in demo mode)."""
        hit = self._hashes.get(hashlib.sha256(data).hexdigest())
        if hit is None:
            return None
        return self.samples[hit[0]], hit[1]
