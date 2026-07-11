"""Language-neutral contracts for the deterministic repository index."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Mapping, Protocol, Sequence


SCHEMA_VERSION = "1.0.0"
PARSER_API_VERSION = "1.0.0"
GENERATOR_VERSION = "1.0.0"
INDEX_ARTIFACTS = ("schema.json", "manifest.json", "symbols.jsonl", "dependencies.jsonl")


class SymbolKind(StrEnum):
    """Normalized symbol kinds shared by all language parsers."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"


class Evidence(StrEnum):
    """Strength of evidence supporting an indexed relationship."""

    DECLARED = "declared"
    RESOLVED = "resolved"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


def stable_id(language: str, kind: str, qualified_name: str) -> str:
    """Return a readable, collision-safe identifier for a normalized symbol."""

    if not language or not kind or not qualified_name:
        raise ValueError("stable ID components must be non-empty")
    identity = json.dumps(
        [language, kind, qualified_name], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return f"{language}:{kind}:{hashlib.sha256(identity).hexdigest()}"


def _validate_path(path: str) -> None:
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or "\\" in path:
        raise ValueError("index paths must be repository-relative POSIX paths")


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """One-based inclusive source location."""

    path: str
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        _validate_path(self.path)
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("source spans require ordered, one-based lines")


@dataclass(frozen=True, slots=True)
class SymbolRecord:
    """Language-neutral record emitted for a source-defined symbol."""

    language: str
    kind: SymbolKind
    qualified_name: str
    span: SourceSpan
    signature: str | None = None
    visibility: str | None = None
    extensions: Mapping[str, object] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return stable_id(self.language, self.kind.value, self.qualified_name)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["id"] = self.id
        data["kind"] = self.kind.value
        return data


@dataclass(frozen=True, slots=True)
class DependencyRecord:
    """A statically observed relationship, qualified by its evidence."""

    language: str
    kind: str
    source_id: str
    target: str
    evidence: Evidence
    span: SourceSpan
    extensions: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["evidence"] = self.evidence.value
        return data


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Normalized output from one parser invocation."""

    symbols: tuple[SymbolRecord, ...] = ()
    dependencies: tuple[DependencyRecord, ...] = ()


class LanguageParser(Protocol):
    """Versioned interface implemented by language-specific parsers."""

    language: str
    api_version: str

    def supports(self, path: str) -> bool:
        """Return whether this parser accepts the repository-relative path."""

    def parse(self, path: str, source: bytes) -> ParseResult:
        """Parse source bytes into normalized records."""


class PythonParser:
    """Extract normalized symbols and declared imports from Python source."""

    language = "python"
    api_version = PARSER_API_VERSION

    def supports(self, path: str) -> bool:
        return PurePosixPath(path).suffix == ".py"

    def parse(self, path: str, source: bytes) -> ParseResult:
        _validate_path(path)
        if not self.supports(path):
            raise ValueError(f"Python parser does not support path: {path}")
        try:
            text = source.decode("utf-8")
            tree = ast.parse(text, filename=path, type_comments=True)
        except (UnicodeDecodeError, SyntaxError) as error:
            raise ValueError(f"cannot parse Python source: {path}") from error

        module_name = self._module_name(path)
        end_line = max(1, len(text.splitlines()))
        module = SymbolRecord(
            self.language,
            SymbolKind.MODULE,
            module_name,
            SourceSpan(path, 1, end_line),
            visibility=self._visibility(module_name.rsplit(".", 1)[-1]),
        )
        symbols: list[SymbolRecord] = [module]
        dependencies: list[DependencyRecord] = []

        def visit_body(body: list[ast.stmt], owner: SymbolRecord, class_body: bool = False) -> None:
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qualified_name = f"{owner.qualified_name}.{node.name}"
                    if isinstance(node, ast.ClassDef):
                        kind = SymbolKind.CLASS
                        signature = None
                    else:
                        kind = SymbolKind.METHOD if class_body else SymbolKind.FUNCTION
                        signature = self._signature(node)
                    record = SymbolRecord(
                        self.language,
                        kind,
                        qualified_name,
                        self._span(path, node),
                        signature=signature,
                        visibility=self._visibility(node.name),
                        extensions={"python": self._extensions(node)},
                    )
                    symbols.append(record)
                    visit_body(node.body, record, isinstance(node, ast.ClassDef))
                else:
                    for child_body in self._statement_bodies(node):
                        visit_body(child_body, owner, class_body)

                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    dependencies.extend(self._imports(path, node, owner.id))

        visit_body(tree.body, module)
        return ParseResult(
            tuple(sorted(symbols, key=lambda record: record.id)),
            tuple(sorted(dependencies, key=lambda record: deterministic_json(record.to_dict()))),
        )

    @staticmethod
    def _module_name(path: str) -> str:
        parts = list(PurePosixPath(path).with_suffix("").parts)
        if parts and parts[0] == "src":
            parts.pop(0)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) or "__init__"

    @staticmethod
    def _visibility(name: str) -> str:
        return "private" if name.startswith("_") and not name.startswith("__") else "public"

    @staticmethod
    def _span(path: str, node: ast.AST) -> SourceSpan:
        decorators = getattr(node, "decorator_list", ())
        start_line = min(
            (decorator.lineno for decorator in decorators),
            default=node.lineno,  # type: ignore[attr-defined]
        )
        return SourceSpan(path, start_line, node.end_lineno or node.lineno)  # type: ignore[attr-defined]

    @staticmethod
    def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        signature = f"({ast.unparse(node.args)})"
        if node.returns is not None:
            signature += f" -> {ast.unparse(node.returns)}"
        return signature

    @staticmethod
    def _extensions(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> dict[str, object]:
        details: dict[str, object] = {
            "decorators": [ast.unparse(decorator) for decorator in node.decorator_list]
        }
        if isinstance(node, ast.AsyncFunctionDef):
            details["async"] = True
        if isinstance(node, ast.ClassDef):
            details["bases"] = [ast.unparse(base) for base in node.bases]
        return details

    @staticmethod
    def _statement_bodies(node: ast.stmt) -> tuple[list[ast.stmt], ...]:
        bodies: list[list[ast.stmt]] = []
        for field in ("body", "orelse", "finalbody"):
            value = getattr(node, field, None)
            if isinstance(value, list):
                bodies.append(value)
        handlers = getattr(node, "handlers", ())
        bodies.extend(handler.body for handler in handlers)
        return tuple(bodies)

    def _imports(
        self, path: str, node: ast.Import | ast.ImportFrom, source_id: str
    ) -> list[DependencyRecord]:
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        else:
            prefix = "." * node.level + (node.module or "")
            separator = "" if not prefix or prefix.endswith(".") else "."
            targets = [f"{prefix}{separator}{alias.name}" for alias in node.names]
        return [
            DependencyRecord(
                self.language,
                "import",
                source_id,
                target,
                Evidence.DECLARED,
                self._span(path, node),
            )
            for target in targets
        ]


@dataclass(frozen=True, slots=True)
class IndexConfig:
    """Explicit inputs that affect index contents."""

    include: tuple[str, ...] = ("**/*.py", "*.py", "pyproject.toml")
    exclude: tuple[str, ...] = (
        ".code-index/**",
        ".git/**",
        ".venv/**",
        "**/__pycache__/**",
        "build/**",
        "dist/**",
    )
    max_file_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if self.max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(deterministic_json(asdict(self))).hexdigest()


@dataclass(frozen=True, slots=True, order=True)
class TrackedFile:
    """Content identity for one indexable, Git-tracked repository file."""

    path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        _validate_path(self.path)
        if self.size < 0:
            raise ValueError("tracked file size cannot be negative")
        if len(self.sha256) != 64 or any(character not in "0123456789abcdef" for character in self.sha256):
            raise ValueError("tracked file sha256 must be a lowercase hexadecimal digest")


def _matches(path: str, patterns: Sequence[str]) -> bool:
    candidate = PurePosixPath(path)
    return any(candidate.match(pattern) for pattern in patterns)


def _worktree_bytes(path: Path) -> bytes:
    """Read a tracked entry without following a symlink outside the repository."""

    if path.is_symlink():
        return os.fsencode(os.readlink(path))
    if not path.is_file():
        raise ValueError(f"tracked path is not a regular file: {path}")
    return path.read_bytes()


def discover_tracked_files(repository: str | Path, config: IndexConfig | None = None) -> tuple[TrackedFile, ...]:
    """Discover and hash indexable tracked files from the current worktree.

    Git is the authority for membership. Include patterns select candidates,
    exclusions take precedence, and oversized files are omitted explicitly.
    """

    root = Path(repository).resolve()
    selected = config or IndexConfig()
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(root), "ls-files", "--cached", "-z"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"cannot list tracked files in repository: {root}") from error

    records: list[TrackedFile] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        _validate_path(path)
        if not _matches(path, selected.include) or _matches(path, selected.exclude):
            continue
        content = _worktree_bytes(root.joinpath(*PurePosixPath(path).parts))
        if len(content) > selected.max_file_bytes:
            continue
        records.append(TrackedFile(path, len(content), hashlib.sha256(content).hexdigest()))

    return tuple(sorted(records))


def deterministic_json(value: object) -> bytes:
    """Encode JSON using the canonical settings required by index artifacts."""

    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def deterministic_jsonl(records: Sequence[SymbolRecord | DependencyRecord]) -> bytes:
    """Encode records in stable identifier/content order with one final newline."""

    encoded = [record.to_dict() for record in records]
    encoded.sort(key=lambda item: deterministic_json(item))
    return b"".join(deterministic_json(item) for item in encoded)


def schema_document() -> dict[str, object]:
    """Return the versioned, language-neutral schema vocabulary."""

    return {
        "schema_version": SCHEMA_VERSION,
        "parser_api_version": PARSER_API_VERSION,
        "record_types": ["dependency", "symbol"],
        "symbol_kinds": [kind.value for kind in SymbolKind],
        "evidence": [value.value for value in Evidence],
        "stable_id": "sha256(JSON([language,kind,qualified_name]))",
        "path_format": "repository-relative-posix",
        "line_format": "one-based-inclusive",
        "serialization": "UTF-8 JSON; sorted keys; compact separators; LF newline",
    }


def _aggregate_content_hash(files: Sequence[TrackedFile]) -> str:
    """Hash the ordered tracked-file identities without depending on host paths."""

    return hashlib.sha256(deterministic_json([asdict(record) for record in files])).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    """Replace one artifact atomically after fully writing it beside its target."""

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _validate_parsers(parsers: Sequence[LanguageParser]) -> None:
    for parser in parsers:
        if parser.api_version != PARSER_API_VERSION:
            raise ValueError(
                f"parser API version mismatch for {parser.language}: {parser.api_version}"
            )


def _parse_files(
    root: Path,
    files: Sequence[TrackedFile],
    parsers: Sequence[LanguageParser],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    symbols: list[dict[str, object]] = []
    dependencies: list[dict[str, object]] = []
    for tracked in files:
        source = _worktree_bytes(root.joinpath(*PurePosixPath(tracked.path).parts))
        if hashlib.sha256(source).hexdigest() != tracked.sha256:
            raise ValueError(f"tracked file changed during index build: {tracked.path}")
        for parser in parsers:
            if parser.supports(tracked.path):
                result = parser.parse(tracked.path, source)
                symbols.extend(record.to_dict() for record in result.symbols)
                dependencies.extend(record.to_dict() for record in result.dependencies)
                break
    return symbols, dependencies


def _jsonl_dicts(content: bytes) -> list[dict[str, object]]:
    return [json.loads(line) for line in content.splitlines()]


def _encode_jsonl_dicts(records: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(deterministic_json(record) for record in sorted(records, key=deterministic_json))


def _write_index(
    destination: Path,
    files: Sequence[TrackedFile],
    config: IndexConfig,
    symbols: Sequence[Mapping[str, object]],
    dependencies: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "parser_api_version": PARSER_API_VERSION,
        "configuration_fingerprint": config.fingerprint,
        "aggregate_content_hash": _aggregate_content_hash(files),
        "tracked_files": [asdict(record) for record in files],
        "artifact_counts": {
            "tracked_files": len(files),
            "symbols": len(symbols),
            "dependencies": len(dependencies),
        },
    }
    artifacts = {
        "schema.json": deterministic_json(schema_document()),
        "manifest.json": deterministic_json(manifest),
        "symbols.jsonl": _encode_jsonl_dicts(symbols),
        "dependencies.jsonl": _encode_jsonl_dicts(dependencies),
    }
    destination.mkdir(parents=True, exist_ok=True)
    for child in destination.iterdir():
        if child.name not in INDEX_ARTIFACTS:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    for name in INDEX_ARTIFACTS:
        _atomic_write(destination / name, artifacts[name])
    return manifest


def build_clean_index(
    repository: str | Path,
    output: str | Path = ".code-index",
    config: IndexConfig | None = None,
    parsers: Sequence[LanguageParser] = (PythonParser(),),
) -> dict[str, object]:
    """Build a complete deterministic index from the tracked worktree.

    Each generated file is atomically replaced. Files left by an older build are
    removed so the output directory represents only the current artifact set.
    """

    root = Path(repository).resolve()
    destination = Path(output)
    if not destination.is_absolute():
        destination = root / destination
    selected = config or IndexConfig()
    files = discover_tracked_files(root, selected)
    _validate_parsers(parsers)
    symbols, dependencies = _parse_files(root, files, parsers)
    return _write_index(destination, files, selected, symbols, dependencies)


def build_incremental_index(
    repository: str | Path,
    output: str | Path = ".code-index",
    config: IndexConfig | None = None,
    parsers: Sequence[LanguageParser] = (PythonParser(),),
) -> dict[str, object]:
    """Update an index by parsing only changed files.

    A missing or incompatible prior index safely falls back to a clean build.
    Records for removed files are discarded, while records for content-identical
    files are reused. The resulting artifacts are byte-identical to a clean build.
    """

    root = Path(repository).resolve()
    destination = Path(output)
    if not destination.is_absolute():
        destination = root / destination
    selected = config or IndexConfig()
    _validate_parsers(parsers)
    files = discover_tracked_files(root, selected)

    try:
        manifest = json.loads((destination / "manifest.json").read_bytes())
        if any(not (destination / name).is_file() for name in INDEX_ARTIFACTS):
            raise ValueError("incomplete prior index")
        compatibility = (
            manifest.get("schema_version") == SCHEMA_VERSION
            and manifest.get("generator_version") == GENERATOR_VERSION
            and manifest.get("parser_api_version") == PARSER_API_VERSION
            and manifest.get("configuration_fingerprint") == selected.fingerprint
        )
        if not compatibility:
            raise ValueError("incompatible prior index")
        previous_hashes = {
            record["path"]: record["sha256"] for record in manifest["tracked_files"]
        }
        unchanged = {
            record.path for record in files if previous_hashes.get(record.path) == record.sha256
        }
        symbols = [
            record
            for record in _jsonl_dicts((destination / "symbols.jsonl").read_bytes())
            if record["span"]["path"] in unchanged
        ]
        dependencies = [
            record
            for record in _jsonl_dicts((destination / "dependencies.jsonl").read_bytes())
            if record["span"]["path"] in unchanged
        ]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return build_clean_index(root, destination, selected, parsers)

    changed = [record for record in files if record.path not in unchanged]
    new_symbols, new_dependencies = _parse_files(root, changed, parsers)
    symbols.extend(new_symbols)
    dependencies.extend(new_dependencies)
    return _write_index(destination, files, selected, symbols, dependencies)
