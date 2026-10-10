"""Non-network safeguards for DR Agenda live publishing."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import publish_dr_agenda_telegram as publisher
from dr_agenda_publisher_core import AgendaError, snapshot


class SafetyGates(unittest.TestCase):
    def test_default_mode_must_not_write(self):
        with patch.dict(os.environ, {
            'DR_AGENDA_CI_APPROVED': 'TELEGRAM_ADMIN_CONFIRMED',
            'DR_AGENDA_PAYLOAD_JSON': '{"fake":"data"}',
            'DR_AGENDA_RUN_MODE': 'auditar'
        }, clear=True), patch.object(publisher, 'build_workspace', return_value=({}, {'id': 'evt-20270115-demo'})), patch.object(publisher, 'publish') as invoke:
            publisher.main()
        invoke.assert_called_once()
        self.assertIs(invoke.call_args.kwargs['preflight'], True)

    def test_live_is_disabled_without_repo_variable(self):
        with patch.dict(os.environ, {
            'DR_AGENDA_CI_APPROVED': 'TELEGRAM_ADMIN_CONFIRMED',
            'DR_AGENDA_PAYLOAD_JSON': '{"fake":"data"}',
            'DR_AGENDA_RUN_MODE': 'publicar'
        }, clear=True), patch.object(publisher, 'build_workspace') as build:
            with self.assertRaisesRegex(AgendaError, 'DESACTIVADA'):
                publisher.main()
            build.assert_not_called()

    def test_manifest_backup_survives_workspace_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / 'artifact'
            with patch.dict(os.environ, {'DR_AGENDA_BACKUP_DIR': str(external)}):
                with tempfile.TemporaryDirectory() as work:
                    snapshot(Path(work), {'events': [], 'schemaVersion': 1},
                             'sites/dr-accesorios-rd/versions/old', {'/radar.json': 'abc'}, None)
            self.assertEqual(len(list(external.glob('*/firebase-manifest.json'))), 1)


if __name__ == '__main__':
    unittest.main()
