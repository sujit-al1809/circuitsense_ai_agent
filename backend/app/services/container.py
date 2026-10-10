"""Wires services together according to configuration (dependency container)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings
from app.schemas.common import ProviderInfo
from app.services.agent import AgentOrchestrator, AgentPolicy, BedrockAgentPolicy
from app.services.analysis_service import AnalysisService
from app.services.bedrock_service import BedrockService
from app.services.circuit_graph import CircuitGraph
from app.services.circuit_parser import parse_netlist
from app.services.demo_agent import DemoAgentPolicy
from app.services.diagnostics import build_insight
from app.services.measurements import MeasurementService
from app.services.retrieval_service import RetrievalBackend, build_retrieval_backend
from app.services.sample_library import SampleLibrary
from app.services.simulation_service import SimulationService
from app.services.storage import InMemorySessionStore, NoopUploadArchive, S3UploadArchive, SessionStore, UploadArchive
from app.services.vision_service import BedrockVisionAnalyzer, UnavailableVisionAnalyzer, VisionAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    store: SessionStore
    samples: SampleLibrary
    retrieval: RetrievalBackend
    simulation: SimulationService
    measurements: MeasurementService
    analysis: AnalysisService
    orchestrator: AgentOrchestrator | None
    bedrock: BedrockService | None
    archive: UploadArchive

    def provider_info(self) -> ProviderInfo:
        s = self.settings
        issues = s.provider_config_issues()
        return ProviderInfo(
            mode=s.provider_mode,
            label="Demo mode (deterministic, no AI model)" if s.provider_mode == "demo" else "Amazon Bedrock",
            configured=not issues,
            region=s.aws_region if s.provider_mode == "bedrock" else None,
            text_model_id=s.bedrock_text_model_id if s.provider_mode == "bedrock" else None,
            vision_model_id=s.effective_vision_model_id if s.provider_mode == "bedrock" else None,
            issues=issues,
            notes=s.provider_config_notes(),
        )


def _annotate_sample_types(samples: SampleLibrary) -> None:
    for sample in samples.list():
        try:
            parsed = parse_netlist(sample.netlist_path.read_text(encoding="utf-8"))
            sample.circuit_type = build_insight(CircuitGraph(parsed.components, parsed.nets)).circuit_type
        except Exception as exc:
            logger.warning("could not classify sample %s: %s", sample.id, exc)


def build_services(settings: Settings, *, bedrock: BedrockService | None = None, policy: AgentPolicy | None = None, s3_client=None) -> Services:
    store = InMemorySessionStore(settings.session_max_count)
    samples = SampleLibrary(settings.sample_circuit_dir)
    _annotate_sample_types(samples)
    retrieval = build_retrieval_backend(settings.retrieval_backend, settings.resolved_datasheet_dir)
    simulation = SimulationService(settings)
    measurements = MeasurementService(store)

    archive: UploadArchive = NoopUploadArchive()
    if settings.s3_bucket:
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3", region_name=settings.aws_region)
        archive = S3UploadArchive(settings.s3_bucket, settings.s3_prefix, s3_client)

    vision: VisionAnalyzer = UnavailableVisionAnalyzer()
    orchestrator_policy: AgentPolicy | None = policy
    if settings.provider_mode == "bedrock":
        if not settings.provider_config_issues():
            bedrock = bedrock or BedrockService(settings)
            vision = BedrockVisionAnalyzer(bedrock, settings.effective_vision_model_id or "")
            orchestrator_policy = orchestrator_policy or BedrockAgentPolicy(bedrock, settings.bedrock_text_model_id or "")
        else:
            logger.error("Bedrock mode is misconfigured: %s", "; ".join(settings.provider_config_issues()))
    else:
        orchestrator_policy = orchestrator_policy or DemoAgentPolicy()

    analysis = AnalysisService(settings, store, samples, vision, archive)
    orchestrator = (
        AgentOrchestrator(settings, orchestrator_policy, retrieval, simulation, measurements)
        if orchestrator_policy is not None
        else None
    )
    return Services(
        settings=settings,
        store=store,
        samples=samples,
        retrieval=retrieval,
        simulation=simulation,
        measurements=measurements,
        analysis=analysis,
        orchestrator=orchestrator,
        bedrock=bedrock,
        archive=archive,
    )
