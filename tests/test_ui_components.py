from pathlib import Path
import unittest

import pandas as pd

import screening
import ui_components


class ComponentAssetTest(unittest.TestCase):
    def test_screening_page_css_matches_the_compact_export_controls(self):
        css = screening._SCREENING_CSS
        self.assertIn(".material-symbols-rounded", css)
        self.assertIn(".screening-result-sort", css)
        self.assertIn("gap: 14px", css)
        self.assertIn("align-items: center !important", css)
        self.assertIn("height: 30px !important", css)
        self.assertIn("margin: 8px 0 -5px", css)

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
        self.assertIn("['AZ','num']", html)
        self.assertIn("['Auszug',row.links?.oereb]", html)
        self.assertNotIn("['Google',row.links?.google]", html)
        self.assertIn("row.saved?'Gemerkt':'Merken'", html)
        self.assertIn("Als «nicht interessant» markiert", html)
        self.assertIn("emit('restore',row)", html)
        self.assertIn("let dismissedOpen=false", html)
        self.assertIn("draw();\n    return panel", html)
        self.assertIn("badge-dev", html)
        self.assertIn("badge-heritage", html)
        self.assertIn("IBMPlexMono-500-normal-latin.woff2", html)
        self.assertIn("'mono num strong',row.potential", html)
        self.assertIn("'mono num strong',row.landValue", html)
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
        self.assertEqual(
            screening.resolve_table_event(
                {"type": "restore", "bfs": 4001, "parcel": "1"}, final
            ),
            ("restore", (4001, "1")),
        )

    def test_screening_statuses_use_the_design_badge_vocabulary(self):
        self.assertEqual(
            screening.screening_status_badges("frei"),
            [{"label": "Unbelastet", "tone": "clear"}],
        )
        badges = screening.screening_status_badges(
            "unbebaut (kein stehendes GWR-Gebäude) · "
            "Gestaltungsplan — AZ evtl. überlagert · Gewässerraum (4 m²)"
        )
        self.assertEqual(
            [(badge["label"], badge["tone"]) for badge in badges],
            [("Gestaltungsplan", "dev"), ("Gewässerabstand", "water")],
        )
        self.assertEqual(
            screening.screening_status_badges("seltene amtliche Beschränkung"),
            [{
                "label": "Prüfen",
                "tone": "muted",
                "detail": "seltene amtliche Beschränkung",
            }],
        )


if __name__ == "__main__":
    unittest.main()
