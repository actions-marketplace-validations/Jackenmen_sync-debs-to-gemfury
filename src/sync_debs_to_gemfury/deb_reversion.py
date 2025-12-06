import contextlib
import gzip
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any, NoReturn

PACKAGE_RE = re.compile("^Package: (.+)", re.MULTILINE)
VERSION_RE = re.compile("^Version: .*", re.MULTILINE)


def _ignore_file_not_found(
    function: Callable[..., Any], path: str, exc_info: tuple[Any, BaseException, Any]
) -> None:
    del function, path
    if not isinstance(exc_info[1], FileNotFoundError):
        raise exc_info[1]


def _update_changelog_file(changelog_gz_path: str, new_version: str) -> None:
    # add changelog entry (unpack, add, repack)
    os.mkdir("debian")
    with (
        gzip.GzipFile(changelog_gz_path, "rb") as gz,
        open("debian/changelog", "wb") as fp,
    ):
        shutil.copyfileobj(gz, fp)
    subprocess.check_call(
        ("dch", "-v", new_version, "--", "Bumped version during deb synchronization."),
        env=os.environ
        | {
            "DEBFULLNAME": "github-actions[bot]",
            "DEBEMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        },
    )
    with (
        gzip.GzipFile(changelog_gz_path, "wb") as gz,
        open("debian/changelog", "rb") as fp,
    ):
        shutil.copyfileobj(fp, gz)

    # update md5sums
    with open(changelog_gz_path, "rb") as fp:
        md5sum = hashlib.file_digest(fp, "md5").hexdigest()
    with open("DEBIAN/md5sums", encoding="utf-8") as fp:
        md5sums_text = fp.read()
    md5sums_text = re.sub(
        f"^[^ ]*( *){changelog_gz_path}$",
        f"{md5sum}\1{changelog_gz_path}",
        md5sums_text,
        flags=re.MULTILINE,
    )
    with open("DEBIAN/md5sums", "w", encoding="utf-8") as fp:
        fp.write(md5sums_text)


def main() -> NoReturn:
    deb_file_path = os.path.abspath(sys.argv[1])
    new_version = sys.argv[2]
    if os.geteuid() != 0:
        print(
            "Please run this as root/fakeroot to ensure proper ownership"
            " in the resulting deb archive."
        )
        raise SystemExit(77)

    with tempfile.TemporaryDirectory() as tmpdir:
        package_dir = os.path.join(tmpdir, "package")
        os.mkdir(package_dir)
        with contextlib.chdir(package_dir):
            # extract
            subprocess.check_call(("dpkg-deb", "--extract", deb_file_path, "."))
            subprocess.check_call(("dpkg-deb", "--control", deb_file_path, "DEBIAN"))

            # change version
            with open("DEBIAN/control", encoding="utf-8") as fp:
                control_text = fp.read()
                match = PACKAGE_RE.search(control_text)
                if match is None:
                    raise RuntimeError(
                        "Could not find the package name in deb's control file."
                    )
                package_name = match[1]
                control_text = VERSION_RE.sub(f"Version: {new_version}", control_text)
            for item in ("changelog.gz", "changelog.Debian.gz"):
                changelog_gz_path = f"usr/share/doc/{package_name}/{item}"
                if os.path.isfile(changelog_gz_path):
                    try:
                        _update_changelog_file(changelog_gz_path, new_version)
                    except subprocess.CalledProcessError as exc:
                        print(
                            f"WARNING: Failed to update changelog file: {exc}\n"
                            "Leaving the changelog file unchanged."
                        )
                    break
            with open("DEBIAN/control", "w", encoding="utf-8") as fp:
                fp.write(control_text)
            shutil.rmtree("debian", onerror=_ignore_file_not_found)

            # repack
            os.chdir("..")
            subprocess.check_call(("dpkg-deb", "-b", "package"))
            shutil.move("package.deb", deb_file_path)

    raise SystemExit(0)


if __name__ == "__main__":
    main()
