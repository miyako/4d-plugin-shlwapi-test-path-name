import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"),
                     "requires macOS and Xcode's Objective-C++ compiler")
class StringOwnershipTests(unittest.TestCase):
    def test_actual_wrappers_and_bundled_sdk(self):
        with tempfile.TemporaryDirectory(prefix="shlwapi-ownership-") as directory:
            executable = pathlib.Path(directory) / "ownership"
            command = [
                "xcrun", "clang++", "-std=c++11", "-x", "objective-c++",
                "-include", "Carbon/Carbon.h", "-Wno-deprecated-declarations",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-I", str(ROOT / "4D Plugin API"),
                "-I", str(ROOT / "4D Plugin API/Classes"),
                str(ROOT / ".github/tests/string_ownership.mm"),
                str(ROOT / "4D Plugin API/4DPluginAPI.c"),
            ]
            command += [
                str(ROOT / "4D Plugin API/Classes" / filename)
                for filename in ("C_PICTURE.cpp", "C_TEXT.cpp", "C_BLOB.cpp",
                                 "ARRAY_TEXT.cpp")
            ]
            command += ["-framework", "Carbon", "-framework", "Cocoa",
                        "-o", str(executable)]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            environment = dict(os.environ, UBSAN_OPTIONS="halt_on_error=1")
            result = subprocess.run([str(executable)], capture_output=True, text=True,
                                    env=environment)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("double disposals=0, live strings=0, live handles=0",
                          result.stderr)
import os
