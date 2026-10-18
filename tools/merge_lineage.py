#!/usr/bin/env python3
"""Extend a base GEDCOM with a person's direct ancestry from other GEDCOM files.

    python3 tools/merge_lineage.py --base base.ged --root @I123@ \
        --donor original.ged --donor other.ged ... --out merged.ged --report review.md

Donors are consulted in the order given (most trusted first). Starting at the
root person, the base and each donor are walked up the pedigree in step:

* both have a parent and they look like the same person -> fill facts the base
  is missing (birth/death date or place), then keep walking;
* both have a parent but they differ                  -> logged as a conflict,
  base kept;
* only the donor has the parent                        -> the parent is grafted
  in (with its own ancestry) if the parent-child link passes date checks;
  otherwise the link is logged as rejected.

Nothing in the base is deleted or overwritten. Every grafted person carries a
NOTE naming the donor file, and every filled fact is listed in the report.
"""
import argparse
import difflib
import re
import unicodedata
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------- GEDCOM I/O
class Node:
    __slots__ = ("tag", "value", "xref", "children")

    def __init__(self, tag, value="", xref=None, children=None):
        self.tag, self.value, self.xref = tag, value, xref
        self.children = children if children is not None else []

    def first(self, tag):
        return next((c for c in self.children if c.tag == tag), None)

    def all(self, tag):
        return [c for c in self.children if c.tag == tag]

    def text(self):
        out = self.value
        for c in self.children:
            if c.tag == "CONC":
                out += c.value
            elif c.tag == "CONT":
                out += "\n" + c.value
        return out

    def copy(self):
        return Node(self.tag, self.value, self.xref, [c.copy() for c in self.children])


LINE = re.compile(r"^\s*(\d+)\s+(@[^@]+@\s+)?(\S+)(?:\s(.*))?$")


def parse(path):
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    root = Node("ROOT")
    stack = [root]
    for raw in text.splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        level = int(m.group(1))
        node = Node(m.group(3), m.group(4) or "", m.group(2).strip() if m.group(2) else None)
        parent = stack[min(level, len(stack) - 1)]
        parent.children.append(node)
        del stack[level + 1:]
        stack.append(node)
    return root.children


def serialize(records):
    out = []

    def emit(n, level):
        head = f"{level} {n.xref + ' ' if n.xref else ''}{n.tag}"
        out.append(head + (f" {n.value}" if n.value != "" else ""))
        for c in n.children:
            emit(c, level + 1)

    for r in records:
        emit(r, 0)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- tree model
YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def year(s):
    m = YEAR.search(s or "")
    return int(m.group(1)) if m else None


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens(s):
    s = strip_accents(s.lower())
    s = re.sub(r"[^a-z ]", " ", s)
    return [t for t in s.split() if t not in {"sr", "jr", "ii", "iii", "iv", "vii", "sir", "dr", "mr", "mrs", "rev", "capt"}]


class Tree:
    def __init__(self, path):
        self.path = Path(path)
        self.records = parse(path)
        self.by_id = {r.xref: r for r in self.records if r.xref}
        self.indi = {r.xref: r for r in self.records if r.tag == "INDI" and r.xref}
        self.fam = {r.xref: r for r in self.records if r.tag == "FAM" and r.xref}

    # -- person facts
    def name(self, pid):
        n = self.indi[pid].first("NAME")
        if not n:
            return "(no name)"
        given = n.first("GIVN").value if n.first("GIVN") else n.value.split("/")[0]
        sur = n.first("SURN").value if n.first("SURN") else (n.value.split("/") + ["", ""])[1]
        return " ".join(x for x in (given.strip(), sur.strip()) if x) or "(no name)"

    def given_surname(self, pid):
        n = self.indi[pid].first("NAME")
        if not n:
            return [], []
        given = n.first("GIVN").value if n.first("GIVN") else n.value.split("/")[0]
        sur = n.first("SURN").value if n.first("SURN") else (n.value.split("/") + ["", ""])[1]
        return tokens(given), tokens(sur)

    def event(self, pid, tags):
        for t in tags:
            e = self.indi[pid].first(t)
            if e:
                return e
        return None

    def by(self, pid):
        e = self.event(pid, ("BIRT", "BAPM", "CHR"))
        return year(e.first("DATE").value) if e and e.first("DATE") else None

    def dy(self, pid):
        e = self.event(pid, ("DEAT", "BURI"))
        return year(e.first("DATE").value) if e and e.first("DATE") else None

    def sex(self, pid):
        s = self.indi[pid].first("SEX")
        return s.value.strip() if s else "U"

    def label(self, pid):
        b, d = self.by(pid), self.dy(pid)
        return f"{self.name(pid)} ({b or '?'}–{d or ''})"

    # -- relationships
    def parent_fam(self, pid):
        for c in self.indi[pid].all("FAMC"):
            f = self.fam.get(c.value.strip())
            if f and (f.first("HUSB") or f.first("WIFE")):
                return f
        for c in self.indi[pid].all("FAMC"):
            f = self.fam.get(c.value.strip())
            if f:
                return f
        return None

    def parent(self, pid, role):
        f = self.parent_fam(pid)
        if not f:
            return None
        n = f.first(role)
        pid2 = n.value.strip() if n else None
        return pid2 if pid2 in self.indi else None


# ---------------------------------------------------------------- matching
def compare(a, aid, b, bid):
    """'same', 'disputed' (same person, birth years disagree) or None.

    Tolerates married vs maiden surnames, initials, nicknames and spelling variants."""
    ag, asur = a.given_surname(aid)
    bg, bsur = b.given_surname(bid)
    if not ag or not bg:
        return None
    g1, g2 = ag[0], bg[0]
    first_ok = g1 == g2 or (len(g1) == 1 and g2.startswith(g1)) or (len(g2) == 1 and g1.startswith(g2)) \
        or difflib.SequenceMatcher(None, g1, g2).ratio() >= 0.8
    # any shared given name of 3+ letters ("Alice M" / "Margaret Alice", "Willis" / "William \"Willis\"")
    given_ok = first_ok or bool({t for t in ag if len(t) > 2} & {t for t in bg if len(t) > 2})
    if not given_ok:
        return None
    at, bt = set(ag + asur), set(bg + bsur)
    sur_ok = bool(set(asur) & bt) or bool(set(bsur) & at) or not asur or not bsur or any(
        difflib.SequenceMatcher(None, x, y).ratio() >= 0.8 for x in asur for y in bsur)
    ya, yb, da, db = a.by(aid), b.by(bid), a.dy(aid), b.dy(bid)
    if not sur_ok:
        # married vs maiden name: accept only when birth years are close and death years agree exactly
        return "same" if ya and yb and abs(ya - yb) <= 3 and da and da == db else None
    if ya and yb:
        if abs(ya - yb) <= 3 or (abs(ya - yb) <= 5 and at == bt):
            return "same"
        # same name and same death year, birth year disputed
        return "disputed" if first_ok and da and da == db else None
    return "same"


def same_person(a, aid, b, bid):
    return compare(a, aid, b, bid) == "same"


def link_problem(tree_c, cid, tree_p, pid, role):
    """Return a reason string if 'pid' cannot plausibly be cid's parent, else None."""
    cb = tree_c.by(cid)
    pb, pd = tree_p.by(pid), tree_p.dy(pid)
    if pb and pd and not (0 <= pd - pb <= 110):
        return f"parent's own dates impossible ({pb}–{pd})"
    if cb and pb:
        gap = cb - pb
        lo, hi = (12, 55) if role == "WIFE" else (14, 80)
        if not lo <= gap <= hi:
            return f"parent born {pb}, child born {cb} ({gap} years apart)"
    if cb and pd:
        if role == "WIFE" and cb > pd:
            return f"child born {cb} after mother died {pd}"
        if role == "HUSB" and cb > pd + 1:
            return f"child born {cb} after father died {pd}"
    return None


# ---------------------------------------------------------------- merge
class Merger:
    def __init__(self, base, root):
        self.base, self.root = base, root
        self.new_id = defaultdict(int)
        self.log = defaultdict(list)          # section -> lines
        self.added = defaultdict(int)          # donor -> count
        self.filled = defaultdict(int)
        self.mapped = {}                       # (donor, donor_id) -> base_id
        self.imported_sources = {}             # (donor, sid) -> base sid

    def fresh(self, prefix):
        while True:
            self.new_id[prefix] += 1
            x = f"@{prefix}{self.new_id[prefix]}@"
            if x not in self.base.by_id:
                return x

    def add_record(self, rec):
        self.base.records.insert(len(self.base.records), rec)
        self.base.by_id[rec.xref] = rec
        if rec.tag == "INDI":
            self.base.indi[rec.xref] = rec
        elif rec.tag == "FAM":
            self.base.fam[rec.xref] = rec

    def import_source(self, donor, sid):
        key = (donor.path.name, sid)
        if key in self.imported_sources:
            return self.imported_sources[key]
        rec = donor.by_id.get(sid)
        if rec is None or rec.tag != "SOUR":
            return None
        if sid not in self.base.by_id:
            new = sid                             # same ID free in base: keep it (repairs dangling citations)
        else:
            existing = self.base.by_id[sid]
            same = existing.tag == "SOUR" and (existing.first("TITL") or Node("")).value == (rec.first("TITL") or Node("")).value
            new = sid if same else self.fresh("SM")
            if same:
                self.imported_sources[key] = sid
                return sid
        r = rec.copy()
        r.xref = new
        self.add_record(r)
        self.imported_sources[key] = new
        return new

    def clean_copy(self, donor, node):
        """Copy a subtree, re-pointing source citations and dropping media/family pointers."""
        out = Node(node.tag, node.value, None, [])
        for c in node.children:
            if c.tag in ("FAMC", "FAMS", "OBJE") or (c.tag.startswith("_") and c.tag not in ("_MILT",)):
                continue
            if c.tag == "SOUR" and c.value.startswith("@"):
                sid = self.import_source(donor, c.value.strip())
                if not sid:
                    continue
                cc = self.clean_copy(donor, c)
                cc.value = sid
                out.children.append(cc)
                continue
            out.children.append(self.clean_copy(donor, c))
        return out

    def import_person(self, donor, did):
        new = self.fresh("M")
        rec = self.clean_copy(donor, donor.indi[did])
        rec.xref = new
        rec.children.append(Node("NOTE", f"Added from {donor.path.name} (ancestry merge, Oct 2026). Source ID {did}."))
        self.add_record(rec)
        self.added[donor.path.name] += 1
        return new

    def find_existing(self, donor, did):
        """A base person who is unambiguously the donor person (avoid duplicating pedigree collapse)."""
        dname = " ".join(tokens(donor.name(did)))
        db = donor.by(did)
        if not db:
            return None
        hits = [b for b in self.base.indi if self.base.by(b) == db and " ".join(tokens(self.base.name(b))) == dname]
        return hits[0] if len(hits) == 1 else None

    def attach_parent(self, child, parent, role):
        f = self.base.parent_fam(child)
        if f is None or f.first(role) is not None:
            f = Node("FAM", "", self.fresh("MF"), [Node("CHIL", child)])
            self.add_record(f)
            self.base.indi[child].children.append(Node("FAMC", f.xref))
        f.children.insert(0, Node(role, parent))
        self.base.indi[parent].children.append(Node("FAMS", f.xref))

    def fill_facts(self, bid, donor, did):
        brec, drec = self.base.indi[bid], donor.indi[did]
        for tag, label in (("BIRT", "birth"), ("DEAT", "death")):
            de = drec.first(tag)
            if not de:
                continue
            be = brec.first(tag)
            if be is None:
                if tag == "DEAT" and not (de.first("DATE") or de.first("PLAC")):
                    continue
                if de.first("DATE") and not self.date_fits(bid, year(de.first("DATE").value), tag):
                    self.log["Facts not used (would break date checks)"].append(
                        f"{self.base.label(bid)}: {label} `{self.ev(de)}` from {donor.path.name}")
                    continue
                brec.children.append(self.clean_copy(donor, de))
                self.note_fill(bid, donor, f"{label} added: {self.ev(de)}")
                continue
            for sub in ("DATE", "PLAC"):
                dv = de.first(sub)
                bv = be.first(sub)
                if dv and dv.value.strip() and (bv is None or not bv.value.strip()):
                    if sub == "DATE" and not self.date_fits(bid, year(dv.value), tag):
                        self.log["Facts not used (would break date checks)"].append(
                            f"{self.base.label(bid)}: {label} date `{dv.value}` from {donor.path.name}")
                        continue
                    if bv is None:
                        be.children.insert(0 if sub == "DATE" else len(be.children), Node(sub, dv.value))
                    else:
                        bv.value = dv.value
                    self.note_fill(bid, donor, f"{label} {sub.lower()} added: {dv.value}")
                elif dv and bv and sub == "DATE":
                    yb, yd = year(bv.value), year(dv.value)
                    if yb and yd and abs(yb - yd) > 2:
                        self.log["Date disagreements (base kept)"].append(
                            f"{self.base.label(bid)}: {label} `{bv.value}` in base vs `{dv.value}` in {donor.path.name}")

    def date_fits(self, bid, y, tag):
        if y is None:
            return True
        by, dy = self.base.by(bid), self.base.dy(bid)
        if tag == "BIRT" and dy and not 0 <= dy - y <= 110:
            return False
        if tag == "BIRT":
            for role in ("HUSB", "WIFE"):
                par = self.base.parent(bid, role)
                pb = self.base.by(par) if par else None
                if pb and not (12 <= y - pb <= 80):
                    return False
        if tag == "DEAT" and by and not 0 <= y - by <= 110:
            return False
        return True

    @staticmethod
    def ev(e):
        return ", ".join(x for x in ((e.first("DATE") or Node("")).value, (e.first("PLAC") or Node("")).value) if x) or "(no details)"

    def note_fill(self, bid, donor, what):
        self.filled[donor.path.name] += 1
        self.log["Facts filled in"].append(f"{self.base.label(bid)} — {what} ({donor.path.name})")

    def align(self, bid, donor, did, path, seen):
        if (bid, did) in seen or len(path) > 40:
            return
        seen.add((bid, did))
        self.mapped[(donor.path.name, did)] = bid
        B = self.base
        dpars = {r: donor.parent(did, r) for r in ("HUSB", "WIFE")}
        bpars = {r: B.parent(bid, r) for r in ("HUSB", "WIFE")}

        # Father and mother entered the wrong way round in the base?
        if all(dpars.values()) and all(bpars.values()) \
                and same_person(B, bpars["HUSB"], donor, dpars["WIFE"]) and same_person(B, bpars["WIFE"], donor, dpars["HUSB"]) \
                and not same_person(B, bpars["HUSB"], donor, dpars["HUSB"]):
            f = B.parent_fam(bid)
            for n in f.children:
                if n.tag in ("HUSB", "WIFE"):
                    n.tag = "WIFE" if n.tag == "HUSB" else "HUSB"
            self.log["Corrections"].append(
                f"{B.label(bid)}: father and mother were swapped; now father {B.label(bpars['WIFE'])}, mother {B.label(bpars['HUSB'])} (per {donor.path.name})")
            bpars = {"HUSB": bpars["WIFE"], "WIFE": bpars["HUSB"]}

        plan, conflict = [], False
        for role, rel in (("HUSB", "father"), ("WIFE", "mother")):
            dpar, bpar = dpars[role], bpars[role]
            if not dpar:
                continue
            if not bpar:
                plan.append((role, rel, dpar, "graft"))
                continue
            how = compare(B, bpar, donor, dpar)
            donor_bad = link_problem(B, bid, donor, dpar, role)
            base_bad = link_problem(B, bid, B, bpar, role)
            if how == "same" and not (donor_bad and not B.by(bpar)):
                plan.append((role, rel, dpar, ("match", bpar)))
            elif how == "disputed" and base_bad and not donor_bad:
                plan.append((role, rel, dpar, ("correct", bpar)))
            elif how == "disputed":
                self.log["Date disagreements (base kept)"].append(
                    f"{B.label(bpar)}: birth year {B.by(bpar)} in base vs {donor.by(dpar)} in {donor.path.name}")
                plan.append((role, rel, dpar, ("match", bpar)))
            else:
                conflict = True
                self.log["Conflicting parents (base kept)"].append(
                    f"{B.label(bid)} — {rel}: base has **{B.label(bpar)}**, {donor.path.name} has **{donor.label(dpar)}**")

        for role, rel, dpar, action in plan:
            if action == "graft":
                if conflict:
                    self.log["Links not added (donor's family for this person disagrees with the base)"].append(
                        f"{B.label(bid)} — {rel} {donor.label(dpar)} from {donor.path.name}")
                    continue
                why = link_problem(B, bid, donor, dpar, role)
                if why:
                    self.log["Links rejected (failed date checks)"].append(
                        f"{B.label(bid)} — {rel} {donor.label(dpar)} from {donor.path.name}: {why}")
                    continue
                existing = self.mapped.get((donor.path.name, dpar)) or self.find_existing(donor, dpar)
                if existing and existing != bid:
                    self.attach_parent(bid, existing, role)
                    self.log["Links added to people already in the tree"].append(
                        f"{B.label(bid)} — {rel} {B.label(existing)} ({donor.path.name})")
                    self.align(existing, donor, dpar, path + rel[0].upper(), seen)
                    continue
                new = self.import_person(donor, dpar)
                self.attach_parent(bid, new, role)
                self.log["People added"].append(
                    f"{path + rel[0].upper()}: {B.label(new)} — {rel} of {B.label(bid)} ({donor.path.name})")
                self.align(new, donor, dpar, path + rel[0].upper(), seen)
                continue
            kind, bpar = action
            if kind == "correct":
                old = B.label(bpar)
                be = B.indi[bpar].first("BIRT")
                de = donor.indi[dpar].first("BIRT")
                be.children = [c for c in be.children if c.tag != "DATE"]
                be.children.insert(0, Node("DATE", de.first("DATE").value))
                B.indi[bpar].children.append(Node("NOTE", f"Birth date corrected from {old} using {donor.path.name}: the earlier date made them younger than allowed for a parent of {B.label(bid)}."))
                self.log["Corrections"].append(f"{old} → born {de.first('DATE').value} (per {donor.path.name}); parent of {B.label(bid)}")
            self.fill_facts(bpar, donor, dpar)
            self.align(bpar, donor, dpar, path + rel[0].upper(), seen)


def ancestors(tree, pid):
    seen, stack = set(), [pid]
    while stack:
        p = stack.pop()
        if not p or p in seen:
            continue
        seen.add(p)
        stack += [tree.parent(p, "HUSB"), tree.parent(p, "WIFE")]
    return seen


def audit(tree, root):
    out = []
    for p in sorted(ancestors(tree, root)):
        for role, rel in (("HUSB", "father"), ("WIFE", "mother")):
            par = tree.parent(p, role)
            if par:
                why = link_problem(tree, p, tree, par, role)
                if why:
                    out.append(f"{tree.label(p)} — {rel} {tree.label(par)}: {why}")
    return out


def find_root(tree, base, root):
    """The donor's record for the root person: same name, birth year ±1, father matching the base's father."""
    bfa = base.parent(root, "HUSB")
    for d in tree.indi:
        if same_person(base, root, tree, d) or (tokens(tree.name(d))[:1] == tokens(base.name(root))[:1]
                                               and tokens(tree.name(d))[-1:] == tokens(base.name(root))[-1:]
                                               and tree.by(d) and base.by(root) and abs(tree.by(d) - base.by(root)) <= 1):
            dfa = tree.parent(d, "HUSB")
            if dfa and bfa and same_person(base, bfa, tree, dfa):
                return d
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--root", required=True, help="xref of the root person in the base, e.g. @I123@")
    ap.add_argument("--donor", action="append", default=[], help="donor file, most trusted first")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    base = Tree(args.base)
    root = args.root
    before = ancestors(base, root)
    before_audit = audit(base, root)
    m = Merger(base, root)
    roots = {}
    for path in args.donor:
        donor = Tree(path)
        d = find_root(donor, base, root)
        roots[donor.path.name] = d
        if d:
            m.align(root, donor, d, "", set())
        else:
            m.log["Donors skipped"].append(f"{donor.path.name}: root person not found")

    # Citations in the base that point at source records it lacks: take them from a donor that defines the same ID.
    # Only long numeric Ancestry IDs (@S123456789@) are global; short IDs like @S12@ are file-local and could collide.
    cited = set()

    def walk(n):
        if n.tag == "SOUR" and n.value.startswith("@"):
            cited.add(n.value.strip())
        for c in n.children:
            walk(c)

    for r in base.records:
        walk(r)
    repaired = 0
    for path in args.donor:
        donor = Tree(path)
        for sid in sorted(cited):
            if sid not in base.by_id and re.fullmatch(r"@S\d{6,}@", sid) and sid in donor.by_id:
                m.import_source(donor, sid)
                repaired += 1
    m.log["Source records restored"].append(
        f"{repaired} of {len([s for s in cited if s not in base.by_id]) + repaired} source records cited but missing in the base were restored from donors")

    # Root person first, so viewers that open on the first record open on them.
    rec = base.indi[root]
    base.records.remove(rec)
    base.records.insert(1 if base.records and base.records[0].tag == "HEAD" else 0, rec)

    head = next((r for r in base.records if r.tag == "HEAD"), None)
    if head is not None:
        head.children = [Node("GEDC", "", None, [Node("VERS", "5.5.1"), Node("FORM", "LINEAGE-LINKED")]),
                         Node("CHAR", "UTF-8"),
                         Node("SOUR", "familyTree-merge_lineage", None, [Node("NAME", "familyTree tools/merge_lineage.py")])]
    if not base.records or base.records[-1].tag != "TRLR":
        base.records.append(Node("TRLR"))
    else:
        base.records.append(base.records.pop(base.records.index(next(r for r in base.records if r.tag == "TRLR"))))
    Path(args.out).write_text(serialize(base.records), encoding="utf-8")

    after = ancestors(base, root)
    after_audit = audit(base, root)
    side = lambda role: len(ancestors(base, base.parent(root, role))) if base.parent(root, role) else 0
    lines = [f"# Ancestry merge report — {base.label(root)}", "",
             f"Base: `{Path(args.base).name}`. Donors, most trusted first: " + ", ".join(f"`{Path(p).name}`" for p in args.donor) + ".", "",
             "| | Before | After |", "|---|---|---|",
             f"| Direct ancestors (incl. root) | {len(before)} | {len(after)} |",
             f"| Paternal / maternal ancestors | | {side('HUSB')} / {side('WIFE')} |",
             f"| Links failing date checks | {len(before_audit)} | {len(after_audit)} |", "",
             "| Donor | Matched root | People added | Facts filled |", "|---|---|---|---|"]
    for path in args.donor:
        n = Path(path).name
        lines.append(f"| `{n}` | {roots.get(n) or '—'} | {m.added[n]} | {m.filled[n]} |")
    lines.append("")
    order = ["Source records restored", "Corrections", "People added", "Links added to people already in the tree", "Facts filled in", "Conflicting parents (base kept)",
             "Date disagreements (base kept)", "Links rejected (failed date checks)", "Links not added (donor's family for this person disagrees with the base)", "Facts not used (would break date checks)", "Donors skipped"]
    for sec in order:
        items = list(dict.fromkeys(m.log.get(sec, [])))
        if not items:
            continue
        lines += [f"## {sec} ({len(items)})", ""] + [f"- {x}" for x in items] + [""]
    lines += [f"## Links in the merged tree that still fail date checks ({len(after_audit)})", "",
              "These were already in the base and are left unchanged for review.", ""] + [f"- {x}" for x in after_audit] + [""]
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:14]))


if __name__ == "__main__":
    main()
