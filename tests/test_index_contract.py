import json

import pytest

from codex_agentic_os.index import (
    Evidence,
    IndexConfig,
    SourceSpan,
    SymbolKind,
    SymbolRecord,
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
