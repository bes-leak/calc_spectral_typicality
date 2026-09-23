# -*- coding: utf-8 -*-
"""The library of class profiles and its persistence in the project."""
import json
import os
import tempfile
import unittest

from .support import FakeProject, library_mod, make_profile, profile_mod

ProfileLibrary = library_mod.ProfileLibrary


class LibraryTest(unittest.TestCase):

    def setUp(self):
        self.project = FakeProject()
        self.library = ProfileLibrary(self.project)
        for i, name in enumerate(("pine", "spruce", "swamp")):
            self.library.add(make_profile(name, seed=30 + i))

    def test_starts_empty(self):
        self.assertEqual(len(ProfileLibrary(FakeProject())), 0)
        self.assertFalse(ProfileLibrary(FakeProject()))

    def test_holds_several_classes_at_once(self):
        self.assertEqual(self.library.names(), ["pine", "spruce", "swamp"])
        self.assertEqual(len(self.library), 3)
        self.assertIn("pine", self.library)

    def test_the_last_one_added_becomes_active(self):
        self.assertEqual(self.library.active_name, "swamp")
        self.assertEqual(self.library.set_active("pine"), "pine")
        self.assertEqual(self.library.active.name, "pine")

    def test_an_unknown_class_gives_nothing(self):
        self.assertIsNone(self.library.get("no such class"))

    def test_adding_the_same_name_replaces_rather_than_duplicates(self):
        self.library.add(make_profile("pine", seed=99))
        self.assertEqual(len(self.library), 3)

    def test_removing_a_profile(self):
        self.assertTrue(self.library.remove("spruce"))
        self.assertEqual(self.library.names(), ["pine", "swamp"])
        self.assertFalse(self.library.remove("spruce"))

    def test_survives_a_reload_of_the_project(self):
        self.library.set_active("pine")
        reopened = ProfileLibrary(self.project)
        self.assertEqual(reopened.names(), ["pine", "spruce", "swamp"])
        self.assertEqual(reopened.active_name, "pine")

    def test_clearing_removes_the_project_entry(self):
        self.library.clear()
        self.assertEqual(ProfileLibrary(self.project).names(), [])

    def test_options_prefer_the_legend_label(self):
        """The list shows the legend label, but matching still goes by value."""
        self.library.add(make_profile("1200", label="Pine forest", seed=41))
        options = dict(self.library.options())
        self.assertEqual(options["Pine forest"], "1200")
        self.assertEqual(self.library.title("1200"), "Pine forest")
        self.assertEqual(self.library.title("pine"), "pine")


class LibraryFileTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.project = FakeProject()
        self.library = ProfileLibrary(self.project)
        for i, name in enumerate(("pine", "spruce")):
            self.library.add(make_profile(name, seed=50 + i))

    def test_saving_and_loading_the_whole_library(self):
        path = os.path.join(self.dir, "classes.classes.json")
        self.library.save(path)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["format"], library_mod.FORMAT)

        empty = ProfileLibrary(FakeProject())
        self.assertEqual(sorted(empty.load(path)), ["pine", "spruce"])
        self.assertEqual(empty.names(), ["pine", "spruce"])

    def test_the_same_call_loads_a_single_profile_file(self):
        path = os.path.join(self.dir, "one.class.json")
        make_profile("birch", seed=52).save(path)
        self.assertEqual(self.library.load(path), ["birch"])
        self.assertEqual(self.library.names(), ["birch", "pine", "spruce"])

    def test_loading_can_replace_instead_of_merging(self):
        path = os.path.join(self.dir, "one.class.json")
        make_profile("birch", seed=53).save(path)
        self.library.load(path, merge=False)
        self.assertEqual(self.library.names(), ["birch"])

    def test_a_foreign_file_is_refused(self):
        path = os.path.join(self.dir, "foreign.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"format": "something/else", "profiles": {}}, fh)
        with self.assertRaises(ValueError):
            self.library.load(path)


class LegacyMigrationTest(unittest.TestCase):

    def test_an_old_single_profile_becomes_a_library_of_one(self):
        project = FakeProject()
        profile_mod.save_to_project(project, make_profile("old", seed=60))
        # the old entry lived under a different key
        raw, _ = project.readEntry(profile_mod.PROJECT_SCOPE, profile_mod.PROJECT_ENTRY)
        project.removeEntry(profile_mod.PROJECT_SCOPE, profile_mod.PROJECT_ENTRY)
        project.writeEntry(library_mod.PROJECT_SCOPE, library_mod.LEGACY_ENTRY, raw)

        library = ProfileLibrary(project)
        self.assertEqual(library.names(), ["old"])
        self.assertEqual(library.active_name, "old")


if __name__ == "__main__":
    unittest.main()
