import json
import subprocess

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.index import build_clean_index, check_index, explain_symbol


def _git(repository, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True)


def _repository(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    (repository / "example.py").write_text("import os\n\ndef hello(name: str) -> str:\n    return name\n")
    _git(repository, "add", ".")
    return repository


def test_check_is_read_only_and_reports_stale_artifacts(tmp_path) -> None:
    repository = _repository(tmp_path)
    build_clean_index(repository)
    manifest = repository / ".code-index" / "manifest.json"
    original = manifest.read_bytes()

    assert check_index(repository) == ()
    manifest.write_text("{}\n")
    assert check_index(repository) == ("manifest.json",)
    assert manifest.read_text() == "{}\n"
    manifest.write_bytes(original)


def test_explain_loads_a_symbol_and_its_outgoing_relationships(tmp_path) -> None:
    repository = _repository(tmp_path)
    build_clean_index(repository)

    explanation = explain_symbol(repository, "example")

    assert explanation["symbol"]["kind"] == "module"
    assert [item["target"] for item in explanation["relationships"]] == ["os"]
    with pytest.raises(ValueError, match="not indexed"):
        explain_symbol(repository, "missing")


def test_cli_build_check_and_explain(tmp_path, monkeypatch, capsys) -> None:
    repository = _repository(tmp_path)
    monkeypatch.chdir(repository)

    main(["index", "build"])
    assert "Built clean index" in capsys.readouterr().out
    main(["index", "check"])
    assert capsys.readouterr().out == "Index is current.\n"
    main(["index", "explain", "example.hello"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["symbol"]["signature"] == "(name: str) -> str"
    assert payload["relationships"] == []


def test_cli_check_fails_for_a_missing_index(tmp_path, monkeypatch, capsys) -> None:
    repository = _repository(tmp_path)
    monkeypatch.chdir(repository)

    with pytest.raises(SystemExit) as exit_info:
        main(["index", "check"])

    assert exit_info.value.code == 1
    assert "Index is stale" in capsys.readouterr().err
