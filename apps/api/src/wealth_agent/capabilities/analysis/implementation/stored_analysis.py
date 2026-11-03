"""Decorator that persists the structured output of an analysis service."""

from wealth_agent.capabilities.analysis.contract import (
    AnalysisRecord,
    AnalysisRepository,
    AnalysisResult,
    AnalysisService,
    AnalyzeProductRequest,
)
from wealth_agent.shared.logging import trace_public_methods


@trace_public_methods(layer="capability")
class StoredAnalysisService:
    def __init__(
        self,
        service: AnalysisService,
        repository: AnalysisRepository,
        *,
        provider: str,
        model: str,
    ) -> None:
        self._service = service
        self._repository = repository
        self._provider = provider
        self._model = model

    def analyze(self, request: AnalyzeProductRequest) -> AnalysisResult:
        result = self._service.analyze(request)
        self._repository.save(
            AnalysisRecord(
                created_at=request.generated_at,
                question=request.question,
                provider=self._provider,
                model=self._model,
                result=result,
            )
        )
        return result
