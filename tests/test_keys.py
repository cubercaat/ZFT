"""Paneldan AI kalitlarini kiritish."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from test_system import make_app


class KeysPanelTest(unittest.TestCase):
    def test_keys_saved_and_applied(self):
        import os
        from unittest import mock
        tmp = Path(tempfile.mkdtemp())
        app = make_app(tmp)
        env = tmp / ".env"
        with mock.patch("zft.config.ROOT", tmp), mock.patch.dict(os.environ, {}):
            code, d = app.control.panel_action("/api/keys", {"fal": "f" * 30})
        self.assertEqual(code, 200)
        self.assertTrue(app.fal.enabled)
        self.assertIn("FAL_KEY=" + "f" * 30, env.read_text())
        code, d = app.control.panel_action("/api/keys", {"fal": "short"})
        self.assertEqual(code, 400)
        app.db.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
