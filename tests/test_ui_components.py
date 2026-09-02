from pathlib import Path
import unittest

import pandas as pd

import screening
import ui_components


class ComponentAssetTest(unittest.TestCase):
    def test_board_is_local_and_contains_native_drag_and_drop(self):
        html = (
            Path(ui_components.__file__).with_name("components")
            / "acquisition_board"
            / "index.html"
        ).read_text()

        self.assertIn("draggable=true", html)
        self.assertIn("dragstart", html)
        self.assertIn("streamlit:setComponentValue", html)
        self.assertNotIn("https://unpkg.com", html)

    def test_merkliste_is_a_local_action_table(self):
        html = (
            Path(ui_components.__file__).with_name("components")
            / "merkliste"
            / "index.html"
        ).read_text()

        self.assertIn("Eigentümer", html)
        self.assertIn("Analyse", html)
        self.assertIn("streamlit:setComponentValue", html)
        self.assertNotIn("https://unpkg.com", html)

    def test_screening_is_a_local_action_table(self):
        html = (
            Path(ui_components.__file__).with_name("components")
            / "screening_table"
            / "index.html"
        ).read_text()

        self.assertIn("Nicht interessant", html)
        self.assertIn("Analyse", html)
        self.assertIn("streamlit:setComponentValue", html)
        self.assertNotIn("https://unpkg.com", html)


class EventConsumptionTest(unittest.TestCase):
    def test_one_component_event_is_consumed_once(self):
        state = {}
        event = {"eventId": "event-1", "type": "move"}

        self.assertEqual(ui_components.consume_event(event, "board", state), event)
        self.assertIsNone(ui_components.consume_event(event, "board", state))

    def test_screening_event_cannot_name_a_row_outside_the_result(self):
        final = pd.DataFrame({"bfs": [4001], "parcel": ["1"]})

        self.assertIsNone(
            screening.resolve_table_event(
                {"type": "analyse", "bfs": 9999, "parcel": "2"}, final
            )
        )
        self.assertEqual(
            screening.resolve_table_event(
                {"type": "save", "bfs": 4001, "parcel": "1"}, final
            ),
            ("save", (4001, "1")),
        )


if __name__ == "__main__":
    unittest.main()
