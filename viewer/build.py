#!/usr/bin/env python3
"""Build the family-tree viewer.

    python3 viewer/build.py                      # viewer/index.html, no data (open a .ged in the browser)
    python3 viewer/build.py tree.ged             # dist/family-tree.html with tree.ged embedded
    python3 viewer/build.py tree.ged --fragment  # same, without the <html>/<head> wrapper

Builds that embed a GEDCOM go to dist/, which is git-ignored: the data
contains living people and should not be pushed to a public repository.
"""
import argparse
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE / "viewer.html"
PLACEHOLDER = "/*GEDCOM*/"

WRAPPER = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>*,*::before,*::after{box-sizing:border-box}body{margin:0;font:14px system-ui,sans-serif}[hidden]{display:none!important}</style>
</head>
<body>
{content}
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gedcom", nargs="?", help="GEDCOM file to embed")
    ap.add_argument("--fragment", action="store_true", help="omit the html/head/body wrapper")
    ap.add_argument("-o", "--out", help="output path")
    args = ap.parse_args()

    page = SOURCE.read_text(encoding="utf-8")
    if args.gedcom:
        ged = pathlib.Path(args.gedcom).read_text(encoding="utf-8-sig", errors="replace")
        # A literal "</script" inside the data would end the <script> block early.
        ged = re.sub(r"</script", r"<\\/script", ged, flags=re.I)
        page = page.replace(PLACEHOLDER, ged, 1)
        default_out = HERE.parent / "dist" / "family-tree.html"
    else:
        default_out = HERE / "index.html"

    if not args.fragment:
        page = WRAPPER.replace("{content}", page)
    out = pathlib.Path(args.out) if args.out else default_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
