from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
BACKEND_CAPABILITIES_ROOT = PROJECT_ROOT / "apps/api/src/wealth_agent/capabilities"
FRONTEND_PAGE_ROOTS = (
    PROJECT_ROOT / "apps/web/src/pages",
    PROJECT_ROOT / "apps/governance/src/pages",
)
EXPECTED_BACKEND_CAPABILITIES = {
    "agent",
    "analysis",
    "backup",
    "conversation",
    "governance",
    "portfolio",
    "tasks",
}
EXPECTED_FRONTEND_PAGES = {
    "apps/web/src/pages": {"backups", "chat", "reports"},
    "apps/governance/src/pages": {"model-governance"},
}
CAPABILITY_TITLES = {
    "agent": "# agent（智能体）",
    "analysis": "# analysis（理财分析）",
    "backup": "# backup（备份）",
    "conversation": "# conversation（对话）",
    "governance": "# governance（治理规则）",
    "portfolio": "# portfolio（资产组合）",
    "tasks": "# tasks（任务）",
}
CAPABILITY_SECTIONS = (
    "## Responsibility（职责）",
    "## Code（代码）",
    "## Dependencies（依赖）",
    "## Dependents（依赖方）",
    "## State / Data Ownership（状态 / 数据所有权）",
    "## Contract（公开契约）",
    "## Status（状态）",
)
BACKEND_CONTRACT_TITLES = {
    capability: f"# {capability} Contract（公开契约）"
    for capability in EXPECTED_BACKEND_CAPABILITIES
}
BACKEND_CONTRACT_SECTIONS = (
    "## Source of Truth（事实源）",
    "## Behavioral Guarantees（行为保证）",
    "## Stability（稳定边界）",
    "## Preconditions（调用前提）",
    "## Verification（验证）",
)
PAGE_CONTRACT_SECTIONS = (
    "## Source of Truth（事实源）",
    "## Behavioral Guarantees（行为保证）",
    "## Preconditions（调用前提）",
)
PAGE_SUMMARY_SECTIONS = (
    "## Responsibility（职责）",
    "## Boundary（边界）",
    "## State / Data Ownership（状态 / 数据所有权）",
    "## Navigation（导航）",
)


def _assert_sections_in_order(text: str, sections: tuple[str, ...], name: str) -> None:
    previous_position = -1
    for section in sections:
        position = text.find(section, previous_position + 1)
        assert position >= 0, f"{name} 缺少 {section}"
        previous_position = position


def _assert_no_bare_placeholder(text: str, name: str) -> None:
    assert "待补充" not in text.replace("【待补充】", ""), f"{name} 存在未标记的待补充内容"


def test_capability_summaries_and_contracts_are_separated() -> None:
    for capability in EXPECTED_BACKEND_CAPABILITIES:
        capability_readme_path = BACKEND_CAPABILITIES_ROOT / capability / "README.md"
        capability_readme = capability_readme_path.read_text(encoding="utf-8")
        assert capability_readme.startswith(CAPABILITY_TITLES[capability] + "\n")
        _assert_sections_in_order(
            capability_readme,
            CAPABILITY_SECTIONS,
            f"{capability}/README.md",
        )
        for marker in ("负责：", "不负责：", "已实现：", "待实现："):
            assert marker in capability_readme, f"{capability}/README.md 缺少 {marker}"
        assert "(contract/README.md)" in capability_readme
        _assert_no_bare_placeholder(capability_readme, str(capability_readme_path))

        contract_path = BACKEND_CAPABILITIES_ROOT / capability / "contract/README.md"
        contract = contract_path.read_text(encoding="utf-8")
        assert contract.startswith(BACKEND_CONTRACT_TITLES[capability] + "\n")
        _assert_sections_in_order(contract, BACKEND_CONTRACT_SECTIONS, str(contract_path))
        assert "[`__init__.py`](__init__.py)" in contract
        assert "不应依赖本能力的 `implementation/`" in contract
        _assert_no_bare_placeholder(contract, str(contract_path))


def test_frontend_pages_use_contract_as_the_only_public_boundary() -> None:
    for page_root in FRONTEND_PAGE_ROOTS:
        relative_root = page_root.relative_to(PROJECT_ROOT).as_posix()
        actual_pages = {path.name for path in page_root.iterdir() if path.is_dir()}
        assert actual_pages == EXPECTED_FRONTEND_PAGES[relative_root]

        for page in actual_pages:
            page_path = page_root / page
            page_readme_path = page_path / "README.md"
            page_readme = page_readme_path.read_text(encoding="utf-8")
            contract_path = page_path / "contract/README.md"
            contract = contract_path.read_text(encoding="utf-8")

            assert page_readme_path.is_file()
            assert (page_path / "contract").is_dir()
            assert contract_path.is_file()
            assert (page_path / "implementation").is_dir()
            assert (page_path / "tests").is_dir()
            assert not (page_path / "schema").exists()
            assert "## Input（输入）" not in page_readme
            _assert_sections_in_order(page_readme, PAGE_SUMMARY_SECTIONS, str(page_readme_path))
            for marker in ("负责：", "不负责：", "拥有：", "不拥有："):
                assert marker in page_readme, f"{page}/README.md 缺少 {marker}"
            _assert_no_bare_placeholder(page_readme, str(page_readme_path))
            _assert_no_bare_placeholder(contract, str(contract_path))
            assert contract.startswith(f"# {page} 页面 Contract（公开契约）\n")
            _assert_sections_in_order(contract, PAGE_CONTRACT_SECTIONS, str(contract_path))
            assert "packages/api-client/src/index.ts" in contract
            assert "不应依赖后端能力的\n`implementation/`" in contract


def test_app_assembly_docs_are_kept_at_application_level() -> None:
    for app_root in (PROJECT_ROOT / "apps/web", PROJECT_ROOT / "apps/governance"):
        assert not (app_root / "src/app/README.md").exists()
        app_readme = (app_root / "README.md").read_text(encoding="utf-8")
        assert "src/main.ts" in app_readme
        assert "src/App.vue" in app_readme


def test_frontend_source_does_not_reference_sqlite() -> None:
    source_suffixes = {".ts", ".vue"}
    for page_root in FRONTEND_PAGE_ROOTS:
        for path in (page_root.parent.parent / "src").rglob("*"):
            if path.is_file() and path.suffix in source_suffixes:
                assert "sqlite" not in path.read_text(encoding="utf-8").lower(), path


def test_project_documents_follow_their_roles() -> None:
    root_documents = {path.name for path in PROJECT_ROOT.glob("*.md")}
    assert root_documents == {"README.md", "PROJECT.md", "SYSTEM.md", "ROADMAP.md"}

    root_readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    for link in (
        "PROJECT.md",
        "SYSTEM.md",
        "ROADMAP.md",
    ):
        assert f"`{link}`" in root_readme

    project = (PROJECT_ROOT / "PROJECT.md").read_text(encoding="utf-8")
    project_sections = (
        "## Problem（问题）",
        "## Actor（参与者）",
        "## Outcome（目标结果）",
        "## Success Criteria（成功标准）",
        "## Scope（范围）",
        "## Out of Scope（范围之外）",
        "## Constraints（约束）",
    )
    _assert_sections_in_order(project, project_sections, "PROJECT.md")
    assert "## Assumptions（假设）" not in project
    assert "## Risks（风险）" not in project

    system = (PROJECT_ROOT / "SYSTEM.md").read_text(encoding="utf-8")
    for section in (
        "## Dependency Rules（依赖规则）",
        "## Capability Boundaries（能力边界）",
        "## Data Ownership（数据所有权）",
        "## Core Rules（核心规则）",
    ):
        assert section in system

    roadmap = (PROJECT_ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    assert roadmap.startswith("# Roadmap（路线图）\n")
    assert "当前实现状态以代码和测试为准" in roadmap


def test_changed_document_placeholders_use_the_explicit_marker() -> None:
    paths = [
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "PROJECT.md",
        PROJECT_ROOT / "SYSTEM.md",
        PROJECT_ROOT / "ROADMAP.md",
        PROJECT_ROOT / "apps/web/README.md",
        PROJECT_ROOT / "apps/governance/README.md",
    ]
    paths.extend(BACKEND_CAPABILITIES_ROOT.glob("*/README.md"))
    paths.extend(BACKEND_CAPABILITIES_ROOT.glob("*/contract/README.md"))
    for page_root in FRONTEND_PAGE_ROOTS:
        for page in EXPECTED_FRONTEND_PAGES[page_root.relative_to(PROJECT_ROOT).as_posix()]:
            paths.extend((page_root / page).glob("**/README.md"))

    for path in paths:
        _assert_no_bare_placeholder(path.read_text(encoding="utf-8"), str(path))
