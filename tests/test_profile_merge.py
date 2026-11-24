"""Tests for merging a re-uploaded resume into a profile.

Run from the project root:  python -m unittest discover tests
"""

import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from profile_merge import merge_profiles  # noqa: E402
from profile_schema import empty_profile  # noqa: E402


def job(company, title, highlights=(), end_date="", is_current=False):
    return {
        "company": company, "title": title, "location": "", "start_date": "2020",
        "end_date": end_date, "is_current": is_current, "highlights": list(highlights),
    }


def profile(**sections):
    data = empty_profile()
    basics = sections.pop("basics", {})
    data["basics"].update(basics)
    data.update(sections)
    return data


class MergeWithBaselineTests(unittest.TestCase):
    def setUp(self):
        # What the LLM parsed from the old resume.
        self.baseline = profile(
            basics={"full_name": "Asha Rao", "headline": "Backend Engineer", "location": "Pune"},
            experience=[job("Acme", "Engineer", ["Built APIs"], is_current=True)],
            achievements=["Hackathon winner"],
        )

    def test_untouched_fields_take_the_new_resume(self):
        current = copy.deepcopy(self.baseline)
        new = profile(basics={"full_name": "Asha Rao", "headline": "Senior Backend Engineer", "location": "Pune"})
        merged, summary = merge_profiles(current, new, self.baseline)
        self.assertEqual(merged["basics"]["headline"], "Senior Backend Engineer")
        self.assertIn("Basics", summary["updated"])

    def test_hand_edited_fields_are_kept(self):
        current = copy.deepcopy(self.baseline)
        current["basics"]["headline"] = "Platform Engineer (my wording)"
        new = profile(basics={"headline": "Senior Backend Engineer"})
        merged, _ = merge_profiles(current, new, self.baseline)
        self.assertEqual(merged["basics"]["headline"], "Platform Engineer (my wording)")

    def test_empty_new_values_never_erase(self):
        current = copy.deepcopy(self.baseline)
        merged, _ = merge_profiles(current, empty_profile(), self.baseline)
        self.assertEqual(merged["basics"]["location"], "Pune")
        self.assertEqual(len(merged["experience"]), 1)

    def test_entries_pair_despite_case_and_punctuation(self):
        current = copy.deepcopy(self.baseline)
        new = profile(experience=[job("ACME.", "engineer", ["Built APIs", "Cut latency 40%"],
                                      end_date="2024", is_current=False)])
        merged, summary = merge_profiles(current, new, self.baseline)
        self.assertEqual(len(merged["experience"]), 1)
        entry = merged["experience"][0]
        self.assertEqual(entry["highlights"], ["Built APIs", "Cut latency 40%"])
        self.assertEqual(entry["end_date"], "2024")
        self.assertFalse(entry["is_current"])
        # Untouched since the last resume, so the new resume's spelling wins.
        self.assertEqual(entry["company"], "ACME.")
        self.assertEqual(summary["updated"][-1], "Experience: engineer at ACME.")

    def test_new_entries_are_added(self):
        current = copy.deepcopy(self.baseline)
        new = profile(experience=[job("Acme", "Engineer"), job("Globex", "Lead")])
        merged, summary = merge_profiles(current, new, self.baseline)
        self.assertEqual([e["company"] for e in merged["experience"]], ["Acme", "Globex"])
        self.assertIn("Experience: Lead at Globex", summary["added"])

    def test_entries_missing_from_new_resume_are_kept(self):
        current = copy.deepcopy(self.baseline)
        current["experience"].append(job("Initech", "Intern"))  # added by hand
        new = profile(experience=[job("Globex", "Lead")])
        merged, summary = merge_profiles(current, new, self.baseline)
        companies = [e["company"] for e in merged["experience"]]
        self.assertEqual(companies, ["Acme", "Initech", "Globex"])
        self.assertIn("Experience: Engineer at Acme", summary["not_on_resume"])
        self.assertIn("Experience: Intern at Initech", summary["not_on_resume"])

    def test_deleted_entries_and_items_are_not_brought_back(self):
        current = copy.deepcopy(self.baseline)
        current["experience"] = []        # user deleted the Acme job
        current["achievements"] = []      # and the achievement
        new = profile(experience=[job("Acme", "Engineer")], achievements=["Hackathon winner"])
        merged, summary = merge_profiles(current, new, self.baseline)
        self.assertEqual(merged["experience"], [])
        self.assertEqual(merged["achievements"], [])
        self.assertIn("Experience: Engineer at Acme", summary["skipped"])

    def test_additional_context_is_always_kept(self):
        current = copy.deepcopy(self.baseline)
        current["additional_context"] = "Open to relocation."
        new = profile(additional_context="something the model made up")
        merged, _ = merge_profiles(current, new, self.baseline)
        self.assertEqual(merged["additional_context"], "Open to relocation.")

    def test_list_caps_are_respected(self):
        current = profile(achievements=[f"Old {i}" for i in range(35)])
        new = profile(achievements=[f"New {i}" for i in range(10)])
        merged, _ = merge_profiles(current, new, current)
        self.assertEqual(len(merged["achievements"]), 40)
        self.assertEqual(merged["achievements"][:35], current["achievements"])


class MergeWithoutBaselineTests(unittest.TestCase):
    def test_only_blank_fields_are_filled(self):
        current = profile(basics={"full_name": "Asha Rao", "email": "asha@example.com", "headline": "Typed by hand"})
        new = profile(basics={"full_name": "A. Rao", "email": "other@example.com",
                              "headline": "From resume", "phone": "+91 99999"})
        merged, _ = merge_profiles(current, new, None)
        self.assertEqual(merged["basics"]["full_name"], "Asha Rao")
        self.assertEqual(merged["basics"]["email"], "asha@example.com")
        self.assertEqual(merged["basics"]["headline"], "Typed by hand")
        self.assertEqual(merged["basics"]["phone"], "+91 99999")

    def test_first_upload_into_empty_profile_fills_everything(self):
        current = profile(basics={"full_name": "Asha Rao", "email": "asha@example.com"})
        new = profile(basics={"headline": "Engineer"}, experience=[job("Acme", "Engineer", ["Built APIs"])],
                      achievements=["Hackathon winner"])
        merged, summary = merge_profiles(current, new, None)
        self.assertEqual(merged["experience"], new["experience"])
        self.assertEqual(merged["achievements"], ["Hackathon winner"])
        self.assertEqual(summary["not_on_resume"], [])


class UndoUploadTests(unittest.TestCase):
    """The undo snapshot, against a throwaway SQLite database."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RESUME_DESK_DB"] = os.path.join(cls.tmp.name, "test.db")
        import importlib

        import db
        cls.db = importlib.reload(db)  # pick up the temporary path
        cls.db.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def upload(self, user_id, data, version, name):
        return self.db.save_profile(
            user_id, data, version,
            resume={"filename": name, "pdf": b"%PDF", "text": name, "baseline": data},
        )

    def test_undo_restores_profile_and_resume(self):
        user_id = self.db.create_user("undo@example.com", "x", profile(basics={"headline": "Before"}))
        first = self.upload(user_id, profile(basics={"headline": "Old resume"}), 1, "old.pdf")
        second = self.upload(user_id, profile(basics={"headline": "New resume"}), first["version"], "new.pdf")
        self.assertTrue(second["can_undo_upload"])

        restored = self.db.undo_resume_upload(user_id, second["version"])
        self.assertEqual(restored["profile"]["basics"]["headline"], "Old resume")
        self.assertEqual(restored["resume"]["filename"], "old.pdf")
        self.assertFalse(restored["can_undo_upload"])  # only one level of undo
        self.assertEqual(self.db.get_merge_inputs(user_id)["resume_text"], "old.pdf")

        with self.assertRaises(self.db.NothingToUndo):
            self.db.undo_resume_upload(user_id, restored["version"])

    def test_undo_is_refused_after_later_edits(self):
        user_id = self.db.create_user("edited@example.com", "x", None)
        uploaded = self.upload(user_id, profile(basics={"headline": "From resume"}), 1, "cv.pdf")
        edited = self.db.save_profile(user_id, profile(basics={"headline": "Hand edit"}), uploaded["version"])
        self.assertFalse(edited["can_undo_upload"])
        with self.assertRaises(self.db.ProfileConflict):
            self.db.undo_resume_upload(user_id, edited["version"])
        self.assertEqual(self.db.get_profile(user_id)["profile"]["basics"]["headline"], "Hand edit")

    def test_failed_upload_leaves_no_snapshot(self):
        user_id = self.db.create_user("stale@example.com", "x", None)
        with self.assertRaises(self.db.ProfileConflict):
            self.upload(user_id, profile(basics={"headline": "x"}), 99, "cv.pdf")
        self.assertFalse(self.db.get_profile(user_id)["can_undo_upload"])
        with self.assertRaises(self.db.NothingToUndo):
            self.db.undo_resume_upload(user_id, 1)


if __name__ == "__main__":
    unittest.main()
