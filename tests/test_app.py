import os
import shutil
import sqlite3
import tempfile
import unittest

from streamlit.testing.v1 import AppTest

import paths


class AppRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.tempdir.name, "results.sqlite")
        shutil.copy2(paths.SEED_DB, self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("DELETE FROM oereb_cache")

        self.original_database = paths.DB
        paths.DB = self.database

    def tearDown(self):
        paths.DB = self.original_database
        self.tempdir.cleanup()

    def test_controls_mixed_results_and_economic_indicator(self):
        app = AppTest.from_file(
            os.path.join(paths.HERE, "app.py"), default_timeout=30
        ).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.number_input[0].min, 130)
        self.assertEqual(app.slider[0].min, 300)
        self.assertEqual(app.number_input[1].max, 50)

        app.selectbox[0].select("Alle").run()
        self.assertFalse(app.exception)
        frame = app.dataframe[0].value
        self.assertEqual(
            frame["Typ"].value_counts().to_dict(),
            {"bebaut": 10, "unbebaut": 10},
        )
        self.assertIn("≈ Landwert / Potenzial-GF", frame.columns)
        self.assertTrue(frame["Preisebene"].eq("Kanton AG").all())
        self.assertTrue(frame["Preisstand"].eq("2021 Q2").all())
        self.assertTrue(frame["≈ Landwert / Potenzial-GF"].notna().all())

        app.number_input[1].set_value(50).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[0].value), 50)


if __name__ == "__main__":
    unittest.main()
