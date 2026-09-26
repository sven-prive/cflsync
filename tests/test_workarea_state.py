# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for state-backed workarea operations."""

import errno
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cflsync import PageState, StateError, Workarea
from cflsync.workarea import filesystem_error_message
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
                    Workarea.init(root, "123456")

            self.assertFalse((root / ".cflsync").exists())
            self.assertFalse(any(path.name.startswith(".cflsync-init-") for path in root.iterdir()))

    def test_records_the_root_page_and_profile(self) -> None:
        with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
            root = Path(temporary_dir)

            Workarea.init(root, "789012", "work")

            workarea = Workarea.find(root)
            self.assertEqual((workarea.root_page_id, workarea.profile), ("789012", "work"))
            self.assertEqual((root / ".cflsync" / "root").read_text(encoding="utf-8"), "789012\n")
            self.assertEqual(list(workarea.cache_dir.iterdir()), [])

    def test_failed_root_write_leaves_no_partial_workarea(self) -> None:
        original_open = Path.open

        def fail_root_open(path, *args, **kwargs):
            if path.name == "root":
                raise OSError("injected root write failure")

            return original_open(path, *args, **kwargs)

        with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
            root = Path(temporary_dir)
            with patch.object(Path, "open", fail_root_open):
                with self.assertRaisesRegex(Workarea.Error, "cannot initialise"):
                    Workarea.init(root, "123456")

            self.assertEqual(list(root.iterdir()), [])

    def test_rejects_a_non_numeric_root_page_id(self) -> None:
        with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
            root = Path(temporary_dir)
            for root_page_id in ["", "12a", "１２"]:
                with self.subTest(root_page_id=root_page_id):
                    with self.assertRaisesRegex(Workarea.Error, "root page ID must be numeric"):
                        Workarea.init(root, root_page_id)

            self.assertEqual(list(root.iterdir()), [])

    def test_refuses_to_initialise_inside_any_existing_workarea(self) -> None:
        with temporary_workarea() as workarea:
            (workarea.cflsync_dir / "root").unlink()
            nested = workarea.root_dir / "nested"
            nested.mkdir()
            for path in [workarea.root_dir, nested]:
                with self.subTest(path=path):
                    with self.assertRaisesRegex(Workarea.Error, "already part of a cflsync workarea"):
                        Workarea.init(path, "789012")

            self.assertFalse((nested / ".cflsync").exists())


class TestWorkareaFormat(unittest.TestCase):

    def test_refuses_a_version_1_workarea_without_a_root_page(self) -> None:
        with temporary_workarea() as workarea:
            (workarea.cflsync_dir / "root").unlink()

            with self.assertRaisesRegex(Workarea.Error, "is a version-1 cflsync workarea.*'cflsync init ROOT_PAGE_REF'"):
                Workarea.find(workarea.root_dir)

    def test_refuses_an_invalid_root_file(self) -> None:
        for content in ["", "\n", "abc\n", "12 34\n", "12\n34\n"]:
            with self.subTest(content=content):
                with temporary_workarea() as workarea:
                    (workarea.cflsync_dir / "root").write_text(content, encoding="utf-8")

                    with self.assertRaisesRegex(Workarea.Error, "must contain one numeric page ID"):
                        Workarea.find(workarea.root_dir)

    def test_finds_the_workarea_from_a_nested_directory(self) -> None:
        with temporary_workarea() as workarea:
            nested = workarea.root_dir / "Page" / "Child"
            nested.mkdir(parents=True)

            self.assertEqual(Workarea.find(nested).root_dir, workarea.root_dir)


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
            with self.assertRaises(Workarea.Error):
                workarea.page_directory_path("../outside")

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

    def test_rejects_an_existing_title_directory_with_different_case(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state(directory="Example page")
            target = workarea.root_dir / "example page"
            target.mkdir()

            with self.assertRaisesRegex(Workarea.Error, "already exists"):
                workarea.page_directory_target(state)


class TestWorkareaMaterialization(unittest.TestCase):

    def test_derives_safe_deterministic_page_directory_names(self) -> None:
        with temporary_workarea() as workarea:
            self.assertEqual(workarea.page_directory_name("Example page"), "Example page")
            self.assertEqual(workarea.page_directory_name("Example/page"), "Example%2Fpage")
            self.assertEqual(workarea.page_directory_name("."), "%2E")
            self.assertEqual(workarea.page_directory_name("CON"), "%43ON")
            self.assertEqual(workarea.page_directory_name("lpt9"), "%6Cpt9")
            self.assertEqual(workarea.page_directory_name("Example "), "Example%20")
            self.assertEqual(workarea.page_directory_name("Example/page"), workarea.page_directory_name("Example/page"))

    @unittest.skipIf(os.name == "nt", "the error Windows reports for an over-long name component depends on its configuration")
    def test_reports_a_name_that_the_filesystem_rejects_as_too_long(self) -> None:
        with temporary_workarea() as workarea:
            directory = "x" * 300
            staging = workarea.stage_page(directory, "# Example\n", {})

            with self.assertRaises(OSError) as context:
                workarea.install_page(staging, directory)

            self.assertRegex(filesystem_error_message(context.exception), r"path is too long for this system \(\d+ characters\)")
            self.assertTrue(staging.is_dir())

    def test_writes_page_markdown_with_lf_newlines(self) -> None:
        with temporary_workarea() as workarea:
            staging = workarea.stage_page("Example page", "# Example\n\nText\n", {})

            self.assertEqual((staging / "page.md").read_bytes(), b"# Example\n\nText\n")

    def test_windows_rejects_renaming_the_current_page_directory(self) -> None:
        with temporary_workarea() as workarea:
            source = workarea.root_dir / "Example page"
            source.mkdir()
            (source / "page.md").write_text("previous\n", encoding="utf-8")
            (source / "_attachments").mkdir()
            staging = workarea.stage_page("Renamed", "replacement\n", {})
            original_cwd = os.getcwd()
            os.chdir(source)
            try:
                with patch("cflsync.workarea._is_windows", return_value=True):
                    with self.assertRaisesRegex(Workarea.Error, "run cflsync from outside"):
                        with workarea.replace_page(staging, "Renamed", source):
                            pass
            finally:
                os.chdir(original_cwd)

            self.assertTrue(source.is_dir())
            self.assertEqual((source / "page.md").read_text(encoding="utf-8"), "previous\n")

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
                        if os.name == "nt" and name != source.name:
                            with self.assertRaisesRegex(Workarea.Error, "run cflsync from outside"):
                                with workarea.replace_page(staging, name, source, ["old.txt", "new.txt"]):
                                    pass

                            self.assertEqual(os.getcwd(), str(source))
                        else:
                            with workarea.replace_page(staging, name, source, ["old.txt", "new.txt"]):
                                self.assertEqual(os.getcwd(), str(workarea.root_dir / name))

                            self.assertEqual(os.getcwd(), str(workarea.root_dir / name))
                    finally:
                        os.chdir(original_cwd)

                    if os.name == "nt" and name != source.name:
                        self.assertEqual((source / "page.md").read_text(), "previous\n")
                        self.assertTrue((source / "_attachments/old.txt").exists())
                        self.assertFalse((source / "_attachments/new.txt").exists())
                        continue

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


class WindowsPathLengthError(OSError):
    """An OSError as Windows reports an over-long path, on any platform."""

    winerror = 206


class TestFilesystemErrorMessage(unittest.TestCase):

    def test_names_the_longer_path_of_a_path_length_error(self) -> None:
        error = OSError(errno.ENAMETOOLONG, "File name too long", "short", None, "much/longer/path")

        self.assertEqual(
            filesystem_error_message(error),
            "path is too long for this system (16 characters): 'much/longer/path'; on Windows, enable long path support")

    def test_recognizes_the_windows_path_length_error(self) -> None:
        error = WindowsPathLengthError(errno.ENOENT, "The filename or extension is too long", "C:/workarea/page")

        self.assertIn("path is too long for this system (16 characters)", filesystem_error_message(error))

    def test_describes_a_path_length_error_without_a_path(self) -> None:
        error = OSError(errno.ENAMETOOLONG, "File name too long")

        self.assertEqual(filesystem_error_message(error), f"a path is too long for this system: {error}")

    def test_leaves_other_errors_unchanged(self) -> None:
        for error in [OSError(errno.EACCES, "Permission denied", "page"), UnicodeError("invalid byte")]:
            with self.subTest(error=error):
                self.assertEqual(filesystem_error_message(error), str(error))


class TestWorkareaRelocation(unittest.TestCase):

    def _page_directory(self, workarea, directory):
        path = workarea.root_dir.joinpath(*directory.split("/"))
        path.mkdir(parents=True)
        (path / "page.md").write_text("previous\n", encoding="utf-8")
        (path / "_attachments").mkdir()
        return path

    def test_moves_a_page_directory_with_its_contents(self) -> None:
        with temporary_workarea() as workarea:
            source = self._page_directory(workarea, "Old parent/Page")
            self._page_directory(workarea, "Old parent/Page/Child")
            (source / "notes.txt").write_text("unmanaged\n", encoding="utf-8")
            self._page_directory(workarea, "New parent")

            target = workarea.relocate(source, "New parent/Page")

            self.assertEqual(target, workarea.root_dir / "New parent" / "Page")
            self.assertFalse(source.exists())
            self.assertEqual((target / "notes.txt").read_text(encoding="utf-8"), "unmanaged\n")
            self.assertTrue((target / "Child" / "page.md").is_file())

    def test_moving_to_the_same_directory_changes_nothing(self) -> None:
        with temporary_workarea() as workarea:
            source = self._page_directory(workarea, "Page")

            self.assertEqual(workarea.relocate(source, "Page"), source)
            self.assertTrue((source / "page.md").is_file())

    def test_refuses_a_directory_assigned_to_another_cached_page(self) -> None:
        with temporary_workarea() as workarea:
            source = self._page_directory(workarea, "Page")
            other = example_page_state("234567", directory="Target")
            other.save(workarea.cache_path(other.page.id))

            with self.assertRaisesRegex(Workarea.Error, "'target' is assigned to page '234567'"):
                workarea.relocate(source, "target")

            self.assertTrue(source.is_dir())

    def test_refuses_an_existing_sibling_that_differs_only_in_case(self) -> None:
        with temporary_workarea() as workarea:
            source = self._page_directory(workarea, "Page")
            (workarea.root_dir / "Parent" / "target").mkdir(parents=True)

            with self.assertRaisesRegex(Workarea.Error, "'Parent/Target' already exists"):
                workarea.relocate(source, "Parent/Target")

            self.assertTrue(source.is_dir())

    def test_windows_refuses_to_move_the_current_directory(self) -> None:
        with temporary_workarea() as workarea:
            source = self._page_directory(workarea, "Page")
            original_cwd = os.getcwd()
            os.chdir(source)
            try:
                with patch("cflsync.workarea._is_windows", return_value=True):
                    with self.assertRaisesRegex(Workarea.Error, "run cflsync from outside"):
                        workarea.relocate(source, "Renamed")
            finally:
                os.chdir(original_cwd)

            self.assertTrue(source.is_dir())

    def test_reports_a_rename_that_the_filesystem_rejects_as_too_long(self) -> None:
        with temporary_workarea() as workarea:
            source = self._page_directory(workarea, "Page")
            target = str(workarea.root_dir / "Renamed")
            error = OSError(errno.ENAMETOOLONG, "File name too long", str(source), None, target)

            with patch("cflsync.workarea.os.rename", side_effect=error):
                with self.assertRaisesRegex(Workarea.Error, "cannot move page directory: path is too long for this system"):
                    workarea.relocate(source, "Renamed")


class TestWorkareaNestedPageDirectories(unittest.TestCase):

    def _page_directory(self, workarea, directory):
        path = workarea.root_dir.joinpath(*directory.split("/"))
        path.mkdir(parents=True)
        (path / "page.md").write_text("previous\n", encoding="utf-8")
        (path / "_attachments").mkdir()
        return path

    def test_resolves_a_nested_page_directory(self) -> None:
        with temporary_workarea() as workarea:
            path = self._page_directory(workarea, "Root/Child/Grandchild")

            self.assertEqual(workarea.page_directory_path("Root/Child/Grandchild"), path)

    def test_rejects_malformed_relative_page_directories(self) -> None:
        with temporary_workarea() as workarea:
            for directory in ["/Root", "Root/", "Root//Child", "Root/./Child", "Root/../Child", "../Root", "."]:
                with self.subTest(directory=directory):
                    with self.assertRaisesRegex(Workarea.Error, "relative path of directory names"):
                        workarea.page_directory_path(directory)

    @unittest.skipIf(os.name == "nt", "creating symbolic links needs extra privileges on Windows")
    def test_rejects_a_nested_directory_that_escapes_through_a_symbolic_link(self) -> None:
        with TemporaryDirectory() as outside:
            with temporary_workarea() as workarea:
                (workarea.root_dir / "Root").symlink_to(outside, target_is_directory=True)

                with self.assertRaisesRegex(Workarea.Error, "outside the workarea"):
                    workarea.page_directory_path("Root/Child")

    def test_stages_and_installs_a_nested_page(self) -> None:
        with temporary_workarea() as workarea:
            self._page_directory(workarea, "Root")
            staging = workarea.stage_page("Root/Child", "# Child\n", {"diagram.png": b"PNG"})

            target = workarea.install_page(staging, "Root/Child")

            self.assertEqual(target, workarea.root_dir / "Root" / "Child")
            self.assertEqual((target / "page.md").read_text(encoding="utf-8"), "# Child\n")
            self.assertEqual((target / "_attachments/diagram.png").read_bytes(), b"PNG")

    def test_renames_a_nested_page_directory_within_its_parent(self) -> None:
        with temporary_workarea() as workarea:
            source = self._page_directory(workarea, "Root/Old")
            (source / "notes.txt").write_text("unmanaged\n", encoding="utf-8")
            staging = workarea.stage_page("Root/New", "replacement\n", {}, source=source)

            with workarea.replace_page(staging, "Root/New", source) as target:
                pass

            self.assertEqual(target, workarea.root_dir / "Root" / "New")
            self.assertFalse(source.exists())
            self.assertEqual((target / "page.md").read_text(encoding="utf-8"), "replacement\n")
            self.assertEqual((target / "notes.txt").read_text(encoding="utf-8"), "unmanaged\n")

    def test_rejects_a_previous_directory_outside_the_workarea(self) -> None:
        with TemporaryDirectory() as outside:
            with temporary_workarea() as workarea:
                staging = workarea.stage_page("Root", "replacement\n", {})

                with self.assertRaisesRegex(Workarea.Error, "previous page directory must be inside the workarea"):
                    with workarea.replace_page(staging, "Root", Path(outside).resolve()):
                        pass


# vim: set ts=4 sw=4 et tw=132:
