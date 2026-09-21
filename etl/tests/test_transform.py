import csv
import json
import tempfile
import unittest
from pathlib import Path

from etl.transform import REQUIRED_COLUMNS, transform


class TransformTests(unittest.TestCase):
    def write_input(self, directory: Path, rows: list[dict[str, str]], fieldnames=None) -> Path:
        input_path = directory / "input.csv"
        with input_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames or REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return input_path

    def read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8", newline="") as file:
            return list(csv.DictReader(file))

    def test_normalizes_valid_rows_and_sorts_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [
                {
                    "transaction_id": "00000000-0000-4000-8000-000000000102",
                    "source_account_id": "00000000-0000-4000-8000-000000000001",
                    "destination_account_id": "00000000-0000-4000-8000-000000000002",
                    "amount": "25.50",
                    "currency": " usd ",
                    "occurred_at": "2026-09-20T09:15:00-05:00",
                    "category": " Groceries ",
                },
                {
                    "transaction_id": "00000000-0000-4000-8000-000000000101",
                    "source_account_id": "00000000-0000-4000-8000-000000000002",
                    "destination_account_id": "00000000-0000-4000-8000-000000000003",
                    "amount": "10",
                    "currency": "",
                    "occurred_at": "2026-09-20 08:00:00",
                    "category": "",
                },
            ]

            summary = transform(self.write_input(root, rows), root / "output")
            processed = self.read_csv(root / "output" / "processed_transactions.csv")

            self.assertEqual(summary["total_read"], 2)
            self.assertEqual(summary["total_processed"], 2)
            self.assertEqual(summary["total_rejected"], 0)
            self.assertEqual(
                [row["transaction_id"] for row in processed],
                [
                    "00000000-0000-4000-8000-000000000101",
                    "00000000-0000-4000-8000-000000000102",
                ],
            )
            self.assertEqual(processed[0]["amount_cents"], "1000")
            self.assertEqual(processed[0]["currency"], "USD")
            self.assertEqual(processed[0]["occurred_at"], "2026-09-20T08:00:00Z")
            self.assertEqual(processed[0]["transaction_date"], "2026-09-20")
            self.assertEqual(processed[0]["transaction_hour"], "08")
            self.assertEqual(processed[0]["category"], "uncategorized")
            self.assertEqual(processed[1]["occurred_at"], "2026-09-20T14:15:00Z")
            self.assertEqual(processed[1]["category"], "groceries")
            self.assertRegex(processed[0]["source_account_ref"], r"^[0-9a-f]{16}$")
            self.assertNotEqual(
                processed[0]["source_account_ref"], rows[1]["source_account_id"]
            )

    def test_rejects_each_supported_validation_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = {
                "transaction_id": "00000000-0000-4000-8000-000000000201",
                "source_account_id": "00000000-0000-4000-8000-000000000001",
                "destination_account_id": "00000000-0000-4000-8000-000000000002",
                "amount": "1.00",
                "currency": "USD",
                "occurred_at": "2026-09-20T10:00:00Z",
                "category": "transfer",
            }
            rows = [
                {**base, "transaction_id": "", "source_account_id": ""},
                {**base, "transaction_id": "not-a-uuid"},
                {**base, "transaction_id": "00000000-0000-4000-8000-000000000202", "amount": "1.999"},
                {**base, "transaction_id": "00000000-0000-4000-8000-000000000203", "currency": "EUR"},
                {**base, "transaction_id": "00000000-0000-4000-8000-000000000204", "occurred_at": "yesterday"},
                base,
                base,
            ]

            summary = transform(self.write_input(root, rows), root / "output")
            rejected = self.read_csv(root / "output" / "rejected_transactions.csv")

            self.assertEqual(summary["total_read"], 7)
            self.assertEqual(summary["total_processed"], 1)
            self.assertEqual(summary["total_rejected"], 6)
            self.assertEqual(
                [row["error_code"] for row in rejected],
                [
                    "missing_required",
                    "invalid_uuid",
                    "invalid_amount",
                    "unsupported_currency",
                    "invalid_timestamp",
                    "duplicate_transaction_id",
                ],
            )
            self.assertEqual(summary["rejections_by_code"]["invalid_amount"], 1)

    def test_missing_column_stops_the_batch_without_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fieldnames = [column for column in REQUIRED_COLUMNS if column != "amount"]
            input_path = self.write_input(root, [], fieldnames=fieldnames)
            output_dir = root / "output"

            with self.assertRaisesRegex(ValueError, "Missing required columns: amount"):
                transform(input_path, output_dir)

            self.assertFalse(output_dir.exists())

    def test_summary_file_matches_returned_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_path = self.write_input(root, [])

            summary = transform(input_path, root / "output")

            saved = json.loads((root / "output" / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, summary)
            self.assertEqual(saved["process_version"], "1.0")


if __name__ == "__main__":
    unittest.main()
