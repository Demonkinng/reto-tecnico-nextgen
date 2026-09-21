"""Transforma un lote CSV ficticio de Bancs en datos aptos para análisis."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID


REQUIRED_COLUMNS = [
    "transaction_id",
    "source_account_id",
    "destination_account_id",
    "amount",
    "currency",
    "occurred_at",
    "category",
]

PROCESSED_COLUMNS = [
    "transaction_id",
    "source_account_ref",
    "destination_account_ref",
    "amount_cents",
    "currency",
    "occurred_at",
    "transaction_date",
    "transaction_hour",
    "category",
]

REJECTED_COLUMNS = ["row_number", "transaction_id", "error_code"]
MANDATORY_VALUES = [
    "transaction_id",
    "source_account_id",
    "destination_account_id",
    "amount",
    "occurred_at",
]


class RowValidationError(ValueError):
    """Error estable que puede informarse sin exponer los datos de la fila."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def _parse_uuid(value: str) -> str:
    try:
        return str(UUID(value.strip()))
    except (AttributeError, ValueError):
        raise RowValidationError("invalid_uuid") from None


def _amount_to_cents(value: str) -> int:
    try:
        amount = Decimal(value.strip())
    except (AttributeError, InvalidOperation):
        raise RowValidationError("invalid_amount") from None

    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise RowValidationError("invalid_amount")

    cents = amount * 100
    if cents != cents.to_integral_value():
        raise RowValidationError("invalid_amount")
    return int(cents)


def _parse_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except (AttributeError, ValueError):
        raise RowValidationError("invalid_timestamp") from None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _account_ref(account_id: str) -> str:
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:16]


def _normalize_row(row: dict[str, str | None]) -> dict[str, str]:
    if any(not (row.get(column) or "").strip() for column in MANDATORY_VALUES):
        raise RowValidationError("missing_required")

    transaction_id = _parse_uuid(row["transaction_id"] or "")
    source_account_id = _parse_uuid(row["source_account_id"] or "")
    destination_account_id = _parse_uuid(row["destination_account_id"] or "")
    amount_cents = _amount_to_cents(row["amount"] or "")

    currency = (row.get("currency") or "USD").strip().upper() or "USD"
    if currency != "USD":
        raise RowValidationError("unsupported_currency")

    occurred_at = _parse_timestamp(row["occurred_at"] or "")
    category = (row.get("category") or "").strip().lower() or "uncategorized"

    return {
        "transaction_id": transaction_id,
        "source_account_ref": _account_ref(source_account_id),
        "destination_account_ref": _account_ref(destination_account_id),
        "amount_cents": str(amount_cents),
        "currency": currency,
        "occurred_at": occurred_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "transaction_date": occurred_at.date().isoformat(),
        "transaction_hour": f"{occurred_at.hour:02d}",
        "category": category,
    }


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def transform(input_path: Path, output_dir: Path) -> dict[str, object]:
    """Procesa un lote y devuelve el mismo resumen escrito en ``summary.json``."""

    input_path = Path(input_path)
    output_dir = Path(output_dir)

    with input_path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        actual_columns = reader.fieldnames or []
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in actual_columns]
        if missing_columns:
            raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")
        source_rows = list(reader)

    processed: list[dict[str, str]] = []
    rejected: list[dict[str, str | int]] = []
    seen_transaction_ids: set[str] = set()

    for row_number, row in enumerate(source_rows, start=2):
        raw_transaction_id = (row.get("transaction_id") or "").strip()
        try:
            normalized = _normalize_row(row)
            transaction_id = normalized["transaction_id"]
            if transaction_id in seen_transaction_ids:
                raise RowValidationError("duplicate_transaction_id")
            seen_transaction_ids.add(transaction_id)
            processed.append(normalized)
        except RowValidationError as error:
            rejected.append(
                {
                    "row_number": row_number,
                    "transaction_id": raw_transaction_id,
                    "error_code": error.error_code,
                }
            )

    processed.sort(key=lambda row: (row["occurred_at"], row["transaction_id"]))
    rejection_counts = Counter(row["error_code"] for row in rejected)
    summary: dict[str, object] = {
        "process_version": "1.0",
        "total_read": len(source_rows),
        "total_processed": len(processed),
        "total_rejected": len(rejected),
        "rejections_by_code": dict(sorted(rejection_counts.items())),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_paths = {
        "processed": output_dir / ".processed_transactions.csv.tmp",
        "rejected": output_dir / ".rejected_transactions.csv.tmp",
        "summary": output_dir / ".summary.json.tmp",
    }
    final_paths = {
        "processed": output_dir / "processed_transactions.csv",
        "rejected": output_dir / "rejected_transactions.csv",
        "summary": output_dir / "summary.json",
    }

    try:
        _write_csv(temporary_paths["processed"], PROCESSED_COLUMNS, processed)
        _write_csv(temporary_paths["rejected"], REJECTED_COLUMNS, rejected)
        temporary_paths["summary"].write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        for name in ("processed", "rejected", "summary"):
            os.replace(temporary_paths[name], final_paths[name])
    finally:
        for path in temporary_paths.values():
            path.unlink(missing_ok=True)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="CSV crudo de entrada")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directorio de salida")
    args = parser.parse_args(argv)

    try:
        summary = transform(args.input, args.output_dir)
    except (OSError, ValueError) as error:
        print(f"ETL error: {error}", file=sys.stderr)
        return 1

    result = {
        "summary": summary,
        "outputs": {
            "processed": str(args.output_dir / "processed_transactions.csv"),
            "rejected": str(args.output_dir / "rejected_transactions.csv"),
            "summary": str(args.output_dir / "summary.json"),
        },
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
