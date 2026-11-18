# hacktricks-mcp

Offline MCP server for [HackTricks](https://hacktricks.wiki/en/index.html) — the
whole wiki, searchable locally, no network at query time.

- Upstream source: [HackTricks-wiki/hacktricks](https://github.com/HackTricks-wiki/hacktricks) (markdown that generates the site)
- 1147 pages → ~2900 section-level chunks in a **SQLite FTS5** database (~31 MB)
- Startup ~0.3 s, queries 1–5 ms, no embeddings, no API keys
- Rebuild/refresh with one command (`git pull` + reindex, ~2 s incremental)

The repository ships **code only**. The corpus is downloaded and built on your
machine from the upstream repo, so you always get current content and the
upstream license/attribution stays intact.

## Requirements

- Python 3.10+ (3.13 tested)
- `git` (for the initial corpus fetch)
- Network access **only** for the first build / refresh

## Install

```bash
git clone https://github.com/<you>/hacktricks-mcp.git ~/exam/hacktricks
cd ~/exam/hacktricks
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 build_corpus.py          # clones upstream + builds hacktricks.db
```

`build_corpus.py` sparse-clones the upstream repo into `./repo/` (only markdown)
and writes `hacktricks.db` plus a human-readable `INDEX.md`.

## MCP client setup

### opencode (`~/.config/opencode/opencode.json`)

```json
{
  "mcp": {
    "hacktricks": {
      "type": "local",
      "command": ["/home/<you>/exam/hacktricks/.venv/bin/python",
                  "/home/<you>/exam/hacktricks/server.py"],
      "enabled": true
    }
  }
}
```

Restart opencode; tools appear as `hacktricks_search`, `hacktricks_read`, etc.

### Claude Desktop / other stdio MCP clients

```json
{
  "mcpServers": {
    "hacktricks": {
      "command": "/home/<you>/exam/hacktricks/.venv/bin/python",
      "args": ["/home/<you>/exam/hacktricks/server.py"]
    }
  }
}
```

Any client that speaks stdio MCP works; the server has no other dependencies
than the `mcp` package and the `.db` file next to it.

## Tools

| tool | purpose |
|---|---|
| `hacktricks_search(query, limit=5, category="")` | BM25 search over title/breadcrumb/body; returns `path#part`, page URL, snippet |
| `hacktricks_read(path, part=0, offset=0, max_chars=12000)` | read one section; long pages are split into numbered parts |
| `hacktricks_commands(query, limit=15)` | pull fenced code blocks (actual commands) from matching pages |
| `hacktricks_service(port_or_name)` | port/service → exact page, e.g. `445`, `winrm`, `kerberos`, `docker` |
| `hacktricks_outline(category="")` | category map, or the page list for one category |

Categories match the upstream tree: `network-services-pentesting`,
`pentesting-web`, `windows-hardening` (incl. `active-directory-methodology`),
`linux-hardening`, `generic-methodologies-and-resources`, `AI`, `cloud`,
containers, CI/CD, etc.

## Refresh

```bash
cd ~/exam/hacktricks
python3 build_corpus.py            # git pull + rebuild
python3 build_corpus.py --no-pull  # reindex the local checkout only
```

## How it works

- One markdown page = one technique. Pages over ~12 KB are split on `#`/`##`/`###`
  headings into parts, so a chunk is a self-contained section.
- Text is cleaned of mdBook directives (`{{#include}}`, `{% hint %}`), HTML tags
  and comments; fenced code blocks are preserved verbatim.
- Titles and breadcrumbs come from upstream `SUMMARY.md`.
- Ports are parsed from page titles (`# 139,445 - Pentesting SMB`) into a
  `services` table used by `hacktricks_service`.
- Long pages keep their full body; `hacktricks_read` pages through them with
  `part` instead of dumping the whole page.

## License and attribution

- **This repository's code** (server, builder, docs) is MIT — see `LICENSE`.
- **HackTricks content** is © Carlos Polop and contributors, licensed
  **CC BY-NC 4.0**. It is not redistributed here; `build_corpus.py` fetches it
  from the upstream repository at build time. Non-commercial use only, keep
  attribution if you publish derived datasets. See
  <https://creativecommons.org/licenses/by-nc/4.0/> and the upstream
  `src/LICENSE.md`.

Do not commit `repo/`, `hacktricks.db`, or `INDEX.md` — they are either upstream
content or generated artifacts and are already in `.gitignore`.
