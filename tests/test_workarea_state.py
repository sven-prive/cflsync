# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for state-backed workarea operations."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cflsync import PageState, StateError, Workarea
from tests.support import example_page_state, temporary_workarea


class TestWorkareaPageStates(unittest.TestCase):

    def test_enumerates_all_page_state_paths_by_numeric_page_id(self) -> None:
        with temporary_workarea() as workarea:
            first = example_page_state("9", directory="First page")
            second = example_page_state("10", directory="Second page")
            second.save(workarea.cache_path(second.page.id))
            first.save(workarea.cache_path(first.page.id))

            paths = workarea.page_state_paths()

            self.assertEqual(list(paths), ["9", "10"])
            self.assertEqual({page_id: PageState.load(path) for page_id, path in paths.items()}, {"9": first, "10": second})

    def test_rejects_a_malformed_cache_entry(self) -> None:
        with temporary_workarea() as workarea:
            (workarea.cache_dir / "not-a-page.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(StateError):
                workarea.page_state_paths()


class TestWorkareaInitialization(unittest.TestCase):

    def test_failed_profile_write_leaves_no_partial_workarea(self) -> None:
        original_open = Path.open

        def fail_profile_open(path, *args, **kwargs):
            if path.name == "profile":
                raise OSError("injected profile write failure")

            return original_open(path, *args, **kwargs)

        with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
            root = Path(temporary_dir)
            with patch.object(Path, "open", fail_profile_open):
                with self.assertRaisesRegex(Workarea.Error, "cannot initialise"):
                    Workarea.init(root)

            self.assertFalse((root / ".cflsync").exists())
            self.assertFalse(any(path.name.startswith(".cflsync-init-") for path in root.iterdir()))


class TestWorkareaPageDirectory(unittest.TestCase):

    def test_returns_an_existing_managed_page_directory(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()
            page_directory = workarea.root_dir / state.page.directory
            page_directory.mkdir()
            (page_directory / "page.md").write_text("# Example page\n", encoding="utf-8")

            self.assertEqual(workarea.page_directory(state), page_directory)

    def test_rejects_a_missing_page_directory_or_page_file(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()

            with self.assertRaises(Workarea.Error):
                workarea.page_directory(state)

            page_directory = workarea.root_dir / state.page.directory
            page_directory.mkdir()
            with self.assertRaises(Workarea.Error):
                workarea.page_directory(state)


class TestWorkareaSafePaths(unittest.TestCase):

    def test_rejects_a_traversal_page_directory(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state(directory="../outside")

            with self.assertRaises(Workarea.Error):
                workarea.page_directory(state)

    def test_rejects_a_missing_page_state(self) -> None:
        with temporary_workarea() as workarea:
            with self.assertRaises(StateError):
                PageState.load(workarea.cache_path("123456"))

    def test_enumerates_duplicate_page_directory_assignments(self) -> None:
        with temporary_workarea() as workarea:
            first = example_page_state("123456", directory="Shared page")
            second = example_page_state("234567", directory="Shared page")
            first.save(workarea.cache_path(first.page.id))
            second.save(workarea.cache_path(second.page.id))

            self.assertEqual(list(workarea.page_state_paths()), ["123456", "234567"])

    def test_rejects_an_existing_title_directory_before_mutation(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()
            target = workarea.root_dir / state.page.directory
            target.mkdir()
            before = sorted(workarea.root_dir.iterdir())

            with self.assertRaises(Workarea.Error):
                workarea.page_directory_target(state)

            self.assertEqual(sorted(workarea.root_dir.iterdir()), before)


class TestWorkareaMaterialization(unittest.TestCase):

    def test_derives_safe_deterministic_page_directory_names(self) -> None:
        with temporary_workarea() as workarea:
            self.assertEqual(workarea.page_directory_name("Example page"), "Example page")
            self.assertEqual(workarea.page_directory_name("Example/page"), "Example%2Fpage")
            self.assertEqual(workarea.page_directory_name("."), "%2E")
            self.assertEqual(workarea.page_directory_name("Example/page"), workarea.page_directory_name("Example/page"))

    def test_stages_and_installs_one_complete_page(self) -> None:
        with temporary_workarea() as workarea:
            staging = workarea.stage_page("Example page", "# Example\n", {"diagram.png": b"PNG", "report.xlsx": b"XLSX"})

            self.assertEqual((staging / "page.md").read_text(encoding="utf-8"), "# Example\n")
            self.assertEqual((staging / "_attachments" / "diagram.png").read_bytes(), b"PNG")
            self.assertFalse((workarea.root_dir / "Example page").exists())

            target = workarea.install_page(staging, "Example page")

            self.assertEqual(target, workarea.root_dir / "Example page")
            self.assertEqual((target / "_attachments" / "report.xlsx").read_bytes(), b"XLSX")

    def test_rejects_collisions_and_staging_failure_without_target_mutation(self) -> None:
        with temporary_workarea() as workarea:
            target = workarea.root_dir / "Example page"
            target.mkdir()
            (target / "page.md").write_text("previous\n", encoding="utf-8")
            before = (target / "page.md").read_text(encoding="utf-8")
            staging = workarea.stage_page("Example page", "replacement\n", {})

            with self.assertRaises(Workarea.Error):
                workarea.install_page(staging, "Example page")

            self.assertEqual((target / "page.md").read_text(encoding="utf-8"), before)

            with self.assertRaises(Workarea.Error):
                workarea.stage_page("Other page", "content", {"../unsafe": b"x"})

            self.assertFalse((workarea.root_dir / "Other page").exists())

    def test_interrupted_staging_removes_hidden_directory(self) -> None:
        with temporary_workarea() as workarea:
            write_text = Path.write_text

            def interrupt_write(path, text, *args, **kwargs):
                if any(part.startswith(".cflsync-stage-") for part in path.parts):
                    raise KeyboardInterrupt()

                return write_text(path, text, *args, **kwargs)

            with patch.object(Path, "write_text", interrupt_write):
                with self.assertRaises(KeyboardInterrupt):
                    workarea.stage_page("Example page", "# Example\n", {})

            self.assertFalse((workarea.root_dir / "Example page").exists())
            self.assertFalse(any(path.name.startswith(".cflsync-stage-") for path in workarea.root_dir.iterdir()))

    def test_replaces_a_complete_page_directory(self) -> None:
        with temporary_workarea() as workarea:
            target = workarea.root_dir / "Example page"
            target.mkdir()
            (target / "page.md").write_text("previous\n", encoding="utf-8")
            (target / "_attachments").mkdir()
            directory_inode = target.stat().st_ino
            attachment_inode = (target / "_attachments").stat().st_ino
            staging = workarea.stage_page("Example page", "replacement\n", {"new.txt": b"new"})

            workarea.install_page(staging, "Example page", replace=True)

            self.assertEqual((target / "page.md").read_text(encoding="utf-8"), "replacement\n")
            self.assertEqual((target / "_attachments" / "new.txt").read_bytes(), b"new")
            self.assertEqual(target.stat().st_ino, directory_inode)
            self.assertEqual((target / "_attachments").stat().st_ino, attachment_inode)

    def test_repull_preserves_current_directory_and_unmanaged_files(self) -> None:
        for name in ["Example page", "Renamed"]:
            with self.subTest(name=name):
                with temporary_workarea() as workarea:
                    source = workarea.root_dir / "Example page"
                    source.mkdir()
                    (source / "page.md").write_text("previous\n")
                    (source / "notes.txt").write_text("notes\n")
                    attachments = source / "_attachments"
                    attachments.mkdir()
                    (attachments / "old.txt").write_text("old\n")
                    source_inode = source.stat().st_ino
                    attachment_inode = attachments.stat().st_ino
                    notes_inode = (source / "notes.txt").stat().st_ino
                    staging = workarea.stage_page(name, "replacement\n", {"new.txt": b"new"})
                    original_cwd = os.getcwd()
                    os.chdir(source)
                    try:
                        with workarea.replace_page(staging, name, source, ["old.txt", "new.txt"]):
                            self.assertEqual(os.getcwd(), str(workarea.root_dir / name))

                        self.assertEqual(os.getcwd(), str(workarea.root_dir / name))
                    finally:
                        os.chdir(original_cwd)

                    target = workarea.root_dir / name
                    self.assertEqual(target.stat().st_ino, source_inode)
                    self.assertEqual((target / "_attachments").stat().st_ino, attachment_inode)
                    self.assertEqual((target / "notes.txt").stat().st_ino, notes_inode)
                    self.assertEqual((target / "page.md").read_text(), "replacement\n")
                    self.assertFalse((target / "_attachments/old.txt").exists())
                    self.assertEqual((target / "_attachments/new.txt").read_bytes(), b"new")

    def test_failed_commit_restores_files_and_directory_name(self) -> None:
        for name in ["Example page", "Renamed"]:
            with self.subTest(name=name):
                with temporary_workarea() as workarea:
                    source = workarea.root_dir / "Example page"
                    source.mkdir()
                    (source / "page.md").write_text("previous\n")
                    (source / "_attachments").mkdir()
                    (source / "_attachments/old.txt").write_bytes(b"old")
                    inode = source.stat().st_ino
                    staging = workarea.stage_page(name, "replacement\n", {"new.txt": b"new"})
                    with self.assertRaisesRegex(RuntimeError, "commit failed"):
                        with workarea.replace_page(staging, name, source, ["old.txt", "new.txt"]):
                            raise RuntimeError("commit failed")

                    self.assertEqual(source.stat().st_ino, inode)
                    self.assertEqual((source / "page.md").read_text(), "previous\n")
                    self.assertEqual((source / "_attachments/old.txt").read_bytes(), b"old")
                    self.assertFalse((source / "_attachments/new.txt").exists())


# vim: set ts=4 sw=4 et tw=132:
