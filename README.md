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
- **Generations** — default depth; the `+`/`−` circle on any card expands or hides that branch.
- Click a card to open the person's record; double-click (or *Centre tree on this person*) to re-root.
- Ancestor cards carry their Ahnentafel number (root = 1, father = 2n, mother = 2n+1).
  An ancestor reached by more than one line (pedigree collapse) is shown once in full;
  later occurrences read `n = m`, pointing at the first.
- **Nation of birth** is inferred from the birth place text (`viewer/viewer.html`, `nationOf`),
  reading components from the last one backwards. Places with no recognisable country,
  state or county are reported as *Unknown*.
