import json
import subprocess

import pytest

from codex_agentic_os.index import (
    Evidence,
    IndexConfig,
    SourceSpan,
    SymbolKind,
    SymbolRecord,
    discover_tracked_files,
    deterministic_json,
    deterministic_jsonl,
    schema_document,
    stable_id,
)


def test_schema_document_is_a_stable_golden_contract() -> None:
    assert deterministic_json(schema_document()) == (
        b'{"evidence":["declared","resolved","inferred","unresolved"],'
        b'"line_format":"one-based-inclusive","parser_api_version":"1.0.0",'
        b'"path_format":"repository-relative-posix","record_types":["dependency","symbol"],'
        b'"schema_version":"1.0.0","serialization":"UTF-8 JSON; sorted keys; compact separators; LF newline",'
        b'"stable_id":"sha256(JSON([language,kind,qualified_name]))",'
        b'"symbol_kinds":["module","class","function","method"]}\n'
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
