"""Thin wrapper around the Amazon Bedrock Converse API (boto3 ``bedrock-runtime``).

* Model IDs and region come from configuration – nothing is hardcoded.
* ``check_models`` verifies that the configured models exist, accept the required
  input modalities and are invocable, before the app relies on them.
* boto3 errors are mapped to ``ProviderError`` with actionable messages.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.schemas.common import ModelCheck

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "provider_error", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


_ERROR_HINTS = {
    "AccessDeniedException": (
        "Access denied by Amazon Bedrock. Check the IAM policy (bedrock:InvokeModel / Converse on the model or "
        "inference-profile ARN), that model access is enabled in the Bedrock console, and that any "
        "AWS_BEARER_TOKEN_BEDROCK API key in the environment has not expired."
    ),
    "ResourceNotFoundException": "The configured model ID was not found in this region. Check the model or inference profile ID.",
    "ValidationException": "Bedrock rejected the request. The model ID may need a cross-region inference profile, or the model may not support this input (e.g. images or tools).",
    "ThrottlingException": "Bedrock throttled the request. Retry later or request a quota increase.",
    "ServiceQuotaExceededException": "A Bedrock service quota was exceeded.",
    "ModelNotReadyException": "The model is not ready yet. Retry shortly.",
    "ModelTimeoutException": "The model took too long to respond.",
    "ModelErrorException": "The model returned an error while processing the request.",
}


def _map_client_error(exc: Exception) -> ProviderError:
    code = "unknown"
    message = str(exc)
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        err = response.get("Error", {})
        code = err.get("Code", code)
        message = err.get("Message", message)
    hint = _ERROR_HINTS.get(code, "Amazon Bedrock request failed.")
    retryable = code in ("ThrottlingException", "ModelNotReadyException", "ServiceUnavailableException")
    return ProviderError(f"{hint} ({code}: {message})", code=code, retryable=retryable)


_PROFILE_PREFIXES = ("us", "eu", "apac", "global", "jp", "au", "ca", "us-gov")


def _is_inference_profile(model_id: str) -> bool:
    if model_id.startswith("arn:"):
        return ":inference-profile/" in model_id or ":application-inference-profile/" in model_id
    return model_id.split(".", 1)[0] in _PROFILE_PREFIXES


class BedrockService:
    def __init__(self, settings: Settings, runtime_client: Any | None = None, control_client: Any | None = None):
        self.settings = settings
        self._runtime = runtime_client
        self._control = control_client

    def _client(self, service: str) -> Any:
        import boto3
        from botocore.config import Config

        if not self.settings.aws_region:
            raise ProviderError("AWS_REGION is not configured.", code="configuration")
        cfg = Config(
            region_name=self.settings.aws_region,
            retries={"max_attempts": 3, "mode": "standard"},
            read_timeout=self.settings.bedrock_read_timeout_s,
            connect_timeout=10,
        )
        return boto3.client(service, config=cfg)

    @property
    def runtime(self) -> Any:
        if self._runtime is None:
            self._runtime = self._client("bedrock-runtime")
        return self._runtime

    @property
    def control(self) -> Any:
        if self._control is None:
            self._control = self._client("bedrock")
        return self._control

    def converse(
        self,
        *,
        model_id: str,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tool_config: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        inference: dict[str, Any] = {"maxTokens": max_tokens or self.settings.bedrock_max_tokens}
        if self.settings.bedrock_temperature is not None:
            inference["temperature"] = self.settings.bedrock_temperature
        kwargs: dict[str, Any] = {
            "modelId": model_id,
            "system": system,
            "messages": messages,
            "inferenceConfig": inference,
        }
        if tool_config:
            kwargs["toolConfig"] = tool_config
        try:
            return self.runtime.converse(**kwargs)
        except ProviderError:
            raise
        except Exception as exc:
            if exc.__class__.__name__ in ("ClientError",) or hasattr(exc, "response"):
                raise _map_client_error(exc) from exc
            if exc.__class__.__name__ in ("NoCredentialsError", "PartialCredentialsError"):
                raise ProviderError("No AWS credentials found for Amazon Bedrock.", code="credentials") from exc
            raise ProviderError(f"Amazon Bedrock request failed: {exc}", code="connection", retryable=True) from exc

    # ------------------------------------------------------------------ model checks
    def check_model(self, model_id: str | None, role: str, *, probe: bool = False) -> ModelCheck:
        if not model_id:
            return ModelCheck(role=role, model_id=None, ok=False, detail="not configured")
        modalities: list[str] = []
        detail_parts: list[str] = []
        ok = True
        try:
            if _is_inference_profile(model_id):
                prof = self.control.get_inference_profile(inferenceProfileIdentifier=model_id)
                models = prof.get("models", [])
                detail_parts.append(f"inference profile '{prof.get('inferenceProfileName', model_id)}' ({prof.get('status', 'unknown')})")
                if models:
                    base_arn = models[0].get("modelArn", "")
                    base_id = base_arn.split("/")[-1] if base_arn else None
                    if base_id:
                        fm = self.control.get_foundation_model(modelIdentifier=base_id)["modelDetails"]
                        modalities = fm.get("inputModalities", [])
            else:
                fm = self.control.get_foundation_model(modelIdentifier=model_id)["modelDetails"]
                modalities = fm.get("inputModalities", [])
                lifecycle = (fm.get("modelLifecycle") or {}).get("status")
                detail_parts.append(f"foundation model '{fm.get('modelName', model_id)}'{f' ({lifecycle})' if lifecycle else ''}")
        except Exception as exc:
            mapped = _map_client_error(exc)
            return ModelCheck(role=role, model_id=model_id, ok=False, detail=str(mapped))
        if role == "vision" and modalities and "IMAGE" not in modalities:
            ok = False
            detail_parts.append("does not accept IMAGE input")
        if probe and ok:
            try:
                self.converse(
                    model_id=model_id,
                    system=[{"text": "Reply with the single word OK."}],
                    messages=[{"role": "user", "content": [{"text": "ping"}]}],
                    max_tokens=16,
                )
                detail_parts.append("invocation probe succeeded")
            except ProviderError as exc:
                ok = False
                detail_parts.append(f"invocation probe failed: {exc}")
        return ModelCheck(role=role, model_id=model_id, ok=ok, detail="; ".join(detail_parts) or "ok", input_modalities=modalities)
