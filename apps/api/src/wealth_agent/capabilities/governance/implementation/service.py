"""Deterministic governance policy and exact structured evaluation."""

from wealth_agent.capabilities.governance.contract import (
    AuthorizationRequest,
    AuthorizationResult,
    EvaluationCaseResult,
    EvaluationRequest,
    EvaluationResult,
    ToolAuthorizationRequest,
)
from wealth_agent.shared.logging import trace_public_methods


@trace_public_methods(layer="capability")
class DefaultGovernanceService:
    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        if request.side_effects == "backup_restore" and request.actor == "agent":
            return AuthorizationResult(
                decision="denied",
                reason="台账恢复只能由备份页面发起",
            )
        if request.side_effects == "none":
            allowed = set(request.requested_permissions).issubset({"ledger:read"})
            return AuthorizationResult(
                decision="allowed" if allowed else "denied",
                granted_permissions=request.requested_permissions if allowed else [],
                reason="只读权限已允许" if allowed else "请求包含未知只读权限",
            )
        if not request.confirmed:
            return AuthorizationResult(
                decision="confirmation_required",
                reason="有副作用的操作必须由用户明确确认",
            )
        return AuthorizationResult(
            decision="allowed",
            granted_permissions=request.requested_permissions,
            reason="用户确认与当前操作绑定，允许执行",
        )

    def authorize_tool(self, request: ToolAuthorizationRequest) -> AuthorizationResult:
        allowed_permissions = {
            "none": {"ledger:read"},
            "ledger_write": {"ledger:write"},
        }[request.side_effects]
        if not set(request.requested_permissions).issubset(allowed_permissions):
            return AuthorizationResult(
                decision="denied",
                reason="Tool 请求包含与副作用不匹配的权限",
            )
        if request.side_effects == "none":
            return AuthorizationResult(
                decision="allowed",
                granted_permissions=request.requested_permissions,
                reason="只读 Tool 已自动允许",
            )
        if request.approval is None:
            return AuthorizationResult(
                decision="confirmation_required",
                reason="台账写入必须由用户确认具体操作",
            )
        if request.approval.decision == "reject":
            return AuthorizationResult(decision="denied", reason="用户已拒绝该操作")
        if request.approval.action_hash != request.action_hash:
            return AuthorizationResult(
                decision="denied",
                reason="用户确认与当前 Tool 参数不匹配",
            )
        return AuthorizationResult(
            decision="allowed",
            granted_permissions=request.requested_permissions,
            rule_version="governance-v2",
            reason="用户确认与当前 Tool 操作绑定，允许执行",
        )


@trace_public_methods(layer="capability")
class ExactJsonEvaluationService:
    def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        cases = [
            EvaluationCaseResult(case_id=item.case_id, passed=item.expected == item.actual)
            for item in request.cases
        ]
        passed = sum(item.passed for item in cases)
        failed = len(cases) - passed
        return EvaluationResult(
            evaluation_id=request.evaluation_id,
            dataset_version=request.dataset_version,
            passed=passed,
            failed=failed,
            pass_rate=passed / len(cases),
            cases=cases,
        )
