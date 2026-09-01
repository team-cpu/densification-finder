import unittest

import navigation


class FakeState(dict):
    """`st.session_state` behaves as a dict for everything this module does.

    Testing against a dict rather than a Streamlit runtime keeps the page
    reconciliation — the part that has an ordering bug waiting in it — provable
    without starting an app.
    """


class NavigationTest(unittest.TestCase):
    def test_a_pending_request_becomes_the_current_page(self):
        state = FakeState({navigation.PENDING: "Analyse"})

        navigation.reconcile(state)

        self.assertEqual(state[navigation.PAGE], "Analyse")
        self.assertNotIn(navigation.PENDING, state)

    def test_reconcile_defaults_to_screening_when_nothing_is_set(self):
        state = FakeState()

        navigation.reconcile(state)

        self.assertEqual(state[navigation.PAGE], "Screening")

    def test_reconcile_leaves_a_chosen_page_alone(self):
        state = FakeState({navigation.PAGE: "Merkliste"})

        navigation.reconcile(state)

        self.assertEqual(state[navigation.PAGE], "Merkliste")

    def test_an_unknown_page_is_refused(self):
        """A typo in a `go_to` call must fail where it is written, not paint an
        empty page that looks like missing data."""
        with self.assertRaises(ValueError):
            navigation.go_to("Aquisition", FakeState())

    def test_go_to_records_a_request_without_touching_the_current_page(self):
        """The write has to be deferred: Streamlit refuses a write to a
        widget-keyed value once that widget has been created this run, so the
        request is parked and reconciled at the top of the next one."""
        state = FakeState({navigation.PAGE: "Screening"})

        navigation.go_to("Akquisition", state)

        self.assertEqual(state[navigation.PENDING], "Akquisition")
        self.assertEqual(state[navigation.PAGE], "Screening")


    def test_go_back_returns_to_the_page_a_jump_came_from(self):
        """Analyse is reachable from Screening, the Merkliste and the board.
        Sending every "back" to Screening would lose the board position of
        anyone who arrived from it."""
        state = FakeState({navigation.PAGE: "Akquisition"})

        navigation.go_to("Analyse", state)
        navigation.reconcile(state)
        navigation.go_back(state)

        self.assertEqual(state[navigation.PENDING], "Akquisition")

    def test_go_back_falls_to_the_default_when_no_origin_was_recorded(self):
        """A parcel opened by a deep link, or by a session restored from an
        older release, has no origin to return to."""
        state = FakeState({navigation.PAGE: "Analyse"})

        navigation.go_back(state)

        self.assertEqual(state[navigation.PENDING], "Screening")


if __name__ == "__main__":
    unittest.main()
