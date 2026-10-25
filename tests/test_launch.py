"""O'rnatishdan keyingi birinchi ishga tushish: egani kod bilan belgilash va darhol birinchi video."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_system import FakeTG, make_app, msg


class LaunchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = make_app(self.tmp, OWNER_ID="0")
        self.app.settings.owner_id = 0
        self.tg: FakeTG = self.app.tg
        self.made = []
        self.app.run_in_background = lambda fn, *a: self.made.append((fn, a))

    def tearDown(self):
        self.app.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_claim_owner_with_code(self):
        with mock.patch.dict(os.environ, {"OWNER_CLAIM_CODE": "482913"}):
            self.app.bot.handle_update(msg(55, "/egasi 111111"))
            self.assertEqual(self.app.settings.owner_id, 0)
            self.assertIn("noto'g'ri", self.tg.texts(55)[-1])
            self.app.bot.handle_update(msg(55, "/egasi 482913"))
            self.assertEqual(self.app.settings.owner_id, 55)
            self.assertEqual(self.app.db.get("owner_id"), "55")
            self.assertTrue(any("ZFT ishga tushdi" in t for t in self.tg.texts(55)))
            self.assertEqual(len(self.made), 1)                 # birinchi video navbatga qo'yildi
            self.app.bot.handle_update(msg(66, "/egasi 482913"))  # ikkinchi odam egallay olmaydi
            self.assertEqual(self.app.settings.owner_id, 55)
            self.app.first_start()                               # qayta ishga tushganda takrorlanmaydi
            self.assertEqual(len(self.made), 1)

    def test_no_code_configured(self):
        with mock.patch.dict(os.environ, {"OWNER_CLAIM_CODE": ""}):
            self.app.bot.handle_update(msg(55, "/egasi "))
            self.assertEqual(self.app.settings.owner_id, 0)


if __name__ == "__main__":
    unittest.main()
