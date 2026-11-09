#!/usr/bin/env python3
"""Coordinated source versions and create-only release publication."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from xml.parsers.expat import ExpatError


PBX = Path("test-path-name.xcodeproj/project.pbxproj")
PLIST = Path("Info.plist")
LOCALIZED = Path("English.lproj/InfoPlist.strings")
SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
KEYS = ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION")
REFERENCES = {f"$({key})" for key in KEYS} | {f"${{{key}}}" for key in KEYS}


def run(*args, input=None):
    return subprocess.run(args, input=input, text=True, check=True,
                          stdout=subprocess.PIPE).stdout


def strict_version(value):
    if not isinstance(value, str) or not re.fullmatch(SEMVER, value):
        raise ValueError(f"Invalid source X.Y.Z version: {value!r}")
    return value


def read_version():
    fields = []
    project = PBX.read_text()
    for key in KEYS:
        values = re.findall(rf"\b{key}\s*=\s*([^;]+);", project)
        if not values:
            raise ValueError(f"Missing {key} in {PBX}")
        fields.extend(strict_version(value.strip()) for value in values)
    with PLIST.open("rb") as stream:
        plist = plistlib.load(stream)
    if not isinstance(plist, dict):
        raise ValueError("Info.plist must be a dictionary")
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if key == "CFBundleVersion" and key not in plist:
            continue
        value = plist.get(key)
        if not isinstance(value, str) or value not in REFERENCES:
            fields.append(strict_version(value))
    localized = LOCALIZED.read_bytes().decode("utf-8-sig")
    for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
        values = re.findall(rf'\b{key}\s*=\s*"([^"]*)";', localized)
        if len(values) != 1:
            raise ValueError(f"Expected exactly one localized {key}")
        value = values[0]
        if key == "CFBundleGetInfoString":
            match = re.fullmatch(r"test-path-name version (\S+), Copyright 2018 miyako\.", value)
            if not match:
                raise ValueError("Invalid localized version description")
            value = match[1]
        fields.append(strict_version(value))
    if len(set(fields)) != 1:
        raise ValueError(f"Source version drift: {fields}")
    return fields[0]


def write_version(version):
    # Validate everything before changing any source file.
    old = read_version()
    strict_version(version)
    project = PBX.read_text()
    for key in KEYS:
        project = re.sub(rf"(\b{key}\s*=\s*)[^;]+;", rf"\g<1>{version};", project)
    plist = PLIST.read_bytes()
    parsed = plistlib.loads(plist)
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if key in parsed and parsed[key] not in REFERENCES:
            pattern = rb"(<key>" + key.encode() + rb"</key>\s*<string>)[^<]*(</string>)"
            plist, count = re.subn(pattern, lambda m: m[1] + version.encode() + m[2], plist)
            if count != 1:
                raise ValueError(f"Expected exactly one XML {key}")
    localized = re.sub(
        rb'(\bCFBundleShortVersionString\s*=\s*")[^"]*(";)',
        lambda match: match[1] + version.encode() + match[2],
        LOCALIZED.read_bytes())
    localized = localized.replace(f"version {old},".encode(), f"version {version},".encode())
    PBX.write_text(project)
    PLIST.write_bytes(plist)
    LOCALIZED.write_bytes(localized)
    if read_version() != version:
        raise ValueError("Version update did not update all source fields")


def remote_refs(*refs):
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "origin", *refs],
        text=True, stdout=subprocess.PIPE, check=False)
    if result.returncode == 2 and not result.stdout:
        return {}
    if result.returncode != 0:
        raise RuntimeError(f"Remote ref lookup failed ({result.returncode})")
    found = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{40}", parts[0]):
            raise ValueError("Malformed remote ref response")
        sha, ref = parts
        if ref not in refs or ref in found:
            raise ValueError("Unexpected or duplicate remote ref")
        found[ref] = sha
    if not found:
        raise ValueError("Empty successful remote ref response")
    return found


def available(tag):
    if remote_refs(f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"):
        raise ValueError(f"Remote tag {tag} already exists; publication refused")
    repo = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository")
    seen = set()
    page = 1
    while True:
        # Explicit authenticated pages include drafts; any HTTP/JSON failure is fatal.
        response = run("gh", "api", "--include", "--method", "GET",
                       f"repos/{repo}/releases?per_page=100&page={page}")
        headers, separator, body = response.partition("\n\n")
        lines = headers.splitlines()
        if not separator or not lines or not re.fullmatch(r"HTTP/\S+ 200(?: .*)?", lines[0]):
            raise ValueError("Invalid releases HTTP response")
        entries = json.loads(body)
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError("Malformed releases page")
        has_next = any(line.lower().startswith("link:") and 'rel="next"' in line
                       for line in headers.splitlines()[1:])
        if has_next and len(entries) != 100:
            raise ValueError("Partial releases page with a next-page link")
        for entry in entries:
            if (not isinstance(entry, dict)
                    or type(entry.get("id")) is not int or entry["id"] <= 0
                    or not isinstance(entry.get("tag_name"), str) or not entry["tag_name"]
                    or type(entry.get("draft")) is not bool
                    or entry["id"] in seen):
                raise ValueError("Malformed or repeated release entry")
            seen.add(entry["id"])
            if entry["tag_name"] == tag:
                raise ValueError(f"Release {tag} already exists (including drafts); publication refused")
        if len(entries) < 100:
            return
        page += 1


def selected_sha(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected an immutable 40-character selected SHA")
    if run("git", "rev-parse", "HEAD").strip() != sha:
        raise ValueError("Checkout does not match the selected SHA")


def bump(mode):
    if os.environ["GITHUB_REF_TYPE"] != "branch":
        raise ValueError("Bump version requires a selected branch")
    branch = os.environ["GITHUB_REF_NAME"]
    ref = f"refs/heads/{branch}"
    run("git", "check-ref-format", ref)
    sha = os.environ["GITHUB_SHA"]
    selected_sha(sha)
    if remote_refs(ref) != {ref: sha}:
        raise ValueError("Selected branch has moved; rerun on its current head")
    current = read_version()
    parts = list(map(int, current.split(".")))
    index = ("major", "minor", "patch").index(mode)
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    version = ".".join(map(str, parts))
    write_version(version)
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "github-actions[bot]@users.noreply.github.com")
    run("git", "add", str(PBX), str(PLIST), str(LOCALIZED))
    run("git", "commit", "-m", f"chore: bump version to {version}")
    run("git", "push", "--no-follow-tags", "origin", f"HEAD:{ref}")
    print(f"Source version {current} -> {version}; no tag, release or build created.")


def preflight():
    selected_sha(os.environ["GITHUB_SHA"])
    version = read_version()
    tag = f"v{version}"
    available(tag)
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write(f"version={version}\ntag={tag}\n")


def publish(version, sha, assets):
    selected_sha(sha)
    if read_version() != strict_version(version):
        raise ValueError("Publication version differs from selected source")
    for asset in assets:
        if not Path(asset).is_file() or Path(asset).stat().st_size == 0:
            raise ValueError(f"Missing or empty release asset: {asset}")
    tag = f"v{version}"
    available(tag)
    # No force, rollback, mutation of existing releases, or retry after side effects.
    run("git", "push", "--no-follow-tags", "origin", f"{sha}:refs/tags/{tag}")
    if remote_refs(f"refs/tags/{tag}") != {f"refs/tags/{tag}": sha}:
        raise ValueError("Published tag does not point to the selected SHA")
    payload = dict(tag_name=tag, target_commitish=sha, name=tag,
                   body="", draft=False, prerelease=False)
    release = json.loads(run("gh", "api", "--method", "POST",
                             f"repos/{os.environ['GITHUB_REPOSITORY']}/releases",
                             "--input", "-", input=json.dumps(payload)))
    if (not isinstance(release, dict) or type(release.get("id")) is not int
            or release["id"] <= 0 or release.get("tag_name") != tag
            or release.get("draft") is not False or release.get("prerelease") is not False):
        raise ValueError("Malformed create-release response; inspect retained remote state")
    run("gh", "release", "upload", tag, *assets,
        "--repo", os.environ["GITHUB_REPOSITORY"])
    print(f"Published {tag} from {sha}")


def verify_notary(paths):
    for path in paths:
        result = json.loads(Path(path).read_text())
        if not isinstance(result, dict) or result.get("status") != "Accepted":
            raise ValueError(f"Notarization was not Accepted: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bump").add_argument("mode", choices=("patch", "minor", "major"))
    commands.add_parser("preflight")
    commands.add_parser("verify-notary").add_argument("paths", nargs="+")
    publication = commands.add_parser("publish")
    publication.add_argument("--version", required=True)
    publication.add_argument("--sha", required=True)
    publication.add_argument("assets", nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "bump":
            bump(args.mode)
        elif args.command == "preflight":
            preflight()
        elif args.command == "verify-notary":
            verify_notary(args.paths)
        else:
            publish(args.version, args.sha, args.assets)
    except (ValueError, KeyError, OSError, ExpatError,
            subprocess.CalledProcessError, RuntimeError) as error:
        print(f"::error::{error}. Stopping without rollback or retry. Any remote tag, "
              "release and uploaded assets are retained; see README recovery instructions.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
