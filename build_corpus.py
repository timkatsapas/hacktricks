#!/usr/bin/env python3
"""Build the HackTricks corpus: markdown -> SQLite FTS5 database + INDEX.md.

Source: github.com/HackTricks-wiki/hacktricks (markdown, same text as
https://hacktricks.wiki/en/). The repo is sparse-cloned to ./repo/src.

  python3 ~/exam/hacktricks/build_corpus.py            # update + rebuild
  python3 ~/exam/hacktricks/build_corpus.py --no-pull  # rebuild only

One markdown file = one page; pages over ~12 KB are split on headings into
parts, so a chunk is one self-contained technique section.
"""
import argparse
import os
import re
import sqlite3
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(BASE, "repo")
SRC = os.path.join(REPO, "src")
DB = os.path.join(BASE, "hacktricks.db")
INDEX = os.path.join(BASE, "INDEX.md")
REPO_URL = "https://github.com/HackTricks-wiki/hacktricks.git"
SITE = "https://hacktricks.wiki/en/"

EXCLUDE_DIRS = {"images", "files", "banners", "todo"}
MAX_CHARS = 12000
SOFT_LIMIT = 6000

HEADING = re.compile(r"^(#{1,3})\s+(.+)$")
FENCE = re.compile(r"^\s*(```|~~~)")
INCLUDE = re.compile(r"^\s*\{\{.*\}\}\s*$", re.M)
LIQUID = re.compile(r"^\s*\{%-?\s*(end)?(raw|hint|hint.*|tabs|tab.*|endtab.*|endtabs|endhint)\s*-?%\}\s*$", re.M)
HTML_BLOCK = re.compile(r"</?(sup|details|summary|p|br|div|span|img|a|b|i|u|code|pre)\b[^>]*>", re.I)
COMMENT = re.compile(r"<!--.*?-->", re.S)
MULTI_NL = re.compile(r"\n{3,}")


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def sync_repo(do_pull=True):
    if not os.path.isdir(os.path.join(REPO, ".git")):
        os.makedirs(os.path.dirname(REPO), exist_ok=True)
        run(["git", "clone", "--depth", "1", "--filter=blob:none",
             "--sparse", REPO_URL, REPO])
        run(["git", "sparse-checkout", "set", "src"], cwd=REPO)
    elif do_pull:
        r = run(["git", "pull", "--ff-only"], cwd=REPO, check=False)
        if r.returncode != 0:
            print("git pull failed (using local copy):", r.stderr.strip()[:200], file=sys.stderr)


def parse_summary():
    """path -> (title, breadcrumb). Breadcrumb from SUMMARY.md headings/list depth."""
    meta = {}
    summary = os.path.join(SRC, "SUMMARY.md")
    if not os.path.exists(summary):
        return meta
    section = ""
    stack = []
    with open(summary, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("# "):
                section = re.sub(r"[^\w\s&/+.-]", "", line[2:]).strip()
                continue
            m = re.match(r"^(\s*)-\s*\[(.+?)\]\(([^)]+\.md)\)", line)
            if not m:
                continue
            depth = len(m.group(1)) // 2
            title = m.group(2).strip()
            path = m.group(3).strip()
            stack = stack[:depth] + [title]
            crumb = " > ".join(([section] if section else []) + stack[:-1])
            meta[path] = (title, crumb)
    return meta


def clean(text):
    text = COMMENT.sub("", text)
    text = INCLUDE.sub("", text)
    text = LIQUID.sub("", text)
    out, in_fence = [], False
    for line in text.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence:
            line = HTML_BLOCK.sub("", line)
            line = re.sub(r"\{\{[^{}]*\}\}", "", line)
            line = re.sub(r"\{%[^%]*%\}", "", line)
        out.append(line)
    text = "\n".join(out)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return MULTI_NL.sub("\n\n", text).strip()


def split_page(text):
    parts, cur, size = [], [], 0
    in_fence = False
    for line in text.split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(line)
        if m and cur and size >= SOFT_LIMIT and len(m.group(1)) <= 3:
            parts.append("\n".join(cur).strip())
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
        if size > MAX_CHARS * 2 and not in_fence:
            parts.append("\n".join(cur).strip())
            cur, size = [], 0
    if cur:
        parts.append("\n".join(cur).strip())
    out = []
    for p in parts:
        if len(p) <= MAX_CHARS:
            out.append(p)
            continue
        while len(p) > MAX_CHARS:
            cut = p.rfind("\n\n", 0, MAX_CHARS)
            cut = cut if cut > MAX_CHARS // 2 else p.rfind("\n", 0, MAX_CHARS)
            out.append(p[:cut].strip())
            p = p[cut:].lstrip("\n")
        if p.strip():
            out.append(p.strip())
    return [p for p in out if len(p) > 80]


def url_for(rel):
    rel = rel[:-3] if rel.endswith(".md") else rel
    if rel.endswith("/README"):
        rel = rel[:-len("README")] + "index"
    return SITE + rel + ".html"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pull", action="store_true")
    args = ap.parse_args()
    sync_repo(do_pull=not args.no_pull)

    meta = parse_summary()
    commit = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, check=False).stdout.strip()

    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE chunks(
        id INTEGER PRIMARY KEY, path TEXT, url TEXT, category TEXT,
        title TEXT, breadcrumb TEXT, part INTEGER, nparts INTEGER, body TEXT)""")
    con.execute("""CREATE VIRTUAL TABLE fts USING fts5(
        title, breadcrumb, body, content='chunks', content_rowid='id',
        tokenize="unicode61 remove_diacritics 2")""")
    con.execute("CREATE TABLE services(port INTEGER, path TEXT, title TEXT)")

    rows, services = [], []
    files = 0
    for root, dirs, names in os.walk(SRC):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith("."))
        for name in sorted(names):
            if not name.endswith(".md") or name == "SUMMARY.md":
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, SRC).replace(os.sep, "/")
            cat = rel.split("/")[0] if "/" in rel else "root"
            title, crumb = meta.get(rel, (None, ""))
            raw = open(full, encoding="utf-8", errors="replace").read()
            text = clean(raw)
            if not title:
                m = re.search(r"^#\s+(.+)$", text, re.M)
                title = m.group(1).strip() if m else name[:-3]
            groups = split_page(text)
            files += 1
            for i, body in enumerate(groups, 1):
                rows.append((rel, url_for(rel), cat, title, crumb, i, len(groups), body))
            ports = set()
            head = re.sub(r"[^\d, \-–]", " ", title)
            for n in re.findall(r"\b\d{1,5}\b", head):
                if 1 <= int(n) <= 65535:
                    ports.add(int(n))
            for p in ports:
                services.append((p, rel, title))

    con.executemany("INSERT INTO chunks(path,url,category,title,breadcrumb,part,nparts,body) "
                    "VALUES (?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO fts(rowid,title,breadcrumb,body) SELECT id,title,breadcrumb,body FROM chunks")
    con.executemany("INSERT INTO services(port,path,title) VALUES (?,?,?)", sorted(set(services)))
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("commit", commit), ("files", str(files)), ("chunks", str(len(rows))),
        ("max_chars", str(MAX_CHARS)), ("site", SITE)])
    con.commit()
    con.execute("INSERT INTO fts(fts) VALUES('optimize')")
    con.commit()

    cats = {}
    for path, url, cat, title, crumb, part, nparts, body in rows:
        cats.setdefault(cat, {})[path] = (title, crumb, nparts)
    with open(INDEX, "w") as f:
        f.write("# HackTricks index\n\n")
        f.write(f"Source: {REPO_URL} @ {commit} -> hacktricks.db (SQLite FTS5)\n")
        f.write(f"{files} pages, {len(rows)} chunks. Website form: {SITE}<path>.html\n\n")
        for cat in sorted(cats):
            pages = cats[cat]
            f.write(f"## {cat} ({len(pages)} pages)\n")
            for path in sorted(pages):
                title, crumb, nparts = pages[path]
                f.write(f"- {title} — `{path}`\n")
            f.write("\n")
    con.close()
    size = os.path.getsize(DB) / 1048576
    print(f"built {len(rows)} chunks from {files} pages -> {DB} ({size:.1f} MB), commit {commit}")


if __name__ == "__main__":
    main()
