import unittest

from bacchus.chart_evidence import chart_findings


def findings(narration, values, unit, brief=""):
    source = {"lines": [{"text": narration}], "intention": brief}
    chart = {"kind": "bar-chart", "id": "comparison", "unit": unit,
             "items": [{"label": label, "value": value} for label, value in values]}
    return chart_findings(source, [{"startLine": 1, "endLine": 1, "layers": [chart]}])


class ChartEvidenceTests(unittest.TestCase):
    def test_spoken_numbers_are_grounded_by_label_and_unit(self):
        spoken = "North received two million. South received four million."
        self.assertFalse(findings(spoken, [("North", 2), ("South", 4)], "million"))
        self.assertEqual(len(findings(spoken, [("North", 90), ("South", 110)], "million")), 2)
        self.assertEqual(len(findings(spoken, [("North", 4), ("South", 2)], "million")), 2)
        self.assertTrue(findings(spoken, [("North", 2)], "percent"))
        self.assertTrue(findings(spoken, [("North", 2)], ""))

    def test_exact_brief_data_can_ground_a_chart(self):
        self.assertFalse(findings("", [("North", 2.5), ("South", -3)], "%", "North: 2.5 percent\nSouth: −3%"))
        self.assertFalse(findings("", [("North", 1250)], "USD", "North: $1,250"))
        self.assertTrue(findings("", [("North", 1)], "USD", "North: $1 million"))
        self.assertTrue(findings("North could reach 20 percent if sales increase.", [("North", 20)], "%"))
        self.assertTrue(findings("North received below 25 percent.", [("North", 25)], "%"))
        self.assertTrue(findings("If North received 25 percent, South would benefit.", [("North", 25)], "%"))
