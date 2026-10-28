#!/usr/bin/env python3
"""
Generates self-hosted, animated GitHub stats cards (SVG) for a profile README.

Why: public stat services (github-readme-stats on Vercel, etc.) are often
rate-limited or down. These cards are built from the GitHub API by a GitHub
Action and committed to your own repo, so they always load.

Only the Python standard library is used. Environment variables:
  GITHUB_TOKEN   token provided automatically by GitHub Actions
  GH_USER        GitHub username (set to the repo owner in the workflow)
  DISPLAY_NAME   name shown in the card title
  EXCLUDE_LANGS  comma-separated languages to hide (e.g. "HTML,CSS")
  OUT_DIR        output folder (default: assets)
  OFFLINE=1      write placeholder cards without calling the API
"""
import datetime
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

USER = os.environ.get("GH_USER", "safia-itouchene")
NAME = os.environ.get("DISPLAY_NAME", "Safia")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
OUT = os.environ.get("OUT_DIR", "assets")
OFFLINE = os.environ.get("OFFLINE") == "1"
EXCLUDE = {x.strip() for x in os.environ.get("EXCLUDE_LANGS", "").split(",") if x.strip()}
API = "https://api.github.com"

LANG_COLORS = {
    "Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "PHP": "#8892BF", "Java": "#e76f00", "C": "#8d8d8d", "C++": "#f34b7d",
    "HTML": "#e34c26", "CSS": "#8b5cf6", "SCSS": "#c6538c", "Blade": "#f7523f",
    "Jupyter Notebook": "#DA5B0B", "Vue": "#41b883", "Shell": "#89e051",
    "Dockerfile": "#2496ed", "Go": "#00ADD8", "Rust": "#dea584", "Kotlin": "#A97BFF",
    "Dart": "#00B4AB", "SQL": "#e38c00", "PLpgSQL": "#336790", "Makefile": "#427819",
}
FONT = "'Segoe UI',Ubuntu,'Helvetica Neue',Arial,sans-serif"


def esc(s):
    return html.escape(str(s), quote=True)


def fmt(v):
    return "–" if v is None else f"{v:,}"


# ----------------------------------------------------------------- API helpers
def _headers():
    h = {"Accept": "application/vnd.github+json", "User-Agent": "profile-stats-generator"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def rest(path):
    req = urllib.request.Request(API + path, headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def paginate(path):
    items, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        chunk = rest(f"{path}{sep}per_page=100&page={page}")
        items += chunk
        if len(chunk) < 100:
            return items
        page += 1


def graphql(query, variables):
    h = _headers()
    h["Content-Type"] = "application/json"
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(API + "/graphql", data=body, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def search_count(endpoint, q):
    return rest(f"/search/{endpoint}?q={urllib.parse.quote(q)}&per_page=1")["total_count"]


def warn(what, err):
    print(f"[warn] could not fetch {what}: {err}", file=sys.stderr)


# ------------------------------------------------------------------ data layer
def compute_streaks(days):
    """days: ascending list of (date, count). Returns (current, longest) streaks."""
    best = (0, None, None)
    run, run_start = 0, None
    for d, c in days:
        if c > 0:
            if run == 0:
                run_start = d
            run += 1
            if run > best[0]:
                best = (run, run_start, d)
        else:
            run = 0
    cur, cs, ce = 0, None, None
    i = len(days) - 1
    if i >= 0 and days[i][1] == 0:  # today has no contribution yet: streak is still alive
        i -= 1
    while i >= 0 and days[i][1] > 0:
        cur += 1
        cs = days[i][0]
        ce = ce or days[i][0]
        i -= 1
    return (cur, cs, ce), best


def span(a, b):
    if not a or not b:
        return "No active streak"
    return f"{a:%b %d} – {b:%b %d}" if a != b else f"{a:%b %d}"


def collect():
    d = dict(stars=None, commits=None, prs=None, issues=None, repos=None,
             followers=None, contributions=None, cur=0, cur_span="–",
             longest=0, longest_span="–", langs={}, ok=False)
    if OFFLINE:
        return d

    try:
        u = rest(f"/users/{USER}")
        d["repos"], d["followers"] = u["public_repos"], u["followers"]
        d["ok"] = True
    except Exception as e:
        warn("profile", e)

    try:
        repos = [r for r in paginate(f"/users/{USER}/repos?type=owner") if not r["fork"]]
        d["stars"] = sum(r["stargazers_count"] for r in repos)
        langs = defaultdict(int)
        for r in repos:
            try:
                for k, v in rest(f"/repos/{USER}/{r['name']}/languages").items():
                    if k not in EXCLUDE:
                        langs[k] += v
            except Exception as e:
                warn(f"languages of {r['name']}", e)
        d["langs"] = dict(langs)
        d["ok"] = True
    except Exception as e:
        warn("repositories", e)

    for key, endpoint, q in (
        ("commits", "commits", f"author:{USER}"),
        ("prs", "issues", f"author:{USER} type:pr"),
        ("issues", "issues", f"author:{USER} type:issue"),
    ):
        try:
            d[key] = search_count(endpoint, q)
        except Exception as e:
            warn(key, e)

    if TOKEN:
        try:
            q = """query($login:String!){user(login:$login){contributionsCollection{
                   contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}}}}"""
            cal = graphql(q, {"login": USER})["user"]["contributionsCollection"]["contributionCalendar"]
            days = sorted((datetime.date.fromisoformat(x["date"]), x["contributionCount"])
                          for w in cal["weeks"] for x in w["contributionDays"])
            (cur, cs, ce), (lg, ls, le) = compute_streaks(days)
            d.update(contributions=cal["totalContributions"], cur=cur, cur_span=span(cs, ce),
                     longest=lg, longest_span=span(ls, le))
        except Exception as e:
            warn("contribution calendar", e)
    return d


# -------------------------------------------------------------------- SVG cards
DEFS = """<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#1a1033"/>
  </linearGradient>
  <linearGradient id="bd" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#7C3AED"/><stop offset="0.5" stop-color="#EC4899"/><stop offset="1" stop-color="#06B6D4"/>
  </linearGradient>
</defs>"""

BASE_CSS = f"""
.t{{font:700 17px {FONT};fill:#c4b5fd}}
.num{{font:700 26px {FONT}}}
.lbl{{font:500 12px {FONT};fill:#9ca3af}}
.fade{{opacity:0;animation:up .6s ease-out forwards}}
@keyframes up{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:translateY(0)}}}}
"""


def card(w, h, title, body, css=""):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">
<title>{esc(title)}</title>
{DEFS}
<style>{BASE_CSS}{css}</style>
<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="14" fill="url(#bg)" stroke="url(#bd)" stroke-width="1.5"/>
{body}
</svg>
"""


def stats_card(d):
    tiles = [
        ("Total Stars", d["stars"], "#FBBF24"), ("Commits", d["commits"], "#A78BFA"),
        ("Pull Requests", d["prs"], "#34D399"), ("Issues", d["issues"], "#F87171"),
        ("Public Repos", d["repos"], "#60A5FA"), ("Followers", d["followers"], "#F472B6"),
    ]
    parts = [f'<text x="25" y="37" class="t">{esc(NAME)}’s GitHub Stats</text>']
    for i, (label, val, color) in enumerate(tiles):
        x, y = 25 + (i % 3) * 152.5, 62 + (i // 3) * 76
        parts.append(
            f'<g class="fade" style="animation-delay:{0.15 + i * 0.12:.2f}s">'
            f'<rect x="{x}" y="{y}" width="140" height="64" rx="10" fill="#161b22" stroke="#30363d"/>'
            f'<rect x="{x}" y="{y + 14}" width="4" height="36" rx="2" fill="{color}"/>'
            f'<text x="{x + 20}" y="{y + 37}" class="num" fill="{color}">{fmt(val)}</text>'
            f'<text x="{x + 20}" y="{y + 55}" class="lbl">{esc(label)}</text></g>')
    return card(495, 224, f"{NAME}'s GitHub stats", "\n".join(parts))


def languages_card(d):
    langs = sorted(d["langs"].items(), key=lambda kv: kv[1], reverse=True)[:8]
    total = sum(v for _, v in langs)
    parts = ['<text x="25" y="37" class="t">Most Used Languages</text>']
    if not total:
        parts.append('<text x="25" y="110" class="lbl">Waiting for the first workflow run…</text>')
        return card(495, 224, "Most used languages", "\n".join(parts))

    bar, x = [], 25.0
    for name, v in langs:
        w = 445 * v / total
        bar.append(f'<rect x="{x:.2f}" y="52" width="{w + 0.5:.2f}" height="12" '
                   f'fill="{LANG_COLORS.get(name, "#8b949e")}"/>')
        x += w
    parts.append(
        '<clipPath id="round"><rect x="25" y="52" width="445" height="12" rx="6"/></clipPath>'
        '<clipPath id="reveal"><rect x="25" y="52" width="445" height="12">'
        '<animate attributeName="width" from="0" to="445" dur="1.4s" fill="freeze" calcMode="spline" '
        'keyTimes="0;1" keySplines="0.25 0.1 0.25 1"/></rect></clipPath>'
        '<g clip-path="url(#round)"><g clip-path="url(#reveal)">' + "".join(bar) + "</g></g>")
    for i, (name, v) in enumerate(langs):
        x0, y0 = 25 + (i // 4) * 222, 100 + (i % 4) * 28
        parts.append(
            f'<g class="fade" style="animation-delay:{0.3 + i * 0.1:.2f}s">'
            f'<circle cx="{x0 + 5}" cy="{y0 - 4}" r="5" fill="{LANG_COLORS.get(name, "#8b949e")}"/>'
            f'<text x="{x0 + 18}" y="{y0}" font-family="{FONT}" font-size="13" fill="#e6edf3">{esc(name)}</text>'
            f'<text x="{x0 + 195}" y="{y0}" text-anchor="end" font-family="{FONT}" font-size="12" '
            f'fill="#9ca3af">{100 * v / total:.1f}%</text></g>')
    return card(495, 224, "Most used languages", "\n".join(parts))


def streak_card(d):
    W, H = 995, 180
    cx = [W / 6, W / 2, 5 * W / 6]
    circ = 2 * 3.14159265 * 42
    frac = min(d["cur"] / d["longest"], 1) if d["longest"] else 0
    target = circ * (1 - frac)
    css = (f".big{{font:700 32px {FONT}}}.lab{{font:600 14px {FONT}}}.sub{{font:400 12px {FONT};fill:#9ca3af}}"
           f".ring{{stroke-dasharray:{circ:.2f};stroke-dashoffset:{circ:.2f};animation:draw 1.6s .4s ease-out forwards}}"
           f"@keyframes draw{{to{{stroke-dashoffset:{target:.2f}}}}}"
           "@keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}.glow{animation:pulse 2.4s ease-in-out infinite}")
    p = [
        f'<line x1="{W / 3:.1f}" y1="30" x2="{W / 3:.1f}" y2="150" stroke="#30363d"/>',
        f'<line x1="{2 * W / 3:.1f}" y1="30" x2="{2 * W / 3:.1f}" y2="150" stroke="#30363d"/>',
        '<defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F472B6"/>'
        '<stop offset="1" stop-color="#A78BFA"/></linearGradient></defs>',
        f'<g class="fade" style="animation-delay:.1s">'
        f'<text x="{cx[0]:.1f}" y="90" text-anchor="middle" class="big" fill="#22D3EE">{fmt(d["contributions"])}</text>'
        f'<text x="{cx[0]:.1f}" y="146" text-anchor="middle" class="lab" fill="#e6edf3">Total Contributions</text>'
        f'<text x="{cx[0]:.1f}" y="164" text-anchor="middle" class="sub">Last 12 months</text></g>',
        f'<circle cx="{cx[1]:.1f}" cy="78" r="42" fill="none" stroke="#30363d" stroke-width="6"/>',
        f'<circle class="ring" cx="{cx[1]:.1f}" cy="78" r="42" fill="none" stroke="url(#rg)" stroke-width="6" '
        f'stroke-linecap="round" transform="rotate(-90 {cx[1]:.1f} 78)"/>',
        f'<g class="fade" style="animation-delay:.3s">'
        f'<text x="{cx[1]:.1f}" y="89" text-anchor="middle" class="big" fill="#ffffff">{d["cur"]}</text>'
        f'<text x="{cx[1]:.1f}" y="146" text-anchor="middle" class="lab glow" fill="#F472B6">Current Streak</text>'
        f'<text x="{cx[1]:.1f}" y="164" text-anchor="middle" class="sub">{esc(d["cur_span"])}</text></g>',
        f'<g class="fade" style="animation-delay:.5s">'
        f'<text x="{cx[2]:.1f}" y="90" text-anchor="middle" class="big" fill="#A78BFA">{d["longest"]}</text>'
        f'<text x="{cx[2]:.1f}" y="146" text-anchor="middle" class="lab" fill="#e6edf3">Longest Streak</text>'
        f'<text x="{cx[2]:.1f}" y="164" text-anchor="middle" class="sub">{esc(d["longest_span"])}</text></g>',
    ]
    return card(W, H, "Contribution streak", "\n".join(p), css)


def main():
    data = collect()
    if not OFFLINE and not data["ok"]:
        print("GitHub API unreachable; keeping the existing cards.", file=sys.stderr)
        return
    os.makedirs(OUT, exist_ok=True)
    for fname, svg in (("stats-card.svg", stats_card(data)),
                       ("languages-card.svg", languages_card(data)),
                       ("streak-card.svg", streak_card(data))):
        with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
            f.write(svg)
        print("wrote", os.path.join(OUT, fname))


if __name__ == "__main__":
    main()
