#!/usr/bin/env bash
# Build the course submission PDF with LaTeX (xelatex): cover, repository link and contributions (front.tex), then
# the full README.md converted by pandoc. Commit counts come from git at build time.
#   docs/submission/build.sh          -> docs/submission/flagpilot-submission.pdf
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(git rev-parse --show-toplevel)"
REF="${SUBMIT_REF:-HEAD}"
FULL="$(git -C "$ROOT" rev-parse "$REF")"
BLOB="https://github.com/yyluck-luck/CSIT6000V-PJ/blob/$FULL"

count() {   # commits and lines added by the given author names (no merges, data files left out)
  local args=() a
  for a in "$@"; do args+=(--author="^$a <"); done
  local commits lines
  commits=$(git -C "$ROOT" log "$REF" --no-merges "${args[@]}" --format=%h | wc -l | tr -d ' ')
  lines=$(git -C "$ROOT" log "$REF" --no-merges "${args[@]}" --numstat --format= -- . ':!*.pdf' ':!*.png' ':!*.svg' ':!flagpilot/results' ':!shared.env' \
          | awk 'NF==3 && $1 != "-" {s+=$1} END {printf "%d", s}')
  printf '%s %s' "$commits" "$lines"
}
span() {    # the dates of an author's first and last commit
  local args=() a
  for a in "$@"; do args+=(--author="^$a <"); done
  git -C "$ROOT" log "$REF" --no-merges "${args[@]}" --format=%ad --date=short | sort | sed -n '1p;$p' | paste -sd' ' - \
    | awk '{print ($2 == "" || $1 == $2) ? $1 : $1 "\\newline to " $2}'
}
read -r LC LL <<< "$(count "Liu Yiyang" "yyluck")"
read -r ZC ZL <<< "$(count "777study777")"
read -r HC HL <<< "$(count "YifanHong7" "Leoasuka" "Hong Yifan")"
LS="$(span "Liu Yiyang" "yyluck")"; ZS="$(span "777study777")"; HS="$(span "YifanHong7" "Leoasuka" "Hong Yifan")"
TOTAL="$(git -C "$ROOT" log "$REF" --no-merges --format=%h | wc -l | tr -d ' ')"
fmt() { printf "%'d" "$1" 2>/dev/null || printf '%d' "$1"; }
cat > stats.tex <<TEX
\\newcommand{\\SubmitCommit}{$(git -C "$ROOT" rev-parse --short "$REF")}
\\newcommand{\\SubmitCommitFull}{$FULL}
\\newcommand{\\SubmitCommitDate}{$(git -C "$ROOT" log -1 --format=%cd --date=format:'%Y-%m-%d %H:%M' "$REF")}
\\newcommand{\\SubmitDate}{$(date '+%Y-%m-%d')}
\\newcommand{\\LiuCommits}{$LC}\\newcommand{\\LiuLines}{$(fmt "$LL")}
\\newcommand{\\ZhangCommits}{$ZC}\\newcommand{\\ZhangLines}{$(fmt "$ZL")}
\\newcommand{\\HongCommits}{$HC}\\newcommand{\\HongLines}{$(fmt "$HL")}
\\newcommand{\\LiuSpan}{$LS}\\newcommand{\\ZhangSpan}{$ZS}\\newcommand{\\HongSpan}{$HS}\\newcommand{\\TotalCommits}{$TOTAL}
TEX

# the README and its figures at that commit; links to files in the repository become links to that commit on GitHub,
# images stay local (pandoc embeds them from .ref/)
git -C "$ROOT" show "$REF:README.md" > readme-src.md
rm -rf .ref && mkdir -p .ref
git -C "$ROOT" archive "$REF" docs/figures 2>/dev/null | tar -x -C .ref 2>/dev/null || true
python3 - "$BLOB" <<'PY'
import re, sys
blob = sys.argv[1]
s = open("readme-src.md", encoding="utf-8").read()
# badges are remote SVG images: a line made only of badges becomes one line of their labels (a linked badge keeps its link)
BADGE = re.compile(r"\[!\[([^\]]*)\]\([^)\s]+\)\]\((https?://[^)\s]+)\)|!\[([^\]]*)\]\((https?://[^)\s]+)\)")
lines, start = s.split("\n"), None
def only_badges(line):
    return bool(line.strip()) and BADGE.sub("", line).strip() == ""
out, i = [], 0
while i < len(lines):
    if only_badges(lines[i]):
        j, labels = i, []
        while j < len(lines) and only_badges(lines[j]):
            for m in BADGE.finditer(lines[j]):
                labels.append(f"[{m.group(1)}]({m.group(2)})" if m.group(2) else m.group(3))
            j += 1
        out.append(" · ".join(labels))
        i = j
        continue
    out.append(lines[i])
    i += 1
s = "\n".join(out)
# relative links (not images) point at the commit on GitHub; relative images are embedded from the checkout of that commit
s = re.sub(r"(?<!!)\[([^\]]*)\]\((?!https?://|#|mailto:)([^)\s]+)\)", lambda m: f"[{m.group(1)}]({blob}/{m.group(2).lstrip('./')})", s)

# pipe tables: give each column a width that follows its content (pandoc reads the widths from the dashes of the
# separator line), so long cells wrap inside the page instead of running off it
SEP = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
lines, out, i = s.split("\n"), [], 0
def cells(line):
    t = line.strip()
    if t.startswith("|"): t = t[1:]
    if t.endswith("|") and not t.endswith("\\|"): t = t[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", t)]
in_code = False
while i < len(lines):
    line = lines[i]
    if line.lstrip().startswith("```"):
        in_code = not in_code
    if not in_code and line.lstrip().startswith("|") and i + 1 < len(lines) and SEP.match(lines[i + 1]):
        j = i + 2
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            j += 1
        rows = [cells(l) for l in [lines[i]] + lines[i + 2:j]]
        n = len(cells(lines[i + 1]))
        width = [max([len(r[k]) if k < len(r) else 0 for r in rows] + [3]) for k in range(n)]
        code = [sum(1 for r in rows[1:] if k < len(r) and r[k].startswith("`")) > len(rows[1:]) / 2 for k in range(n)]
        natural = [max(w, 6) * (1.45 if c else 1.0) for w, c in zip(width, code)]     # monospace cells are wider
        if sum(natural) <= 100:                       # it fits: every column gets what it needs
            dashes = [max(3, round(100 * x / sum(natural))) for x in natural]
        else:                                         # short columns keep their width; long ones share the rest
            short = [x if x <= 26 else 0 for x in natural]
            left = max(100 - sum(short), 30)
            longw = [x ** 0.8 if x > 26 else 0 for x in natural]
            dashes = [max(3, round(short[k] + 2 if short[k] else left * longw[k] / sum(longw))) for k in range(n)]
        seps = cells(lines[i + 1])
        new = []
        for k in range(n):
            sp = seps[k] if k < len(seps) else "---"
            new.append((":" if sp.startswith(":") else "") + "-" * dashes[k] + (":" if sp.endswith(":") and len(sp) > 1 else ""))
        out += [lines[i], "|" + "|".join(new) + "|"] + lines[i + 2:j]
        i = j
        continue
    out.append(line)
    i += 1
open("readme-src.md", "w", encoding="utf-8").write("\n".join(out))
PY

pandoc readme-src.md -f markdown+gfm_auto_identifiers+autolink_bare_uris+lists_without_preceding_blankline-yaml_metadata_block-smart-implicit_figures \
  --resource-path=.ref --columns=70 -t latex -s -o flagpilot-submission.tex \
  -H header.tex -B front.tex \
  -V documentclass=article -V fontsize=10pt -V geometry:margin=2cm -V block-headings \
  -V mainfont="Helvetica Neue" -V monofont="Menlo" -V CJKmainfont="PingFang SC" \
  -V colorlinks=true -V linkcolor=blue!60!black -V urlcolor=blue!60!black \
  -M title="" --syntax-highlighting=tango
latexmk -xelatex -interaction=nonstopmode -halt-on-error -quiet flagpilot-submission.tex > build.log 2>&1 || { tail -40 build.log; exit 1; }
latexmk -c -quiet flagpilot-submission.tex >/dev/null 2>&1 || true
echo "built: $(pwd)/flagpilot-submission.pdf ($(pdfinfo flagpilot-submission.pdf 2>/dev/null | awk '/^Pages/ {print $2}') pages)"
