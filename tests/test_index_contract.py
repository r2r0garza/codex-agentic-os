import json
import subprocess

import pytest

from codex_agentic_os.index import (
    DependencyKind,
    DependencyRecord,
    Evidence,
    IndexConfig,
    PythonParser,
    SourceSpan,
    SymbolKind,
    SymbolRecord,
    build_clean_index,
    build_incremental_index,
    discover_tracked_files,
    deterministic_json,
    deterministic_jsonl,
    schema_document,
    stable_id,
)


def test_schema_document_is_a_stable_golden_contract() -> None:
    assert deterministic_json(schema_document()) == (
        b'{"call_target_contract":{"resolved":"evidence=resolved and target_id is the stable ID of one indexed symbol",'
        b'"target":"normalized syntactic callee text","unresolved":"evidence=unresolved and target_id is omitted"},'
        b'"dependency_kinds":["import","call"],'
        b'"dependency_source_identity":"source_id is the stable ID of the lexically enclosing indexed symbol",'
        b'"evidence":["declared","resolved","inferred","unresolved"],'
        b'"line_format":"one-based-inclusive","parser_api_version":"1.1.0",'
        b'"path_format":"repository-relative-posix","record_types":["dependency","symbol"],'
        b'"schema_version":"1.1.0","serialization":"UTF-8 JSON; sorted keys; compact separators; LF newline",'
        b'"stable_id":"sha256(JSON([language,kind,qualified_name]))",'
        b'"symbol_kinds":["module","class","function","method"]}\n'
    )


def test_call_relationship_contract_requires_stable_resolved_target_identity() -> None:
    source_id = stable_id("python", "function", "pkg.caller")
    target_id = stable_id("python", "function", "pkg.callee")
    resolved = DependencyRecord(
        "python",
        DependencyKind.CALL,
        source_id,
        "callee",
        Evidence.RESOLVED,
        SourceSpan("pkg.py", 3, 3),
        target_id=target_id,
    )
    unresolved = DependencyRecord(
        "python",
        DependencyKind.CALL,
        source_id,
        "receiver.method",
        Evidence.UNRESOLVED,
        SourceSpan("pkg.py", 4, 4),
    )

    assert resolved.to_dict()["target_id"] == target_id
    assert "target_id" not in unresolved.to_dict()
    with pytest.raises(ValueError, match="resolved calls require target_id"):
        DependencyRecord(
            "python",
            DependencyKind.CALL,
            source_id,
            "callee",
            Evidence.RESOLVED,
            SourceSpan("pkg.py", 3, 3),
        )
    with pytest.raises(ValueError, match="resolved or unresolved"):
        DependencyRecord(
            "python",
            DependencyKind.CALL,
            source_id,
            "callee",
            Evidence.INFERRED,
            SourceSpan("pkg.py", 3, 3),
        )


def test_stable_ids_include_language_and_kind_and_are_reproducible() -> None:
    first = stable_id("python", "class", "package.Widget")

    assert first == stable_id("python", "class", "package.Widget")
    assert first.startswith("python:class:")
    assert first != stable_id("typescript", "class", "package.Widget")
    assert first != stable_id("python", "function", "package.Widget")


def test_symbol_jsonl_is_order_independent_and_keeps_extensions_namespaced() -> None:
    alpha = SymbolRecord(
        "python",
        SymbolKind.FUNCTION,
        "pkg.alpha",
        SourceSpan("src/pkg.py", 1, 2),
        signature="()",
        extensions={"python": {"decorators": ["cache"]}},
    )
    beta = SymbolRecord("python", SymbolKind.CLASS, "pkg.Beta", SourceSpan("src/pkg.py", 4, 8))

    output = deterministic_jsonl((beta, alpha))

    assert output == deterministic_jsonl((alpha, beta))
    decoded = [json.loads(line) for line in output.splitlines()]
    assert {item["qualified_name"] for item in decoded} == {"pkg.alpha", "pkg.Beta"}
    assert next(item for item in decoded if item["qualified_name"] == "pkg.alpha")["extensions"] == {
        "python": {"decorators": ["cache"]}
    }


@pytest.mark.parametrize("path", ("/tmp/source.py", "../secret", r"src\\source.py"))
def test_source_spans_reject_non_repository_posix_paths(path: str) -> None:
    with pytest.raises(ValueError, match="repository-relative POSIX"):
        SourceSpan(path, 1, 1)


def test_configuration_fingerprint_changes_only_with_explicit_configuration() -> None:
    default = IndexConfig()

    assert default.fingerprint == IndexConfig().fingerprint
    assert default.fingerprint != IndexConfig(max_file_bytes=10).fingerprint
    assert Evidence.UNRESOLVED.value == "unresolved"


def _git(repository, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True)


def test_tracked_file_discovery_is_filtered_hashed_and_lexically_ordered(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    files = {
        "z.py": b"print('z')\n",
        "src/a.py": b"VALUE = 1\n",
        "pyproject.toml": b"[project]\nname='fixture'\n",
        ".env": b"TOKEN=secret\n",
        ".venv/hidden.py": b"secret = True\n",
        "notes.md": b"not included\n",
    }
    for relative_path, content in files.items():
        destination = repository / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    _git(repository, "add", "--force", ".")

    records = discover_tracked_files(repository)

    assert [record.path for record in records] == ["pyproject.toml", "src/a.py", "z.py"]
    assert records[1].size == len(files["src/a.py"])
    assert records[1].sha256 == "e13df8c44af5dea1e412403910b99cc5a48f2ccbf68a66b3374d6ab9cef9fc65"


def test_discovery_uses_worktree_content_and_ignores_untracked_and_oversized_files(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    tracked = repository / "tracked.py"
    tracked.write_bytes(b"old\n")
    _git(repository, "add", "tracked.py")
    tracked.write_bytes(b"new\n")
    (repository / "untracked.py").write_bytes(b"untracked\n")

    first = discover_tracked_files(repository)
    second = discover_tracked_files(repository)

    assert first == second
    assert [record.path for record in first] == ["tracked.py"]
    assert first[0].sha256 == "7aa7a5359173d05b63cfd682e3c38487f3cb4f7f1d60659fe59fab1505977d4c"
    assert discover_tracked_files(repository, IndexConfig(max_file_bytes=3)) == ()


def test_python_parser_extracts_symbols_signatures_imports_and_spans() -> None:
    source = b'''\
import os
from .helpers import tool as renamed

class Public(Base):
    @classmethod
    async def build(cls, value: str = "x") -> "Public":
        import json
        def normalize(item: str) -> str:
            return item
        return cls()

def _helper(*items: int, flag: bool = False) -> None:
    pass
'''
    result = PythonParser().parse("src/package/module.py", source)
    by_name = {symbol.qualified_name: symbol for symbol in result.symbols}

    assert set(by_name) == {
        "package.module",
        "package.module.Public",
        "package.module.Public.build",
        "package.module.Public.build.normalize",
        "package.module._helper",
    }
    assert by_name["package.module.Public"].kind is SymbolKind.CLASS
    assert by_name["package.module.Public"].span == SourceSpan("src/package/module.py", 4, 10)
    assert by_name["package.module.Public.build"].kind is SymbolKind.METHOD
    assert by_name["package.module.Public.build"].span == SourceSpan("src/package/module.py", 5, 10)
    assert by_name["package.module.Public.build"].signature == "(cls, value: str='x') -> 'Public'"
    assert by_name["package.module.Public.build"].extensions == {
        "python": {"decorators": ["classmethod"], "async": True}
    }
    assert by_name["package.module.Public.build.normalize"].kind is SymbolKind.FUNCTION
    assert by_name["package.module._helper"].visibility == "private"
    imports = [
        dependency for dependency in result.dependencies if dependency.kind is DependencyKind.IMPORT
    ]
    assert sorted((dependency.target, dependency.evidence) for dependency in imports) == [
        (".helpers.tool", Evidence.DECLARED),
        ("json", Evidence.DECLARED),
        ("os", Evidence.DECLARED),
    ]
    assert next(dependency for dependency in imports if dependency.target == "json").source_id == (
        by_name["package.module.Public.build"].id
    )


def test_python_parser_handles_packages_empty_files_and_parse_errors() -> None:
    parser = PythonParser()

    result = parser.parse("src/package/__init__.py", b"")

    assert parser.supports("package/module.py")
    assert not parser.supports("package/module.ts")
    assert result.symbols[0].qualified_name == "package"
    assert result.symbols[0].span == SourceSpan("src/package/__init__.py", 1, 1)
    with pytest.raises(ValueError, match="cannot parse Python source"):
        parser.parse("broken.py", b"def nope(:\n")


def test_python_parser_extracts_call_candidates_with_enclosing_symbols() -> None:
    source = b'''\
def outer(factory):
    first = factory()
    items = [transform(item) for item in first]
    def nested():
        return helper(first)
    return nested()

class Service:
    async def run(self):
        return await self.fetch(
            build_request()
        )
'''
    result = PythonParser().parse("src/package/calls.py", source)
    symbols = {symbol.qualified_name: symbol for symbol in result.symbols}
    calls = [dependency for dependency in result.dependencies if dependency.kind is DependencyKind.CALL]

    assert [
        (call.source_id, call.target, call.evidence, call.span)
        for call in calls
    ] == sorted(
        [
            (symbols["package.calls.outer"].id, "factory", Evidence.UNRESOLVED, SourceSpan("src/package/calls.py", 2, 2)),
            (symbols["package.calls.outer"].id, "transform", Evidence.UNRESOLVED, SourceSpan("src/package/calls.py", 3, 3)),
            (symbols["package.calls.outer"].id, "nested", Evidence.UNRESOLVED, SourceSpan("src/package/calls.py", 6, 6)),
            (symbols["package.calls.outer.nested"].id, "helper", Evidence.UNRESOLVED, SourceSpan("src/package/calls.py", 5, 5)),
            (symbols["package.calls.Service.run"].id, "self.fetch", Evidence.UNRESOLVED, SourceSpan("src/package/calls.py", 10, 12)),
            (symbols["package.calls.Service.run"].id, "build_request", Evidence.UNRESOLVED, SourceSpan("src/package/calls.py", 11, 11)),
        ],
        key=lambda item: deterministic_json(DependencyRecord("python", DependencyKind.CALL, item[0], item[1], item[2], item[3]).to_dict()),
    )
    assert all(call.target_id is None for call in calls)


def test_clean_build_resolves_only_proven_repository_call_targets(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    package = repository / "src" / "package"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "helpers.py").write_text(
        "def imported():\n    return True\n\ndef other():\n    return True\n"
    )
    (package / "calls.py").write_text(
        "from .helpers import imported as alias\n"
        "import package.helpers\n\n"
        "def local():\n    return True\n\n"
        "def caller(injected):\n"
        "    local()\n"
        "    alias()\n"
        "    package.helpers.other()\n"
        "    injected()\n\n"
        "class Service:\n"
        "    def run(self):\n        self.finish()\n        self.missing()\n\n"
        "    def finish(self):\n        return True\n"
    )
    _git(repository, "add", ".")

    build_clean_index(repository)
    symbol_records = [
        json.loads(line)
        for line in (repository / ".code-index" / "symbols.jsonl").read_text().splitlines()
    ]
    dependency_records = [
        json.loads(line)
        for line in (repository / ".code-index" / "dependencies.jsonl").read_text().splitlines()
    ]
    symbols = {
        record["qualified_name"]: record
        for record in symbol_records
    }
    calls = {
        (record["source_id"], record["target"]): record
        for record in dependency_records
        if record["kind"] == "call"
    }
    caller_id = symbols["package.calls.caller"]["id"]
    run_id = symbols["package.calls.Service.run"]["id"]

    assert calls[(caller_id, "local")]["target_id"] == symbols["package.calls.local"]["id"]
    assert calls[(caller_id, "alias")]["target_id"] == symbols["package.helpers.imported"]["id"]
    assert calls[(caller_id, "package.helpers.other")]["target_id"] == symbols["package.helpers.other"]["id"]
    assert calls[(run_id, "self.finish")]["target_id"] == symbols["package.calls.Service.finish"]["id"]
    assert calls[(caller_id, "injected")]["evidence"] == "unresolved"
    assert "target_id" not in calls[(caller_id, "injected")]
    assert calls[(run_id, "self.missing")]["evidence"] == "unresolved"


def test_clean_build_writes_deterministic_manifest_and_jsonl(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    (repository / "src").mkdir()
    (repository / "src/example.py").write_text("import os\n\ndef hello(name: str) -> str:\n    return name\n")
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n")
    _git(repository, "add", ".")

    first_manifest = build_clean_index(repository)
    first = {path.name: path.read_bytes() for path in (repository / ".code-index").iterdir()}
    second_manifest = build_clean_index(repository)
    second = {path.name: path.read_bytes() for path in (repository / ".code-index").iterdir()}

    assert first == second
    assert first_manifest == second_manifest
    assert set(first) == {"schema.json", "manifest.json", "symbols.jsonl", "dependencies.jsonl"}
    manifest = json.loads(first["manifest.json"])
    assert manifest["generator_version"] == "1.1.0"
    assert manifest["artifact_counts"] == {"tracked_files": 2, "symbols": 2, "dependencies": 1}
    assert [record["path"] for record in manifest["tracked_files"]] == [
        "pyproject.toml",
        "src/example.py",
    ]
    assert [json.loads(line)["qualified_name"] for line in first["symbols.jsonl"].splitlines()] == [
        "example.hello",
        "example",
    ]


def test_clean_build_removes_stale_output_and_rejects_parser_version_mismatch(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    (repository / "example.py").write_text("VALUE = 1\n")
    _git(repository, "add", ".")
    output = repository / ".code-index"
    output.mkdir()
    (output / "stale.json").write_text("stale")

    build_clean_index(repository)

    assert not (output / "stale.json").exists()

    class OldParser(PythonParser):
        api_version = "0.9.0"

    with pytest.raises(ValueError, match="parser API version mismatch"):
        build_clean_index(repository, parsers=(OldParser(),))


def test_incremental_build_parses_only_changes_and_matches_clean_output(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    (repository / "keep.py").write_text("def keep():\n    return 1\n")
    (repository / "edit.py").write_text("def before():\n    return 1\n")
    (repository / "delete.py").write_text("DELETED = True\n")
    (repository / "rename.py").write_text("def renamed():\n    return True\n")
    _git(repository, "add", ".")
    build_clean_index(repository)

    (repository / "edit.py").write_text("def after():\n    return 2\n")
    (repository / "add.py").write_text("import os\n")
    (repository / "delete.py").unlink()
    (repository / "rename.py").rename(repository / "moved.py")
    _git(repository, "add", "--all")

    class RecordingParser(PythonParser):
        def __init__(self) -> None:
            self.paths: list[str] = []

        def parse(self, path: str, source: bytes):
            self.paths.append(path)
            return super().parse(path, source)

    parser = RecordingParser()
    incremental_manifest = build_incremental_index(repository, parsers=(parser,))
    incremental = {
        path.name: path.read_bytes() for path in (repository / ".code-index").iterdir()
    }
    clean_manifest = build_clean_index(repository)
    clean = {path.name: path.read_bytes() for path in (repository / ".code-index").iterdir()}

    assert parser.paths == ["add.py", "edit.py", "moved.py"]
    assert incremental_manifest == clean_manifest
    assert incremental == clean
    symbols = [json.loads(line) for line in incremental["symbols.jsonl"].splitlines()]
    assert {record["qualified_name"] for record in symbols} == {
        "add",
        "edit",
        "edit.after",
        "keep",
        "keep.keep",
        "moved",
        "moved.renamed",
    }


def test_incremental_build_falls_back_when_prior_index_is_incompatible(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    (repository / "example.py").write_text("VALUE = 1\n")
    _git(repository, "add", ".")
    build_clean_index(repository)
    manifest_path = repository / ".code-index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = "0.0.0"
    manifest_path.write_bytes(deterministic_json(manifest))

    incremental = build_incremental_index(repository)
    clean = build_clean_index(repository)

    assert incremental == clean
