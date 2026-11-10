import contextlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release", ROOT / ".github/scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = "a" * 40
OTHER = "b" * 40
VERSION = "2.3.4"
TAG = f"v{VERSION}"


@contextlib.contextmanager
def cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def fixture():
    release.PBX.parent.mkdir(parents=True)
    release.PBX.write_text("\n".join(
        f"{key} = {VERSION};" for key in release.KEYS for _ in range(3)))
    release.PLIST.write_bytes(plistlib.dumps({"CFBundleShortVersionString": VERSION,
                                            "CFBundleVersion": VERSION}))
    release.LOCALIZED.parent.mkdir()
    release.LOCALIZED.write_text(
        f'CFBundleShortVersionString = "{VERSION}";\n'
        f'CFBundleGetInfoString = "test-path-name version {VERSION}, Copyright 2018 miyako.";\n')


def page(entries, next_page=False):
    link = '\nLink: <https://api.github.com/next>; rel="next"' if next_page else ""
    return f"HTTP/2.0 200 OK{link}\n\n{json.dumps(entries)}"


def entry(number, tag="another-tag", draft=False):
    return {"id": number, "tag_name": tag, "draft": draft}


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = self.enterContext(cwd(self.temp.name))
        fixture()
        self.enterContext(patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "example/plugin", "GITHUB_SHA": SHA,
            "GITHUB_REF_NAME": "feature/release", "GITHUB_REF_TYPE": "branch",
        }))


class MetadataTests(FixtureTest):
    def test_strict_invalid_versions(self):
        for value in ("1.2", "01.2.3", "1.02.3", "v1.2.3", "1.2.3-rc1", "",
                      "1.2.3\n", None, 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.strict_version(value)

    def test_all_fields_bump_together(self):
        self.assertEqual(release.read_version(), VERSION)
        release.write_version("2.3.5")
        self.assertEqual(release.read_version(), "2.3.5")
        self.assertEqual(release.PBX.read_text().count("2.3.5"), 6)

    def test_drift_in_each_source_fails_before_writes(self):
        for path in (release.PBX, release.PLIST, release.LOCALIZED):
            original = path.read_bytes()
            path.write_bytes(original.replace(VERSION.encode(), b"9.8.7", 1))
            before = {p: p.read_bytes() for p in (release.PBX, release.PLIST, release.LOCALIZED)}
            with self.subTest(path=path), self.assertRaises(ValueError):
                release.write_version("2.3.5")
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            path.write_bytes(original)

    def test_invalid_missing_project_fields(self):
        original = release.PBX.read_text()
        for text in (original.replace(VERSION, "1.2"),
                     original.replace("MARKETING_VERSION", "UNRELATED_VERSION"),
                     original + "\nMARKETING_VERSION = nonsense;"):
            release.PBX.write_text(text)
            with self.assertRaises(ValueError):
                release.read_version()

    def test_known_plist_references_preserved(self):
        for reference in release.REFERENCES:
            fixture_plist = {"CFBundleShortVersionString": reference,
                             "CFBundleVersion": reference}
            release.PLIST.write_bytes(plistlib.dumps(fixture_plist))
            before = release.PLIST.read_bytes()
            release.write_version("2.3.5")
            self.assertEqual(release.PLIST.read_bytes(), before)
            release.write_version(VERSION)

    def test_unknown_reference_rejected(self):
        release.PLIST.write_bytes(plistlib.dumps({
            "CFBundleShortVersionString": "$(UNRECOGNIZED)"}))
        with self.assertRaises(ValueError):
            release.read_version()

    def test_invalid_plist_and_localized_metadata(self):
        plist = release.PLIST.read_bytes()
        for value in ([], {}, {"CFBundleShortVersionString": "1.2"},
                      {"CFBundleShortVersionString": VERSION, "CFBundleVersion": "bad"}):
            release.PLIST.write_bytes(plistlib.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.read_version()
        release.PLIST.write_bytes(plist)
        original = release.LOCALIZED.read_bytes()
        for value in (b"", original + original,
                      original.replace(VERSION.encode(), b"1.2"),
                      original.replace(b"version", b"unrecognized")):
            release.LOCALIZED.write_bytes(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                release.read_version()

    def test_optional_bundle_version_is_not_added(self):
        release.PLIST.write_bytes(plistlib.dumps({"CFBundleShortVersionString": VERSION}))
        release.write_version("2.3.5")
        self.assertNotIn("CFBundleVersion", plistlib.loads(release.PLIST.read_bytes()))

    def test_notary_acceptance_required(self):
        path = Path("notary.json")
        for status in ("Invalid", "In Progress", None):
            path.write_text(json.dumps({"status": status}))
            with self.assertRaises(ValueError):
                release.verify_notary([path])
        path.write_text('{"status":"Accepted"}')
        release.verify_notary([path])
        path.write_text("invalid json")
        with self.assertRaises(ValueError):
            release.verify_notary([path])


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/plugin"}))
        self.remote = self.enterContext(patch.object(release, "remote_refs", return_value={}))
        self.command = self.enterContext(patch.object(release, "run"))

    def test_page_two_draft_collision(self):
        self.command.side_effect = [
            page([entry(i) for i in range(1, 101)], True),
            page([entry(101, TAG, True)]),
        ]
        with self.assertRaisesRegex(ValueError, "including drafts"):
            release.available(TAG)
        self.assertIn("page=2", self.command.call_args.args[-1])

    def test_full_page_followed_by_empty_page(self):
        self.command.side_effect = [page([entry(i) for i in range(1, 101)]), page([])]
        release.available(TAG)
        self.assertEqual(self.command.call_count, 2)

    def test_existing_tag_stops_before_api(self):
        self.remote.return_value = {f"refs/tags/{TAG}": SHA}
        with self.assertRaises(ValueError):
            release.available(TAG)
        self.command.assert_not_called()

    def test_http_and_network_failures_closed(self):
        for code in (1, 22, 404):
            self.command.side_effect = subprocess.CalledProcessError(code, ["gh", "api"])
            with self.subTest(code=code), self.assertRaises(subprocess.CalledProcessError):
                release.available(TAG)

    def test_malformed_and_partial_lists(self):
        cases = [
            "", "[]", "\n\n[]", "HTTP/2.0 404 Not Found\n\n[]",
            "HTTP/2.0 200 OK\n\n[", page({}), page([None]),
            page([{}]), page([entry(True)]), page([{"id": 1, "tag_name": TAG}]),
            page([dict(entry(1), draft="false")]),
            page([dict(entry(1), tag_name="")]),
            page([entry(1), entry(1)]), page([entry(1)], True),
            page([entry(i) for i in range(1, 102)]),
        ]
        for response in cases:
            self.command.return_value = response
            with self.subTest(response=response[:120]), self.assertRaises(ValueError):
                release.available(TAG)

    def test_page_two_error_not_treated_as_end(self):
        self.command.side_effect = [page([entry(i) for i in range(1, 101)], True),
                                    subprocess.CalledProcessError(1, ["gh", "api"])]
        with self.assertRaises(subprocess.CalledProcessError):
            release.available(TAG)

    def test_remote_errors_and_malformed_refs(self):
        for code, output in ((128, ""), (0, ""), (2, "unexpected"),
                             (0, f"{SHA}\trefs/tags/other\n"), (0, "not-a-sha\tref\n")):
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], code, output)), self.assertRaises((ValueError, RuntimeError)):
                REMOTE_REFS(f"refs/tags/{TAG}")


REMOTE_REFS = release.remote_refs


class PublicationTests(FixtureTest):
    def setUp(self):
        super().setUp()
        Path("plugin.zip").write_bytes(b"original zip")
        Path("plugin.dmg").write_bytes(b"stapled dmg")
        self.calls = []
        self.tag = None
        self.created = None
        self.uploaded = []
        self.failure = None
        self.enterContext(patch.object(release, "run", side_effect=self.command))
        self.enterContext(patch.object(release, "remote_refs", side_effect=self.refs))

    def refs(self, *refs):
        self.calls.append(("refs", *refs))
        return {f"refs/tags/{TAG}": self.tag} if self.tag else {}

    def command(self, *args, input=None):
        self.calls.append(args)
        if args[:2] == ("git", "rev-parse"):
            return SHA
        if args[:2] == ("git", "push"):
            if self.failure == "tag-race":
                self.tag = OTHER
                raise subprocess.CalledProcessError(1, args)
            self.tag = OTHER if self.failure == "wrong-remote" else SHA
            return ""
        if args[:2] == ("gh", "api") and "GET" in args:
            if self.failure == "collision":
                return page([entry(1, TAG, True)])
            return page([])
        if args[:2] == ("gh", "api") and "POST" in args:
            if self.failure == "post":
                raise subprocess.CalledProcessError(1, args)
            self.created = json.loads(input)
            if self.failure == "response":
                return "{}"
            return json.dumps(dict(self.created, id=1))
        if args[:3] == ("gh", "release", "upload"):
            self.uploaded.append("plugin.zip")
            if self.failure == "upload":
                raise subprocess.CalledProcessError(1, args)
            self.uploaded.append("plugin.dmg")
            return ""
        self.fail(f"Unexpected command: {args}")

    def publish(self):
        release.publish(VERSION, SHA, ["plugin.zip", "plugin.dmg"])

    def test_success_exact_sha_create_only_and_original_assets(self):
        self.publish()
        self.assertIn(("git", "push", "--no-follow-tags", "origin",
                       f"{SHA}:refs/tags/{TAG}"), self.calls)
        self.assertEqual(self.created, dict(tag_name=TAG, target_commitish=SHA, name=TAG,
                                           body="", draft=False, prerelease=False))
        self.assertEqual(self.uploaded, ["plugin.zip", "plugin.dmg"])
        self.assertEqual(Path("plugin.zip").read_bytes(), b"original zip")
        self.assertEqual(Path("plugin.dmg").read_bytes(), b"stapled dmg")
        first_lookup = next(i for i, call in enumerate(self.calls) if call[0] == "refs")
        push = next(i for i, call in enumerate(self.calls) if call[:2] == ("git", "push"))
        post = next(i for i, call in enumerate(self.calls) if "POST" in call)
        self.assertLess(first_lookup, push)
        self.assertEqual(self.calls[post - 1], ("refs", f"refs/tags/{TAG}"))

    def test_failures_preserve_state_and_never_rollback(self):
        for failure in ("post", "upload", "response", "tag-race", "wrong-remote", "collision"):
            with self.subTest(failure=failure):
                self.failure = failure
                self.tag = self.created = None
                self.uploaded = []
                self.calls.clear()
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    self.publish()
                if failure == "collision":
                    self.assertIsNone(self.tag)
                else:
                    self.assertIsNotNone(self.tag)
                if failure in ("upload", "response"):
                    self.assertIsNotNone(self.created)
                if failure == "upload":
                    self.assertEqual(self.uploaded, ["plugin.zip"])
                for command in self.calls:
                    self.assertFalse(set(command) & {
                        "DELETE", "PATCH", "--clobber", "--force", "-f", "--delete"})
                self.assertLessEqual(sum("POST" in call for call in self.calls), 1)

    def test_wrong_sha_or_version_or_missing_assets_has_no_side_effects(self):
        for version, sha, assets in (("9.8.7", SHA, ["plugin.zip"]),
                                     (VERSION, OTHER, ["plugin.zip"]),
                                     (VERSION, "branch", ["plugin.zip"]),
                                     (VERSION, SHA, ["missing.zip"])):
            self.calls.clear()
            with self.assertRaises(ValueError):
                release.publish(version, sha, assets)
            self.assertIsNone(self.tag)
            self.assertFalse(any(call[:2] == ("gh", "api") for call in self.calls))

    def test_preflight_validates_and_outputs_source_version(self):
        output = Path("output")
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            release.preflight()
        self.assertEqual(output.read_text(), f"version={VERSION}\ntag={TAG}\n")
        self.assertIsNone(self.tag)
        self.assertIsNone(self.created)


class LocalGitTests(FixtureTest):
    def git(self, *args):
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.PIPE).strip()

    def setUp(self):
        super().setUp()
        self.git("init", "--initial-branch=feature/release")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "fixture")
        self.sha = self.git("rev-parse", "HEAD")
        self.git("init", "--bare", "remote.git")
        self.git("remote", "add", "origin", str(Path("remote.git").resolve()))
        self.git("push", "origin", "HEAD:refs/heads/feature/release")
        self.git("config", "push.followTags", "true")
        self.git("tag", "-a", "fixture-follow-tag", "-m", "must stay local")
        os.environ["GITHUB_SHA"] = self.sha

    def test_real_bump_no_tag_even_with_follow_tags_configured(self):
        release.bump("patch")
        self.assertEqual(release.read_version(), "2.3.5")
        self.assertEqual(self.git("ls-remote", "--tags", "origin"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git(
            "--git-dir=remote.git", "rev-parse", "refs/heads/feature/release"))

    def test_bump_modes(self):
        for mode, expected in (("minor", "2.4.0"), ("major", "3.0.0")):
            with patch.object(release, "selected_sha"), patch.object(
                    release, "remote_refs", return_value={"refs/heads/feature/release": self.sha}), patch.object(
                    release, "run"):
                release.bump(mode)
                self.assertEqual(release.read_version(), expected)
                release.write_version(VERSION)

    def test_moved_branch_rejected_before_metadata_change(self):
        with patch.object(release, "remote_refs", return_value={
                "refs/heads/feature/release": OTHER}), self.assertRaises(ValueError):
            release.bump("patch")
        self.assertEqual(release.read_version(), VERSION)

    def test_tag_selection_rejected(self):
        with patch.dict(os.environ, {"GITHUB_REF_TYPE": "tag"}), self.assertRaises(ValueError):
            release.bump("patch")

    def test_nonforce_branch_race(self):
        real_run = release.run
        winner = self.git("commit-tree", "HEAD^{tree}", "-p", self.sha, "-m", "winner")

        def race(*args, **kwargs):
            if args[:2] == ("git", "push"):
                self.git("push", "origin", f"{winner}:refs/heads/feature/release")
            return real_run(*args, **kwargs)

        with patch.object(release, "run", side_effect=race), self.assertRaises(
                subprocess.CalledProcessError):
            release.bump("patch")
        self.assertEqual(self.git("--git-dir=remote.git", "rev-parse",
                                  "refs/heads/feature/release"), winner)

    def test_nonforce_tag_race(self):
        self.git("push", "--no-follow-tags", "origin", f"{self.sha}:refs/tags/{TAG}")
        Path("new").write_text("another commit")
        self.git("add", "new")
        self.git("commit", "-m", "another")
        new = self.git("rev-parse", "HEAD")
        with self.assertRaises(subprocess.CalledProcessError):
            release.run("git", "push", "--no-follow-tags", "origin", f"{new}:refs/tags/{TAG}")
        self.assertEqual(release.remote_refs(f"refs/tags/{TAG}"), {f"refs/tags/{TAG}": self.sha})
        self.assertNotIn("fixture-follow-tag", self.git("ls-remote", "--tags", "origin"))


class RepositoryTests(unittest.TestCase):
    def test_live_version_agrees_with_authoritative_source(self):
        with cwd(ROOT):
            values = re.findall(r"MARKETING_VERSION = ([^;]+);", release.PBX.read_text())
            self.assertTrue(values)
            self.assertEqual(release.read_version(), values[0])

    def test_workflow_contract_and_publication_gates(self):
        build = (ROOT / ".github/workflows/release.yml").read_text()
        bump = (ROOT / ".github/workflows/bump-version.yml").read_text()
        self.assertIn("name: Build\n", build)
        self.assertEqual(build.split("on:\n", 1)[1].split("\npermissions:", 1)[0].strip(),
                         "workflow_dispatch:")
        self.assertEqual(bump.count("workflow_dispatch:"), 1)
        self.assertNotRegex(bump, r"(?m)^  (push|pull_request):")
        self.assertNotIn("inputs.tag", build)
        self.assertEqual(build.count("uses: actions/checkout@v4"), 3)
        self.assertEqual(build.count("ref: ${{ github.sha }}"), 3)
        self.assertIn("group: repository-release", build)
        self.assertIn("cancel-in-progress: false", build)
        self.assertIn("needs: [version, build-windows]", build)
        self.assertIn("if-no-files-found: error", build)
        gates = ["build x64", "verify universal binary", "merge Windows binaries",
                 "- name: codesign", "codesign --verify", "notarytool submit",
                 "verify-notary", "stapler staple", "stapler validate",
                 "- name: create GitHub Release", "release.py publish"]
        positions = [build.index(gate) for gate in gates]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("continue-on-error", build)
        self.assertNotIn("always()", build)
        secrets = set(re.findall(r"secrets\.([A-Z_]+)", build))
        self.assertEqual(secrets, {
            "APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
            "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID",
            "NOTARYTOOL_PASSWORD"})

    @unittest.skipIf(os.environ.get("RELEASE_TEST_BUMPED_COPY"), "already in bumped copy")
    def test_entire_suite_survives_temporary_source_bump(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for path in (".github", "4D Plugin API", "test-path-name.xcodeproj",
                         "Info.plist", "English.lproj"):
                source = ROOT / path
                if source.is_dir():
                    shutil.copytree(source, target / path)
                else:
                    shutil.copy2(source, target / path)
            with cwd(target):
                current = release.read_version()
                parts = list(map(int, current.split(".")))
                parts[2] += 1
                release.write_version(".".join(map(str, parts)))
            result = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "discover", "-s", ".github/tests"],
                cwd=target, env={**os.environ, "RELEASE_TEST_BUMPED_COPY": "1"},
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(result.returncode, 0, result.stdout)
