from __future__ import annotations

import ast
from collections.abc import Iterator
from importlib.util import resolve_name
from pathlib import Path

API_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "wealth_agent"
CAPABILITIES_ROOT = API_PACKAGE / "capabilities"
ADAPTERS_ROOT = API_PACKAGE / "adapters"
SHARED_ROOT = API_PACKAGE / "shared"
CAPABILITY_PACKAGE_PREFIX = "wealth_agent.capabilities"
EXPECTED_CAPABILITIES = {
    "agent",
    "analysis",
    "backup",
    "conversation",
    "governance",
    "portfolio",
    "tasks",
}
EXPECTED_SHARED_COMPONENTS = {
    "clock",
    "errors",
    "file_lock",
    "identity",
    "logging",
    "model",
    "serialization",
}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(API_PACKAGE.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_name = _module_name(path)
    package_name = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative_name = "." * node.level + (node.module or "")
                imported_from = resolve_name(relative_name, package_name)
            else:
                imported_from = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    yield imported_from
                else:
                    yield f"{imported_from}.{alias.name}" if imported_from else alias.name


def _capability_python_files() -> Iterator[tuple[str, Path]]:
    for capability in sorted(EXPECTED_CAPABILITIES):
        for path in (CAPABILITIES_ROOT / capability).rglob("*.py"):
            yield capability, path


def test_capability_layout_uses_contract_as_the_only_public_boundary() -> None:
    actual_capabilities = {
        path.name
        for path in CAPABILITIES_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    assert actual_capabilities == EXPECTED_CAPABILITIES

    for capability in EXPECTED_CAPABILITIES:
        root = CAPABILITIES_ROOT / capability
        assert (root / "README.md").is_file()
        assert (root / "contract").is_dir()
        assert (root / "contract" / "README.md").is_file()
        assert (root / "implementation").is_dir()
        assert (root / "tests").is_dir()
        assert not (root / "schema").exists()


def test_backend_layout_separates_http_adapters_and_bootstrap() -> None:
    assert (ADAPTERS_ROOT / "__init__.py").is_file()
    assert (ADAPTERS_ROOT / "http" / "__init__.py").is_file()
    assert (ADAPTERS_ROOT / "http" / "README.md").is_file()
    assert (ADAPTERS_ROOT / "http" / "dependencies.py").is_file()
    assert {
        path.name for path in (ADAPTERS_ROOT / "http" / "routes").glob("*.py")
    } == {
        "__init__.py",
        "analysis.py",
        "backup.py",
        "conversation.py",
        "governance.py",
        "portfolio.py",
    }
    assert (API_PACKAGE / "bootstrap" / "__init__.py").is_file()
    assert (ADAPTERS_ROOT / "model" / "__init__.py").is_file()
    assert (ADAPTERS_ROOT / "logging" / "__init__.py").is_file()
    assert (ADAPTERS_ROOT / "logging" / "README.md").is_file()
    assert (SHARED_ROOT / "model" / "contracts.py").is_file()


def test_runtime_business_methods_are_structurally_traced() -> None:
    expected_classes = {
        "AgentToolRegistry",
        "DefaultGovernanceService",
        "DeterministicAnalysis",
        "ExactJsonEvaluationService",
        "InMemoryConversationService",
        "InMemoryTaskStateStore",
        "InMemoryTaskService",
        "LocalBackupService",
        "ModelAgentRouter",
        "ModelAssistedAnalysis",
        "ModelTaskPlanner",
        "OpenAiCompatibleGateway",
        "RapidOcrAdapter",
        "ReadOnlyAgentPlanner",
        "ReadOnlyAgentRuntime",
        "SqliteAnalysisRepository",
        "SqliteConversationService",
        "SqlitePortfolioBackupPort",
        "SqlitePortfolioReader",
        "SqlitePortfolioStore",
        "SqliteTaskStateStore",
        "SqliteTelemetryRepository",
        "StoredAnalysisService",
        "TaskOrchestratingAgentRuntime",
        "TaskStateMachineService",
        "TaskWorker",
        "TaskWorkerRuntime",
    }
    traced_classes: set[str] = set()
    for root in (ADAPTERS_ROOT, CAPABILITIES_ROOT):
        for path in root.rglob("*.py"):
            if "tests" in path.parts or "contract" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                if any(
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "trace_public_methods"
                    for decorator in node.decorator_list
                ):
                    traced_classes.add(node.name)
    assert traced_classes == expected_classes

    for path in (ADAPTERS_ROOT / "http" / "routes").glob("*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        route_functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name != f"build_{path.stem}_router"
        ]
        assert route_functions
        for node in route_functions:
            assert any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "trace_call"
                for decorator in node.decorator_list
            ), f"{path}:{node.lineno} 的 HTTP 入口缺少结构化日志"


def test_shared_components_use_package_boundaries() -> None:
    assert {path.name for path in SHARED_ROOT.glob("*.py")} == {"__init__.py"}

    actual_components = {
        path.name
        for path in SHARED_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith("__")
    }
    assert actual_components == EXPECTED_SHARED_COMPONENTS
    for component in EXPECTED_SHARED_COMPONENTS:
        assert (SHARED_ROOT / component / "__init__.py").is_file()


def test_shared_consumers_use_component_public_entrypoints() -> None:
    search_roots = (API_PACKAGE, Path(__file__).resolve().parents[1])
    for search_root in search_roots:
        for path in search_root.rglob("*.py"):
            if path.is_relative_to(SHARED_ROOT):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module is None:
                    continue
                if node.module == "wealth_agent.shared":
                    raise AssertionError(f"{path} 不得从 shared 根包集中导入")
                if not node.module.startswith("wealth_agent.shared."):
                    continue
                parts = node.module.split(".")
                assert len(parts) == 3 and parts[2] in EXPECTED_SHARED_COMPONENTS, (
                    f"{path} 绕过了 shared 组件公共入口：{node.module}"
                )


def test_cross_capability_imports_only_use_contracts() -> None:
    for source_capability, path in _capability_python_files():
        for imported_module in _imported_modules(path):
            parts = imported_module.split(".")
            if parts[:2] != ["wealth_agent", "capabilities"] or len(parts) < 3:
                continue
            target_capability = parts[2]
            if target_capability not in EXPECTED_CAPABILITIES:
                continue
            if target_capability == source_capability:
                continue
            assert len(parts) >= 4 and parts[3] == "contract", (
                f"{path} 跨能力导入了私有模块：{imported_module}"
            )


def test_http_adapters_only_use_capability_contracts() -> None:
    for path in (ADAPTERS_ROOT / "http").rglob("*.py"):
        for imported_module in _imported_modules(path):
            parts = imported_module.split(".")
            if parts[:2] != ["wealth_agent", "capabilities"] or len(parts) < 3:
                continue
            target_capability = parts[2]
            if target_capability not in EXPECTED_CAPABILITIES:
                continue
            assert len(parts) >= 4 and parts[3] == "contract", (
                f"{path} HTTP 适配器导入了能力私有模块：{imported_module}"
            )


def test_http_adapters_do_not_depend_on_bootstrap() -> None:
    for path in (ADAPTERS_ROOT / "http").rglob("*.py"):
        for imported_module in _imported_modules(path):
            assert imported_module != "wealth_agent.bootstrap"
            assert not imported_module.startswith("wealth_agent.bootstrap."), (
                f"{path} HTTP 适配器反向依赖了 bootstrap：{imported_module}"
            )


def test_contracts_do_not_depend_on_private_implementation() -> None:
    for capability in EXPECTED_CAPABILITIES:
        private_prefix = f"{CAPABILITY_PACKAGE_PREFIX}.{capability}.implementation"
        for path in (CAPABILITIES_ROOT / capability / "contract").rglob("*.py"):
            for imported_module in _imported_modules(path):
                assert imported_module != private_prefix
                assert not imported_module.startswith(f"{private_prefix}.")


def test_contract_protocol_methods_do_not_expose_multiple_input_values() -> None:
    for capability in EXPECTED_CAPABILITIES:
        for path in (CAPABILITIES_ROOT / capability / "contract").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                is_protocol = any(
                    isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases
                )
                if not is_protocol:
                    continue
                for member in node.body:
                    if not isinstance(member, ast.FunctionDef) or member.name.startswith("_"):
                        continue
                    positional = [*member.args.posonlyargs, *member.args.args]
                    inputs = [argument for argument in positional if argument.arg != "self"]
                    inputs.extend(member.args.kwonlyargs)
                    assert member.args.vararg is None and member.args.kwarg is None, (
                        f"{path}:{member.lineno} 的公开契约方法不得使用可变参数"
                    )
                    assert len(inputs) <= 1, (
                        f"{path}:{member.lineno} 的多个关联输入必须封装为 Request Model"
                    )


def test_contract_protocols_do_not_expose_unstructured_collections() -> None:
    unstructured = {"dict", "list", "tuple", "set"}
    for capability in EXPECTED_CAPABILITIES:
        for path in (CAPABILITIES_ROOT / capability / "contract").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                if not any(
                    isinstance(base, ast.Name) and base.id == "Protocol"
                    for base in node.bases
                ):
                    continue
                for member in node.body:
                    if not isinstance(member, ast.FunctionDef) or member.name.startswith("_"):
                        continue
                    annotations = [
                        argument.annotation
                        for argument in [
                            *member.args.posonlyargs,
                            *member.args.args,
                            *member.args.kwonlyargs,
                        ]
                        if argument.arg != "self"
                    ]
                    annotations.append(member.returns)
                    for annotation in annotations:
                        root = (
                            annotation.value
                            if isinstance(annotation, ast.Subscript)
                            else annotation
                        )
                        assert not (
                            isinstance(root, ast.Name) and root.id in unstructured
                        ), f"{path}:{member.lineno} 的公开输入输出必须使用结构化类型"


def test_capabilities_do_not_depend_on_bootstrap() -> None:
    for _, path in _capability_python_files():
        for imported_module in _imported_modules(path):
            assert imported_module != "wealth_agent.bootstrap"
            assert not imported_module.startswith("wealth_agent.bootstrap.")


def test_capabilities_do_not_depend_on_concrete_model_adapters() -> None:
    for _, path in _capability_python_files():
        for imported_module in _imported_modules(path):
            assert imported_module != "wealth_agent.adapters.model"
            assert not imported_module.startswith("wealth_agent.adapters.model.")


def test_chat_orchestration_dependency_direction_is_enforced() -> None:
    conversation_root = CAPABILITIES_ROOT / "conversation"
    tasks_root = CAPABILITIES_ROOT / "tasks"
    for path in conversation_root.rglob("*.py"):
        assert not any(
            module.startswith("wealth_agent.capabilities.tasks")
            for module in _imported_modules(path)
        ), f"{path} Conversation 不得调用 Tasks"
    for path in tasks_root.rglob("*.py"):
        assert not any(
            module.startswith("wealth_agent.capabilities.agent")
            for module in _imported_modules(path)
        ), f"{path} Tasks 不得依赖 Agent"

    registry = (
        CAPABILITIES_ROOT / "agent/implementation/tools/registry.py"
    ).read_text(encoding="utf-8")
    assert "backup_restore" not in registry
    assert "restore_backup" not in registry


def test_shared_does_not_depend_on_business_or_outer_layers() -> None:
    forbidden_prefixes = (
        "wealth_agent.capabilities",
        "wealth_agent.adapters",
        "wealth_agent.bootstrap",
    )
    for path in SHARED_ROOT.rglob("*.py"):
        for imported_module in _imported_modules(path):
            assert not imported_module.startswith(forbidden_prefixes), (
                f"{path} 的 shared 代码依赖了业务或外层模块：{imported_module}"
            )


def test_agent_tools_do_not_offer_ledger_restore() -> None:
    tools_root = CAPABILITIES_ROOT / "agent" / "implementation" / "tools"
    for path in tools_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8").lower()
        assert "restore" not in source
        assert "恢复" not in source
