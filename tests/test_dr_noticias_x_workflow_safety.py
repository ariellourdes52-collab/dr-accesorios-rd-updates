"""Protecciones de la ejecución manual de X, sin ninguna llamada a APIs."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / ".github/workflows/dr-noticias-x-auto.yml"
AUTO = ROOT / ".github/workflows/dr-noticias-railway-dispatch.yml"


class XManualWorkflowSafety(unittest.TestCase):
    def test_manual_requires_explicit_publish_once_on_main(self):
        text = MANUAL.read_text(encoding="utf-8")
        self.assertIn("inputs.action == 'publish_once'", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("DR_NOTICIAS_X_LIVE_APPROVED: 'true'", text)
        self.assertIn("group: dr-noticias-x-auto", text)
        self.assertIn("needs: offline-tests", text)

    def test_scan_default_and_no_independent_schedule(self):
        text = MANUAL.read_text(encoding="utf-8")
        self.assertIn("default: preview", text)
        self.assertIn("inputs.action == 'preview'", text)
        self.assertIn("python scripts/dr_noticias_x_auto.py --mode scan", text)
        self.assertNotIn("  schedule:", text)

    def test_auto_requires_separate_approval_and_does_not_touch_meta(self):
        text = AUTO.read_text(encoding="utf-8")
        self.assertIn("vars.DR_NOTICIAS_X_LIVE_APPROVED == 'true'", text)
        for job in ("  instagram:", "  facebook:", "  threads:", "  x:"):
            self.assertIn(job, text)


if __name__ == "__main__":
    unittest.main()
