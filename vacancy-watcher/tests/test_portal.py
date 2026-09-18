import unittest

from vacancy_watcher.config import Settings
from vacancy_watcher.models import decide_target_class
from vacancy_watcher.portal import class_snapshots


class FakeCell:
    def __init__(self, direct_text):
        self.direct_text = direct_text

    def evaluate(self, _script):
        return self.direct_text


class FakeCells:
    def __init__(self, rendered, direct_text):
        self.rendered = rendered
        self.first = FakeCell(direct_text)

    def all_text_contents(self):
        return self.rendered

    def count(self):
        return len(self.rendered)


class FakeInput:
    def __init__(self, *, alt, value, enabled, checked):
        self.attributes = {"alt": alt, "value": value}
        self.enabled = enabled
        self.checked = checked

    def get_attribute(self, name):
        return self.attributes.get(name)

    def is_disabled(self):
        return not self.enabled

    def is_checked(self):
        return self.checked


class FakeInputs:
    def __init__(self, inputs):
        self.inputs = inputs

    def count(self):
        return len(self.inputs)

    def nth(self, index):
        return self.inputs[index]


class FakeRow:
    def __init__(self, cells, direct_text, inputs):
        self.cells = FakeCells(cells, direct_text)
        self.inputs = FakeInputs(inputs)

    def locator(self, selector):
        if selector == ":scope > td":
            return self.cells
        if selector == "input[name='inscrever']":
            return self.inputs
        raise AssertionError(f"unexpected selector: {selector}")


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def nth(self, index):
        return self.rows[index]


class FakePage:
    def __init__(self, rows):
        self.rows = FakeRows(rows)

    def locator(self, selector):
        if selector != "tr":
            raise AssertionError(f"unexpected selector: {selector}")
        return self.rows


class PortalExtractionTests(unittest.TestCase):
    def test_superscript_footnote_is_excluded_from_target_name(self):
        page = FakePage(
            [
                FakeRow(
                    ["PL31)", "Teacher", "14", "1", "", ""],
                    "PL3",
                    [FakeInput(alt="PL", value="249089", enabled=True, checked=False)],
                )
            ]
        )
        snapshots = class_snapshots(page, Settings())
        self.assertEqual(snapshots[0].name, "PL3")
        self.assertEqual(snapshots[0].cells[0], "PL3")
        self.assertEqual(
            decide_target_class(
                snapshots,
                class_name="PL3",
                profile_alt="PL",
                expected_class_id="249089",
            ).kind,
            "available",
        )


if __name__ == "__main__":
    unittest.main()
