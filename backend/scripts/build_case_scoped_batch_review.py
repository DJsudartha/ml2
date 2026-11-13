"""Create a new case-scoped view of a preserved candidate-only review package.

Selection is not approval. Frozen proposals, manifests and evidence stay intact;
the generated UI exports only the explicitly requested case identities.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


class ScopeError(ValueError):
    """The supplied package or requested scope is ambiguous or unsupported."""


def require(condition, message):
    if not condition:
        raise ScopeError(message)


_TOKEN = re.compile(
    r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"'
    r"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
    r"|true|false|null|[{}\[\]:,]"
)


class JsonSpans:
    """Validate JSON and locate fields without decoding game/batch payloads."""

    def __init__(self, text):
        self.text = text

    def ws(self, pos):
        while pos < len(self.text) and self.text[pos] in " \r\n\t":
            pos += 1
        return pos

    def value(self, pos):
        pos = self.ws(pos)
        match = _TOKEN.match(self.text, pos)
        require(match is not None, "invalid JSON token")
        token = match.group()
        if token in ("{", "["):
            return self.container(pos)[0]
        require(token not in ("}", "]", ":", ","), "JSON value required")
        return match.end()

    def container(self, pos):
        start = self.ws(pos)
        require(start < len(self.text) and self.text[start] in "{[", "container required")
        object_mode = self.text[start] == "{"
        close = "}" if object_mode else "]"
        entries = {} if object_mode else []
        pos = self.ws(start + 1)
        if pos < len(self.text) and self.text[pos] == close:
            return pos + 1, entries
        while True:
            if object_mode:
                match = _TOKEN.match(self.text, pos)
                require(match is not None and match.group().startswith('"'), "key required")
                key = json.loads(match.group())
                require(key not in entries, "duplicate JSON key")
                pos = self.ws(match.end())
                require(pos < len(self.text) and self.text[pos] == ":", "colon required")
                pos = self.ws(pos + 1)
            end = self.value(pos)
            if object_mode:
                entries[key] = (pos, end)
            else:
                entries.append((pos, end))
            pos = self.ws(end)
            require(pos < len(self.text), "truncated JSON")
            if self.text[pos] == close:
                return pos + 1, entries
            require(self.text[pos] == ",", "comma required")
            pos = self.ws(pos + 1)

    def object(self, span):
        require(self.text[span[0]] == "{", "object required")
        return self.container(span[0])[1]

    def array(self, span):
        require(self.text[span[0]] == "[", "array required")
        return self.container(span[0])[1]

    def scalar(self, span):
        require(self.text[span[0]] not in "{[", "identity must be scalar")
        return json.loads(self.text[span[0]:span[1]])

    def document(self):
        start = self.ws(0)
        end = self.value(start)
        require(self.ws(end) == len(self.text), "trailing JSON content")
        return self.object((start, end))


def identities(scanner, fields):
    require("games" in fields, "missing games")
    result = []
    for span in scanner.array(fields["games"]):
        game = scanner.object(span)
        require({"game_id", "match_id", "game_no"} <= game.keys(), "missing case identity")
        identity = tuple(scanner.scalar(game[key]) for key in ("game_id", "match_id", "game_no"))
        require(all(isinstance(v, str) and v.strip() for v in identity[:2])
                and type(identity[2]) is int and identity[2] > 0, "invalid case identity")
        result.append(identity)
    require(len({item[0] for item in result}) == len(result), "duplicate source game ID")
    require(len({item[1:] for item in result}) == len(result), "ambiguous source identity")
    return result


def tokens(text):
    # Inputs have already passed the strict parser. Compare lexical content while
    # allowing formatting/HTML-safe Unicode escaping used by the original builder.
    return [m.group().replace("<", "\\u003c").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029") for m in _TOKEN.finditer(text)]


SCOPE_JS = r"""
// Explicit selection controls review scope; it does not approve any labels.
const requestedCases=__SCOPE__;
function assertCaseScope(cases){
 if(!Array.isArray(cases)||cases.length!==requestedCases.length)throw Error('Exact requested cases required');
 const seen=new Set();
 for(const c of cases){const expected=requestedCases.find(x=>x[0]===c.game_id);
  if(!expected||seen.has(c.game_id)||c.match_id!==expected[1]||c.game_no!==expected[2]||c.kind!=='candidate'||c.independently_verified!==false)throw Error('Unexpected, duplicate or mismatched case identity');
  seen.add(c.game_id);
 }
}
const scopedCandidateGames=requestedCases.map(identity=>{
 const found=pkg.manifest.games.filter(g=>g.game_id===identity[0]);
 if(found.length!==1||found[0].match_id!==identity[1]||found[0].game_no!==identity[2])throw Error('Missing or ambiguous requested identity');
 return found[0];
});
if(pkg.holdouts.games.length)throw Error('Candidate-only package required');
"""


def replace_once(text, old, new):
    require(text.count(old) == 1, f"unsupported UI marker: {old[:65]}")
    return text.replace(old, new, 1)


def scope_html(html, requested, package_fields):
    marker = "const pkg="
    require(html.count(marker) == 1, "unsupported embedded package marker")
    start = html.index(marker) + len(marker)
    embedded = JsonSpans(html)
    end = embedded.value(start)
    require(html[end:end + 1] == ";", "unsupported embedded package terminator")
    fields = embedded.object((start, end))
    require({"manifest", "manifest_sha256", "holdouts", "holdout_sha256"} <= fields.keys(),
            "missing embedded package fields")
    for key, (raw, digest) in package_fields.items():
        span = fields[key]
        require(tokens(html[span[0]:span[1]]) == tokens(raw), "embedded manifest differs")
        hash_key = "manifest_sha256" if key == "manifest" else "holdout_sha256"
        require(embedded.scalar(fields[hash_key]) == digest, "manifest hash mismatch")
    # The source package was checked without decoding any game or holdout payload.
    injection = SCOPE_JS.replace("__SCOPE__", json.dumps(requested).replace("<", "\\u003c"))
    html = html[:end + 1] + injection + html[end + 1:]
    scope_digest = hashlib.sha256(json.dumps(requested).encode()).hexdigest()
    html = replace_once(html, "const key='ml2-batch-review-'+pkg.manifest_sha256;",
                        "const key='ml2-batch-review-'+pkg.manifest_sha256+'-scope-"
                        + scope_digest + "';")
    html = replace_once(html,
                        "function games(){return mode==='candidate'?pkg.manifest.games:pkg.holdouts.games;}",
                        "function games(){return scopedCandidateGames;}")
    html = replace_once(html,
                        "for(const kind of ['candidate','holdout']){for(const g of kind==='candidate'?pkg.manifest.games:pkg.holdouts.games)",
                        "for(const kind of ['candidate']){for(const g of scopedCandidateGames)")
    html = replace_once(html, "return {issues,payload:",
                        "assertCaseScope(cases); return {issues,payload:")
    html = replace_once(html, "const next={};for(const c of p.cases){",
                        "assertCaseScope(p.cases);const next={};for(const c of p.cases){")
    html = replace_once(html,
                        "const g=[...pkg.manifest.games,...pkg.holdouts.games].find(x=>x.game_id===c.game_id);",
                        "const g=scopedCandidateGames.find(x=>x.game_id===c.game_id);")
    html = replace_once(html, "el('holdoutTab').onclick=()=>switchMode('holdout');",
                        "el('holdoutTab').hidden=true;el('holdoutTab').onclick=null;")
    html = replace_once(html, "el('reviewer').value=state.reviewer;switchMode('candidate');",
                        "el('candidateTab').textContent=requestedCases.length+' selected candidate games';"
                        "el('reviewer').value=state.reviewer;switchMode('candidate');")
    return html


def build(source, output, requested):
    source, output = Path(source).resolve(), Path(output).resolve()
    require(bool(requested), "at least one explicit case identity required")
    require(all(isinstance(c, (tuple, list)) and len(c) == 3
                and all(isinstance(v, str) and v.strip() for v in c[:2])
                and type(c[2]) is int and c[2] > 0 for c in requested), "invalid requested identity")
    requested = [tuple(case) for case in requested]
    require(len({c[0] for c in requested}) == len(requested), "duplicate requested game ID")
    require(len({c[1:] for c in requested}) == len(requested), "ambiguous requested identity")
    require(not output.exists(), "refuse existing output")
    require(not output.is_relative_to(source) and not source.is_relative_to(output),
            "source and output must be separate directories")
    names = {"manifest": "candidates/original_candidates.json", "holdouts": "holdout/blank_cases.json"}
    manifests = {}
    for key, name in names.items():
        path = source / name
        require(path.is_file() and not path.is_symlink(), "missing or linked manifest")
        raw = path.read_bytes()
        manifests[key] = (raw.decode("utf-8"), hashlib.sha256(raw).hexdigest())
    scanner = JsonSpans(manifests["manifest"][0])
    candidate_fields = scanner.document()
    available = identities(scanner, candidate_fields)
    holdouts = JsonSpans(manifests["holdouts"][0])
    held_fields = holdouts.document()
    require("games" in held_fields and not holdouts.array(held_fields["games"]),
            "nonempty holdouts forbidden; payloads were not decoded")
    require(all(case in available for case in requested), "missing or mismatched requested case")
    index = source / "index.html"
    assets = source / "assets"
    require(index.is_file() and not index.is_symlink(), "missing or linked UI")
    require(assets.is_dir() and not assets.is_symlink(), "missing or linked assets")
    require(not any(p.is_symlink() for p in assets.rglob("*")), "linked evidence forbidden")
    html = scope_html(index.read_text(encoding="utf-8"), requested, manifests)
    # Nothing is created until every identity and supported UI marker passes.
    output.mkdir(parents=True, exist_ok=False)
    try:
        for name in names.values():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        shutil.copytree(assets, output / "assets")
        (output / "index.html").write_text(html, encoding="utf-8")
        (output / "README.md").write_text(
            "# Scoped candidate review\n\nOpen index.html. Only these cases are navigable/exported:\n\n"
            + "".join(f"- {gid} / {mid} / game {number}\n" for gid, mid, number in requested)
            + "\nSelection is not approval. Frozen manifests and evidence are unchanged. "
            "This is proposal-assisted development review, not blind evaluation. "
            "Original human reviews and verified labels were not copied. "
            "Exports keep independently_verified:false. Partial/uncertain reviews "
            "cannot become complete-game labels. External human finalization, exact "
            "export hash and source attestation remain separate requirements.\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(output)
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", nargs=3, action="append", metavar=("GAME_ID", "MATCH_ID", "GAME_NO"),
                        required=True)
    args = parser.parse_args()
    try:
        cases = [(gid, mid, int(number)) for gid, mid, number in args.case]
        print(build(args.package, args.output, cases))
    except (ScopeError, OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
