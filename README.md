# familyTree

Browser viewer for a GEDCOM family tree: a horizontal pedigree (ancestors) or
descendant chart, with each connecting line coloured by the birth nation of the
person it leads to.

## Use

Open `viewer/index.html` in a browser and choose **Open .ged** (or drop a file on
the page). The file is parsed locally in the browser; nothing is uploaded.

To produce a single self-contained page with a tree embedded:

```sh
python3 viewer/build.py path/to/tree.ged      # writes dist/family-tree.html
```

`*.ged` files and `dist/` are git-ignored because this repository is public and
genealogy exports contain living people.

## Viewer

- **Ancestors / Descendants** — direction of the chart from the selected root.
- **Generations / Timeline** — space generations evenly, or place everyone on a year axis: each person is a lifeline from birth to death (dotted where no death is recorded), and each marriage is a ring at its year where the lines to the two parents branch. Hollow rings and dots mark estimated years.
- **Horizontal / Vertical** — generations run left to right, or top to bottom like a printed family tree (ancestors above the root, descendants below). The choice is remembered.
- **Generations** — default depth; the `+`/`−` circle on any card expands or hides that branch.
- Click a card to open the person's record; double-click (or *Centre tree on this person*) to re-root.
- Ancestor cards carry their Ahnentafel number (root = 1, father = 2n, mother = 2n+1).
  An ancestor reached by more than one line (pedigree collapse) is shown once in full;
  later occurrences read `n = m`, pointing at the first.
- **Nation of birth** is inferred from the birth place text (`viewer/viewer.html`, `nationOf`),
  reading components from the last one backwards. Places with no recognisable country,
  state or county are reported as *Unknown*.

## Merging ancestry from other exports

`tools/merge_lineage.py` extends a base GEDCOM with one person's direct ancestry
from other GEDCOM files, walking both pedigrees in step from that person:

```sh
python3 tools/merge_lineage.py --base base.ged --root @I123@ \
    --donor most_trusted.ged --donor next.ged --out merged.ged --report report.md
```

- A parent missing from the base is grafted in from a donor only if the
  parent–child dates are plausible (mother 12–55 years older, father 14–80,
  child not born after the parent's death) and the donor's family for that
  child does not otherwise contradict the base.
- Matched people get missing birth/death dates and places filled in; nothing
  already in the base is overwritten, except two kinds of correction that are
  logged: father/mother entered the wrong way round, and a parent's birth year
  replaced when the base's year fails the date checks and a donor's passes.
- Grafted people carry a NOTE naming the donor file. The report lists every
  addition, correction, conflict and rejected link for review.
