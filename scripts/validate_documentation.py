"""Valida referencias y afirmaciones verificables de la documentación."""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import unquote


LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
METRIC_DECLARATION_PATTERN = re.compile(
    r'(?:Counter|Gauge|Histogram)\(\s*["\']([a-z][a-z0-9_]*)["\']'
)
CODE_TOKEN_PATTERN = re.compile(r"`([a-z][a-z0-9_]*)`")
METRIC_SUFFIXES = ("_total", "_seconds", "_bucket", "_count", "_sum", "_created", "_jobs")

FORBIDDEN_CLAIMS = {
    "I/O-bound, concurrencia con asyncio": "FastAPI usa handlers síncronos para las operaciones Psycopg",
    "índices, particionamiento, MVCC": "el MVP no implementa particionamiento",
    "Grafana + Prometheus": "el entorno incluye Prometheus, no Grafana",
    "el ETL y la integración Bancs siguen pendientes": "el ETL ya está incluido y Bancs es una propuesta teórica",
    "Bancs/ETL, proveedor real, ciclo de vida del modelo": "el ETL y el ciclo de vida ya tienen evidencia",
    "guía completa del incidente, escalamiento y post mortem del reto todavía debe desarrollarse": "la guía ya debe estar enlazada",
}


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def validate_local_links(root: Path, documents: list[Path]) -> list[str]:
    errors: list[str] = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_without_anchor = unquote(target.split("#", 1)[0])
            if not target_without_anchor:
                continue
            resolved = (document.parent / target_without_anchor).resolve()
            if not resolved.exists():
                errors.append(
                    f"{_relative(root, document)}: enlace local inexistente: {target_without_anchor}"
                )
    return errors


def _known_metrics(telemetry_files: list[Path]) -> set[str]:
    metrics: set[str] = set()
    for path in telemetry_files:
        metrics.update(METRIC_DECLARATION_PATTERN.findall(path.read_text(encoding="utf-8")))
    generated = set()
    for metric in metrics:
        generated.update({f"{metric}_bucket", f"{metric}_count", f"{metric}_sum", f"{metric}_created"})
    return metrics | generated


def validate_metric_names(
    root: Path, documents: list[Path], telemetry_files: list[Path]
) -> list[str]:
    known = _known_metrics(telemetry_files)
    errors: list[str] = []
    for document in documents:
        tokens = set(CODE_TOKEN_PATTERN.findall(document.read_text(encoding="utf-8")))
        candidates = sorted(token for token in tokens if token.endswith(METRIC_SUFFIXES))
        for candidate in candidates:
            if candidate not in known:
                errors.append(
                    f"{_relative(root, document)}: métrica no declarada en telemetría: {candidate}"
                )
    return errors


def _plain_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(character for character in normalized if not unicodedata.combining(character)).lower()


def validate_compliance_statuses(root: Path, matrix: Path) -> list[str]:
    if not matrix.exists():
        return [f"{_relative(root, matrix)}: archivo obligatorio inexistente"]

    errors: list[str] = []
    for line_number, line in enumerate(matrix.read_text(encoding="utf-8").splitlines(), start=1):
        normalized = _plain_text(line)
        if "10 000 tps" in normalized and "no validado" not in normalized:
            errors.append(
                f"{_relative(root, matrix)}:{line_number}: 10 000 TPS debe figurar como No validado"
            )
        if "video" in normalized and "manual pendiente" not in normalized:
            errors.append(
                f"{_relative(root, matrix)}:{line_number}: video debe figurar como Manual pendiente"
            )
        if ("publicacion" in normalized or "correo" in normalized) and "manual pendiente" not in normalized:
            errors.append(
                f"{_relative(root, matrix)}:{line_number}: publicación y correo deben figurar como Manual pendiente"
            )
    return errors


def validate_forbidden_claims(root: Path, documents: list[Path]) -> list[str]:
    errors: list[str] = []
    for document in documents:
        content = document.read_text(encoding="utf-8")
        for claim, reason in FORBIDDEN_CLAIMS.items():
            if claim in content:
                errors.append(f"{_relative(root, document)}: afirmación obsoleta: {claim!r}; {reason}")
    return errors


def project_documents(root: Path) -> list[Path]:
    candidates = [root / "README.md", root / "AI-DECLARATION.md", root / "etl" / "README.md"]
    candidates.extend(sorted((root / "docs").glob("*.md")))
    candidates.extend(sorted((root / "evidence").glob("*.md")) if (root / "evidence").exists() else [])
    return [path for path in candidates if path.exists()]


def validate_repository(root: Path) -> list[str]:
    documents = project_documents(root)
    telemetry_files = [
        root / "ms-transaction" / "app" / "telemetry.py",
        root / "ms-inference-ai" / "app" / "telemetry.py",
    ]
    errors = validate_local_links(root, documents)
    errors.extend(validate_metric_names(root, documents, telemetry_files))
    errors.extend(validate_compliance_statuses(root, root / "docs" / "compliance-matrix.md"))
    errors.extend(validate_forbidden_claims(root, documents))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    errors = validate_repository(root)
    if errors:
        print("Documentación inválida:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Documentación válida: {len(project_documents(root))} archivos revisados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
