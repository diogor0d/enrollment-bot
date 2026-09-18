import unittest

from vacancy_watcher.models import (
    ClassSnapshot,
    CourseListRow,
    authoritative_enrollment_verified,
    decide_target_class,
)


class ModelTests(unittest.TestCase):
    def test_zero_seat_six_cell_snapshot_is_unavailable(self):
        row = ClassSnapshot.from_cells(["PL3", "Teacher", "14", "0", "", "Sem vagas"])
        decision = decide_target_class([row], class_name="PL3", profile_alt="PL", expected_class_id="249089")
        self.assertEqual(decision.kind, "no_vacancy")

    def test_positive_seats_require_enabled_checkbox(self):
        missing = ClassSnapshot.from_cells(["PL3", "Teacher", "14", "1", "", ""], class_id="249089")
        self.assertEqual(decide_target_class([missing], class_name="PL3", profile_alt="PL", expected_class_id="249089").kind, "invalid")
        missing_id = ClassSnapshot.from_cells(
            ["PL3", "Teacher", "14", "1", "", ""],
            input_present=True,
            input_enabled=True,
        )
        self.assertEqual(decide_target_class([missing_id], class_name="PL3", profile_alt="PL", expected_class_id="249089").kind, "invalid")
        available = ClassSnapshot.from_cells(
            ["PL3", "Teacher", "14", "1", "", ""],
            class_id="249089",
            input_present=True,
            input_enabled=True,
        )
        self.assertEqual(decide_target_class([available], class_name="PL3", profile_alt="PL", expected_class_id="249089").kind, "available")

    def test_checked_target_is_already_enrolled_even_when_no_free_seats(self):
        selected = ClassSnapshot.from_cells(
            ["PL3", "Teacher", "14", "0", "", ""],
            class_id="249089",
            input_present=True,
            input_enabled=True,
            checked=True,
        )
        self.assertEqual(
            decide_target_class([selected], class_name="PL3", profile_alt="PL", expected_class_id="249089").kind,
            "already_enrolled",
        )

    def test_authoritative_verification_requires_exact_course_and_profile(self):
        rows = [CourseListRow("02038756", "Segurança e Privacidade", (("PL3", "PL"),))]
        self.assertTrue(authoritative_enrollment_verified(rows, course_code="02038756", course_title="Segurança e Privacidade", class_name="PL3", profile_alt="PL"))
        self.assertFalse(authoritative_enrollment_verified(rows, course_code="02038756", course_title="Other", class_name="PL3", profile_alt="PL"))
        self.assertFalse(authoritative_enrollment_verified(rows, course_code="02038756", course_title="Segurança e Privacidade", class_name="PL3", profile_alt="TP"))
