#!/usr/bin/env python3
"""HackTricks MCP server: offline search over a local SQLite FTS5 build of
hacktricks.wiki (markdown mirror of github.com/HackTricks-wiki/hacktricks).

Tools (exposed by opencode as hacktricks_<name>):
  search(query, limit, category)      ranked pages+section snippets (bm25)
  read(path, part, offset, max_chars) one page section
  outline(category)                   category map, or page list for one category
  commands(query, limit)              fenced shell/code blocks from matching pages
  service(port_or_name)               port/service -> the specific HackTricks page

Rebuild the DB with ./build_corpus.py. Run: python3 ~/exam/hacktricks/server.py
"""
import json
import os
import re
import sqlite3
from typing import Optional

from mcp.server.fastmcp import FastMCP

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "hacktricks.db")
SITE = "https://hacktricks.wiki/en/"

STOP = {"the", "and", "for", "with", "from", "into", "that", "this", "are", "was",
        "how", "what", "when", "where", "which", "you", "your", "can", "use",
        "using", "get", "set", "via", "not", "but", "all", "any", "its", "our",
        "than", "then", "there", "their", "has", "have", "does", "did", "one",
        "out", "off", "per", "also", "may", "should", "must", "will"}
TOKEN = re.compile(r"[a-z0-9]+")
FENCE = re.compile(r"^\s*(```|~~~)")

mcp = FastMCP("hacktricks")


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def terms(query):
    out = []
    for t in TOKEN.findall(query.lower()):
        if len(t) > 2 and t not in STOP and t not in out:
            out.append(t)
    return out


def expr(ts, op):
    return f" {op} ".join(t + "*" if len(t) >= 4 else t for t in ts)


def query(ts, limit, category, op="AND"):
    c = con()
    sql = ("SELECT c.path,c.title,c.breadcrumb,c.part,c.nparts,c.url,"
           "bm25(fts,8.0,4.0,1.0) AS rank,"
           "snippet(fts,2,'«','»',' … ',12) AS snip "
           "FROM fts JOIN chunks c ON c.id=fts.rowid WHERE fts MATCH ?")
    args = [expr(ts, op)]
    if category:
        sql += " AND c.category = ?"
        args.append(category)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in c.execute(sql, args)]
    c.close()
    return rows


def search_rows(query_text, limit=5, category=""):
    ts = terms(query_text)
    if not ts:
        return [], ts
    rows = query(ts, limit, category, "AND")
    if len(rows) < limit and len(ts) > 1:
        seen = {(r["path"], r["part"]) for r in rows}
        for r in query(ts, limit * 2, category, "OR"):
            if (r["path"], r["part"]) not in seen:
                rows.append(r)
                seen.add((r["path"], r["part"]))
            if len(rows) >= limit:
                break
    rows.sort(key=lambda r: r["rank"])
    return rows[:limit], ts


def port_hints(ts):
    ports = [int(t) for t in ts if t.isdigit() and 1 <= int(t) <= 65535]
    if not ports:
        return []
    c = con()
    rows = []
    for p in ports:
        for r in c.execute("SELECT port,path,title FROM services WHERE port=? ORDER BY path", (p,)):
            rows.append(dict(r))
    c.close()
    return rows[:6]


@mcp.tool()
def search(query: str, limit: int = 5, category: str = "") -> str:
    """Search HackTricks (offline mirror of hacktricks.wiki) for a technique, service, tool, port or error string.

    query: natural keywords, e.g. 'smb relay', 'kerberoast', 'docker escape', '445'.
    category: optional top-level filter: network-services-pentesting, pentesting-web,
      windows-hardening, linux-hardening, generic-methodologies-and-resources, AI, etc.
    Returns path, part, url, cite (path#part) and a snippet.
    """
    rows, ts = search_rows(query, limit, category)
    out = {"query": query, "terms": ts, "hits": []}
    hints = port_hints(ts)
    if hints:
        out["port_pages"] = hints
    for r in rows:
        out["hits"].append({
            "cite": f"{r['path']}#{r['part']}",
            "title": r["title"],
            "part": f"{r['part']}/{r['nparts']}",
            "breadcrumb": r["breadcrumb"],
            "url": r["url"],
            "snippet": r["snip"],
        })
    if not rows:
        out["note"] = "no hits; try fewer/simpler keywords or a category filter"
    return json.dumps(out, indent=1)


@mcp.tool()
def read(path: str, part: int = 0, offset: int = 0, max_chars: int = 12000) -> str:
    """Read one HackTricks page section. path may be a relative path, a URL, or a unique substring
    (e.g. 'pentesting-smb'). Omit part to get the first section; the header lists other parts."""
    c = con()
    rows = [dict(r) for r in c.execute(
        "SELECT path,url,title,breadcrumb,part,nparts,body FROM chunks "
        "WHERE path=? ORDER BY part", (path.replace(SITE, ""),))]
    if not rows:
        like = path.replace(SITE, "").replace(".html", "")
        rows = [dict(r) for r in c.execute(
            "SELECT path,url,title,breadcrumb,part,nparts,body FROM chunks "
            "WHERE path LIKE ? ORDER BY path,part", (f"%{like}%",))]
    c.close()
    if not rows:
        return "no page matched; run search() or outline() first"
    paths = sorted({r["path"] for r in rows})
    if len(paths) > 1:
        stem = path.rstrip("/").split("/")[-1]
        exact = [p for p in paths if p.split("/")[-1] in (stem, stem + ".md")]
        dirpage = [p for p in paths if p.endswith(f"/{stem}/README.md")]
        pick = exact or dirpage
        if len(pick) == 1:
            rows = [r for r in rows if r["path"] == pick[0]]
            paths = pick
    if len(paths) > 1:
        return "ambiguous path, pass a longer substring:\n" + "\n".join(paths[:12])
    parts = sorted(rows, key=lambda r: r["part"])
    sel = next((r for r in parts if r["part"] == part), parts[0]) if part else parts[0]
    head = (f"# {sel['title']}" + (f" (part {sel['part']}/{sel['nparts']})" if sel["nparts"] > 1 else "")
            + f"\n{sel['breadcrumb']}\n{sel['url']}\nsource: {sel['path']}\n\n")
    body = sel["body"][offset:offset + max_chars]
    tail = ""
    if sel["nparts"] > 1:
        tail += f"\n\n[other parts: 1..{sel['nparts']} via part=N]"
    if offset + max_chars < len(sel["body"]):
        tail += f"\n[truncated: offset={offset + max_chars} of {len(sel['body'])}]"
    return head + body + tail


@mcp.tool()
def outline(category: str = "") -> str:
    """Category map of HackTricks (page counts), or the page list for one category."""
    c = con()
    meta = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM meta")}
    if not category:
        rows = c.execute("SELECT category, count(DISTINCT path) p, count(*) ch FROM chunks "
                         "GROUP BY category ORDER BY p DESC").fetchall()
        c.close()
        lines = [f"HackTricks mirror @ {meta.get('commit','?')}: {meta.get('files','?')} pages, "
                 f"{meta.get('chunks','?')} chunks"]
        lines += [f"{r['category']:<38} {r['p']:>4} pages  {r['ch']:>4} chunks" for r in rows]
        lines.append("\ncall outline(category='network-services-pentesting') for its page list")
        return "\n".join(lines)
    rows = c.execute("SELECT path,max(title) t FROM chunks WHERE category=? "
                     "GROUP BY path ORDER BY path", (category,)).fetchall()
    c.close()
    if not rows:
        return f"no category '{category}'; call outline() for the list"
    return "\n".join(f"{r['t']} — {r['path']}" for r in rows)


@mcp.tool()
def commands(query: str, limit: int = 15, category: str = "") -> str:
    """Extract runnable code blocks (fenced ``` blocks) from the HackTricks pages that best match a query.
    Use before improvising a service exploit; pair with search() for context."""
    rows, ts = search_rows(query, 8, category)
    hits = []
    c = con()
    for r in rows:
        body = c.execute("SELECT body FROM chunks WHERE path=? AND part=?",
                         (r["path"], r["part"])).fetchone()[0]
        lines = body.split("\n")
        i = 0
        while i < len(lines):
            if FENCE.match(lines[i]):
                j = i + 1
                while j < len(lines) and not FENCE.match(lines[j]):
                    j += 1
                block = "\n".join(lines[i + 1:j]).strip()
                ctx = lines[i - 1].strip() if i else ""
                low = (block + " " + ctx).lower()
                if block and any(t in low for t in ts):
                    hits.append({
                        "cite": f"{r['path']}#{r['part']}",
                        "title": r["title"],
                        "url": r["url"],
                        "context": re.sub(r"\s+", " ", ctx)[:150],
                        "command": block[:800],
                    })
                i = j + 1
            else:
                i += 1
        if len(hits) >= limit:
            break
    c.close()
    return json.dumps({"query": query, "terms": ts, "commands": hits[:limit]}, indent=1)


@mcp.tool()
def service(port_or_name: str) -> str:
    """Map a port number or service name to the exact HackTricks page(s) for it.
    e.g. service('445'), service('winrm'), service('kubernetes'), service('jenkins')."""
    c = con()
    out = []
    q = port_or_name.strip().lower()
    if q.isdigit():
        out = [dict(r) for r in c.execute(
            "SELECT port,path,title FROM services WHERE port=? ORDER BY path", (int(q),))]
    if not out:
        cands = [dict(r) for r in c.execute(
            "SELECT path,max(title) title FROM chunks "
            "WHERE lower(title) LIKE ? OR lower(path) LIKE ? GROUP BY path",
            (f"%{q}%", f"%{q}%"))]
        word = re.compile(rf"\b{re.escape(q)}\b")

        def rank(x):
            sc = 0.0
            if f"pentesting-{q}" in x["path"]:
                sc += 12
            if word.search(x["title"].lower()):
                sc += 8
            if x["path"].startswith("network-services-pentesting"):
                sc += 4
            return -sc + len(x["path"]) / 1000

        cands.sort(key=rank)
        out = cands[:8]
    c.close()
    if not out:
        hits, _ = search_rows(q, 5)
        out = [{"path": h["path"], "title": h["title"]} for h in hits]
    for o in out:
        o["cite"] = o["path"]
        rel = o["path"][:-3] if o["path"].endswith(".md") else o["path"]
        if rel.endswith("/README"):
            rel = rel[:-len("README")] + "index"
        o["url"] = SITE + rel + ".html"
    return json.dumps({"query": port_or_name, "pages": out}, indent=1)


if __name__ == "__main__":
    import sys
    print(f"hacktricks MCP: {os.path.getsize(DB) / 1048576:.1f} MB index", file=sys.stderr)
    mcp.run()
