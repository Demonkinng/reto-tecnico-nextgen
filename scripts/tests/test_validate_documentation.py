import tempfile
import unittest
from pathlib import Path

from scripts.validate_documentation import (
    validate_compliance_statuses,
    validate_local_links,
    validate_metric_names,
)


class DocumentationValidationTests(unittest.TestCase):
    def test_reports_broken_local_markdown_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "docs").mkdir()
            source = root / "README.md"
            source.write_text("[guía](docs/missing.md)\n", encoding="utf-8")

            errors = validate_local_links(root, [source])

            self.assertEqual(errors, ["README.md: enlace local inexistente: docs/missing.md"])

    def test_ignores_web_anchor_and_image_links_when_targets_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "docs" / "images").mkdir(parents=True)
            (root / "docs" / "images" / "architecture.jpg").write_bytes(b"demo")
            source = root / "docs" / "design.md"
            source.write_text(
                "[web](https://example.com) [sección](#section) "
                "![diagrama](images/architecture.jpg)\n",
                encoding="utf-8",
            )

            self.assertEqual(validate_local_links(root, [source]), [])

    def test_reports_metric_not_declared_in_telemetry_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            telemetry = root / "service" / "telemetry.py"
            telemetry.parent.mkdir()
            telemetry.write_text(
                'Counter("requests_total", "requests")\n'
                'Histogram("request_duration_seconds", "duration")\n',
                encoding="utf-8",
            )
            document = root / "runbook.md"
            document.write_text(
                "Use `requests_total`, `request_duration_seconds_bucket` "
                "y `invented_metric_total`.\n",
                encoding="utf-8",
            )

            errors = validate_metric_names(root, [document], [telemetry])

            self.assertEqual(
                errors,
                ["runbook.md: métrica no declarada en telemetría: invented_metric_total"],
            )

    def test_requires_honest_status_for_manual_and_capacity_items(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            matrix = root / "docs" / "compliance-matrix.md"
            matrix.parent.mkdir()
            matrix.write_text(
                "| Capacidad 10 000 TPS | Cumple | prueba |\n"
                "| Video | Cumple | enlace |\n"
                "| Publicación y correo | Cumple | enlace |\n",
                encoding="utf-8",
            )

            errors = validate_compliance_statuses(root, matrix)

            self.assertEqual(len(errors), 3)
            self.assertTrue(any("No validado" in error for error in errors))
            self.assertEqual(sum("Manual pendiente" in error for error in errors), 2)


if __name__ == "__main__":
    unittest.main()
