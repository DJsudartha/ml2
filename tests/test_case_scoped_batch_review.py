"""Synthetic execution of the generated UI; never open real review payloads."""

from copy import deepcopy
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from backend.scripts.build_case_scoped_batch_review import ScopeError, build


# Original candidate-only UI code, with its embedded package removed. The fixture
# below supplies invented games and evidence. No external files are needed in CI.
TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ML² batch verification</title>
<style>body{font:16px system-ui;margin:0;background:#f3f5f7;color:#152131}header{background:#12283f;color:white;padding:20px}main{max-width:1250px;margin:auto;padding:18px}button,input,select,textarea{font:inherit;padding:8px;margin:4px}button{cursor:pointer}textarea{width:95%;min-height:65px}article,.notice{background:white;border:1px solid #ccd4dd;border-radius:8px;padding:16px;margin:16px 0}h2{margin-top:0}.warning{color:#7b3900;background:#fff0d5;padding:12px}.observations{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px}figure{margin:6px 0;border:1px solid #ccc;padding:8px}img{width:100%;height:auto}table{font-size:13px;border-collapse:collapse;width:100%}td,th{padding:4px;border:1px solid #ddd;text-align:left}.muted{color:#516275}label{display:inline-block}#saveStatus{font-size:14px}summary{cursor:pointer}.roster{line-height:1.8}a{color:#075e9d}.unreviewed{border-left:6px solid #aaa}.reviewed{border-left:6px solid #247367}</style></head>
<body><header><h1>ML² batch verification</h1><div>Human judgment required · no automatic verification or training promotion</div></header>
<main><div class="notice"><label>Reviewer identity <input id="reviewer" placeholder="Name or stable reviewer ID"></label><button id="export">Export draft human review</button><label>Import draft <input id="import" type="file" accept="application/json"></label><div id="saveStatus"></div>
<p>Inspect source VOD footage around each change. Repeated artwork alone does not prove commitment. Use Uncertain when the selected status or batch cannot be established. Double-pick internal order is irrelevant; R3/R4 swaps are incorrect.</p></div>
<nav><button id="candidateTab">8 candidate games</button><button id="holdoutTab" hidden>No holdouts in this package</button><select id="game"></select></nav><div id="content"></div></main>
<script>
'use strict';
const pkg=/*PACKAGE_DATA*/null;
const key='ml2-batch-review-'+pkg.manifest_sha256;
const state={reviewer:'',responses:{}};
let mode='candidate';
const el=id=>document.getElementById(id);
const text=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;return n;};
function games(){return mode==='candidate'?pkg.manifest.games:pkg.holdouts.games;}
function current(){return games()[Number(el('game').value)||0];}
function response(g,b){const k=g.game_id+'::'+b.batch;return state.responses[k]||(state.responses[k]={decision:'unreviewed',corrected_heroes:[],source_inspected:false,source_timestamp_note:'',comment:'',reviewed_at:null});}
function save(){try{localStorage.setItem(key,JSON.stringify(state));el('saveStatus').textContent='Progress saved in this browser. Export a draft JSON for a durable copy.';}catch{el('saveStatus').textContent='Browser storage unavailable. Export a draft JSON to save progress.';}}
function stamp(r){r.reviewed_at=new Date().toISOString();save();}
function vod(g,t){const a=text('a','Open original VOD at '+t+'s');a.href='https://www.youtube.com/watch?v='+encodeURIComponent(g.video_id)+'&t='+Math.max(0,Math.floor(t));a.target='_blank';a.rel='noopener noreferrer';return a;}
function observation(g,o,label){const fig=document.createElement('figure');fig.append(text('strong',label+' · '+o.timestamp_sec+'s'),document.createElement('br'),vod(g,o.timestamp_sec));const im=document.createElement('img');im.src=o.frame;im.alt=label+' roster at '+o.timestamp_sec+' seconds';im.loading='lazy';fig.append(im,text('div','Blue: '+(o.cards.blue.join(', ')||'(empty)')+' | Red: '+(o.cards.red.join(', ')||'(empty)')));
 const details=document.createElement('details');details.append(text('summary','OCR confidence, name margins and ambiguities'));const table=document.createElement('table');const tr=document.createElement('tr');['Observed text','Matched hero / team','OCR','Name similarity','Margin'].forEach(x=>tr.append(text('th',x)));table.append(tr);(o.name_evidence||[]).forEach(e=>{const row=document.createElement('tr');[e.text,(e.hero||'?')+' / '+(e.team||'?'),e.ocr_score,e.name_similarity,e.name_margin].forEach(v=>row.append(text('td',typeof v==='number'?v.toFixed(4):String(v))));table.append(row);});details.append(table,text('p','Duplicate regions: '+JSON.stringify(o.duplicate_hero_regions||[])+' · Overlay score: '+String(o.roster_anchor_score)));fig.append(details);return fig;}
function render(){const g=current();const c=el('content');c.replaceChildren();c.append(text('h2',g.match_id+' · Game '+g.game_no),text('p',g.teams.blue+' (blue) vs '+g.teams.red+' (red) · '+g.date),vod(g,0));
 c.append(text('p','Liquipedia blue heroes: '+g.known_heroes.blue.join(', '),'roster'),text('p','Liquipedia red heroes: '+g.known_heroes.red.join(', '),'roster'));
 c.append(text('p',mode==='candidate'?'Proposals are visible: this is assisted verification. Recognition ambiguous observations: '+g.recognition_ambiguous_observations+'. None of these games is independently verified.':g.source_metadata_status+' '+g.prior_exposure,'warning'));
 g.batches.forEach(b=>{const r=response(g,b);const a=document.createElement('article');a.className=r.decision==='unreviewed'?'unreviewed':'reviewed';a.append(text('h2',b.batch+' — '+(b.heroes?b.heroes.join(' + '):'No proposed assignment')));
 if(mode==='candidate'){a.append(text('p',b.unresolved_commitment||'Selected-roster status requires source inspection.','warning'),text('p','Set elimination dependencies: '+JSON.stringify(b.set_elimination_inferences||[])));
 const grid=document.createElement('div');grid.className='observations';[['preceding_phase_evidence','Before'],['evidence','After / repeated phase'],['roster_display_evidence','Later carryover / reordering']].forEach(([k,label])=>(b[k]||[]).forEach(o=>grid.append(observation(g,o,label))));a.append(grid);}
 const select=document.createElement('select');const options=mode==='candidate'?[['unreviewed','Unreviewed'],['confirm','Confirm proposal'],['correct','Correct proposal'],['uncertain','Uncertain / unverifiable']]:[['unreviewed','Unreviewed'],['label','Record independent source label'],['uncertain','Uncertain / unverifiable']];options.forEach(([v,t])=>{const o=text('option',t);o.value=v;select.append(o);});select.value=r.decision;
 const fields=document.createElement('div');function choices(){fields.replaceChildren();if(!['correct','label'].includes(r.decision))return;fields.append(text('span','Observed heroes: '));const team=b.batch.startsWith('B')?'blue':'red';const count=b.batch.includes(',')?2:1;for(let j=0;j<count;j++){const s=document.createElement('select');const blank=text('option','Choose hero');blank.value='';s.append(blank);g.known_heroes[team].forEach(h=>{const o=text('option',h);o.value=h;s.append(o);});s.value=r.corrected_heroes[j]||'';s.onchange=()=>{r.corrected_heroes[j]=s.value;stamp(r);};fields.append(s);}}
 select.onchange=()=>{r.decision=select.value;stamp(r);choices();a.className=r.decision==='unreviewed'?'unreviewed':'reviewed';};a.append(select,fields);choices();
 const label=document.createElement('label');const checked=document.createElement('input');checked.type='checkbox';checked.checked=r.source_inspected;checked.onchange=()=>{r.source_inspected=checked.checked;stamp(r);};label.append(checked,document.createTextNode('I inspected the original VOD footage for this batch'));a.append(label);
 const source=document.createElement('input');source.style.width='90%';source.placeholder='Source timestamp/range and evidence for selected-roster status';source.value=r.source_timestamp_note;source.oninput=()=>{r.source_timestamp_note=source.value;stamp(r);};a.append(source);
 const comment=document.createElement('textarea');comment.placeholder='Corrections, uncertainty, recognition/presentation issues, or mistake cause';comment.value=r.comment;comment.oninput=()=>{r.comment=comment.value;stamp(r);};a.append(comment);c.append(a);});}
function switchMode(m){mode=m;el('game').replaceChildren();games().forEach((g,i)=>{const o=text('option',g.match_id+' G'+g.game_no);o.value=i;el('game').append(o);});render();}
function draft(){const issues=[];const cases=[];for(const kind of ['candidate','holdout']){for(const g of kind==='candidate'?pkg.manifest.games:pkg.holdouts.games){const seen=new Set();const batches=g.batches.map(b=>{const r=state.responses[g.game_id+'::'+b.batch]||{decision:'unreviewed',corrected_heroes:[],source_inspected:false,source_timestamp_note:'',comment:'',reviewed_at:null};const assigned=r.decision==='confirm'?b.heroes:['correct','label'].includes(r.decision)?r.corrected_heroes:[];if(['confirm','correct','label'].includes(r.decision)){const n=b.batch.includes(',')?2:1;const team=b.batch.startsWith('B')?'blue':'red';if(!r.source_inspected||!r.source_timestamp_note.trim())issues.push(g.match_id+' G'+g.game_no+' '+b.batch+': source inspection and evidence note required');if(assigned.length!==n||new Set(assigned).size!==n||assigned.some(h=>!g.known_heroes[team].includes(h)))issues.push(g.match_id+' '+b.batch+': choose the required distinct team heroes');assigned.forEach(h=>{if(seen.has(h))issues.push(g.match_id+': duplicate hero '+h);seen.add(h);});}if(r.decision==='uncertain'&&!r.comment.trim())issues.push(g.match_id+' '+b.batch+': explain uncertainty');return {batch:b.batch,original_proposal:b.heroes,...r};});cases.push({game_id:g.game_id,match_id:g.match_id,game_no:g.game_no,kind,independently_verified:false,batches});}}
 return {issues,payload:{schema_version:1,kind:'draft_human_batch_review',reviewer_id:state.reviewer.trim(),exported_at:new Date().toISOString(),candidate_manifest_sha256:pkg.manifest_sha256,candidate_source_sha256:pkg.manifest.candidate_source_sha256,holdout_manifest_sha256:pkg.holdout_sha256,independently_verified:false,cases}};}
el('export').onclick=()=>{if(!state.reviewer.trim()){alert('Enter a reviewer identity before exporting.');return;}const d=draft();if(d.issues.length){alert(d.issues.join('\n'));return;}const blob=new Blob([JSON.stringify(d.payload,null,2)+'\n'],{type:'application/json'});const a=document.createElement('a');const u=URL.createObjectURL(blob);a.href=u;a.download='ml2-draft-human-review-'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);};
el('import').onchange=async e=>{try{const p=JSON.parse(await e.target.files[0].text());if(p.kind!=='draft_human_batch_review'||p.candidate_manifest_sha256!==pkg.manifest_sha256||p.holdout_manifest_sha256!==pkg.holdout_sha256)throw Error('Review provenance does not match this package');const next={};for(const c of p.cases){const g=[...pkg.manifest.games,...pkg.holdouts.games].find(x=>x.game_id===c.game_id);if(!g)throw Error('Unknown game');for(const b of c.batches){const original=g.batches.find(x=>x.batch===b.batch);if(!original||JSON.stringify(b.original_proposal)!==JSON.stringify(original.heroes))throw Error('Original proposal differs');if(!['unreviewed','confirm','correct','label','uncertain'].includes(b.decision))throw Error('Unknown decision');next[g.game_id+'::'+b.batch]={decision:b.decision,corrected_heroes:b.corrected_heroes||[],source_inspected:b.source_inspected===true,source_timestamp_note:b.source_timestamp_note||'',comment:b.comment||'',reviewed_at:b.reviewed_at||null};}}state.responses=next;state.reviewer=p.reviewer_id||'';el('reviewer').value=state.reviewer;save();render();}catch(err){alert('Import failed: '+err.message);}};
el('reviewer').oninput=e=>{state.reviewer=e.target.value;save();};el('game').onchange=render;el('candidateTab').onclick=()=>switchMode('candidate');el('holdoutTab').onclick=()=>switchMode('holdout');
try{const s=JSON.parse(localStorage.getItem(key));if(s&&typeof s.responses==='object'){state.reviewer=s.reviewer||'';state.responses=s.responses;}}catch{/* Local-file storage may be unavailable. */}el('reviewer').value=state.reviewer;switchMode('candidate');
</script></body></html>
"""

BATCHES = ("B1", "R1,R2", "B2,B3", "R3", "R4", "B4,B5", "R5")
SIZES = (1, 2, 2, 1, 1, 2, 1)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_package(tmp_path, mutate=None):
    source = tmp_path / "source"
    source.mkdir()
    games = []
    for i in range(8):
        blue = [f"Blue{i}-{n}" for n in range(5)]
        red = [f"Red{i}-{n}" for n in range(5)]
        pools = {"B": iter(blue), "R": iter(red)}
        evidence = {"timestamp_sec": 1, "frame": "assets/synthetic.jpg",
                    "cards": {"blue": [], "red": []}, "name_evidence": []}
        games.append({
            "game_id": f"synthetic-{i}", "match_id": f"match-{i}", "game_no": i + 1,
            "date": "2026-01-01", "teams": {"blue": "Blue", "red": "Red"},
            "known_heroes": {"blue": blue, "red": red}, "video_id": "synthetic",
            "recognition_ambiguous_observations": 0, "independent_verification": False,
            "batches": [{"batch": batch, "heroes": [next(pools[batch[0]]) for _ in range(size)],
                         "evidence": [deepcopy(evidence)], "preceding_phase_evidence": [],
                         "roster_display_evidence": [deepcopy(evidence)]}
                        for batch, size in zip(BATCHES, SIZES, strict=True)],
        })
    manifest = {"schema_version": 1, "kind": "original_candidates_for_review",
                "candidate_source_sha256": "b" * 64, "games": games, "assets": {}}
    holdouts = {"schema_version": 1, "kind": "prediction_free_holdout_cases", "games": []}
    if mutate:
        mutate(manifest, holdouts)
    embedded = {"manifest": manifest, "holdouts": holdouts}
    for name, value, key in (
        ("candidates/original_candidates.json", manifest, "manifest_sha256"),
        ("holdout/blank_cases.json", holdouts, "holdout_sha256"),
    ):
        path = source / name
        path.parent.mkdir()
        raw = (json.dumps(value, indent=2) + "\n").encode()
        path.write_bytes(raw)
        embedded[key] = sha(raw)
    (source / "assets").mkdir()
    (source / "assets/synthetic.jpg").write_bytes(b"invented-evidence")
    for name in ("human_reviews", "verified_labels"):
        (source / name).mkdir()
        (source / name / "do-not-copy.txt").write_text("not a review")
    payload = json.dumps(embedded, ensure_ascii=False).replace("<", "\\u003c")
    (source / "index.html").write_text(TEMPLATE.replace("/*PACKAGE_DATA*/null", payload),
                                       encoding="utf-8")
    return source, embedded


def scope(*numbers):
    return [(f"synthetic-{i}", f"match-{i}", i + 1) for i in numbers]


def snapshots(source):
    return {p.relative_to(source).as_posix(): p.read_bytes()
            for p in source.rglob("*") if p.is_file()}


NODE_HARNESS = r"""
const fs=require('fs'),vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const nodes=new Map(),storage=new Map(),alerts=[],downloads=[];
class Element{
 constructor(){this.value='';this.children=[];this.style={};this.files=[];}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 click(){downloads.push(this.download);}
}
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},
 createElement(){return new Element();},createTextNode(value){return value;}};
let blob;
class FixedDate extends Date{constructor(...args){super(...(args.length?args:['2026-10-30T00:00:00Z']));}}
const context=vm.createContext({document,console,Date:FixedDate,alert:x=>alerts.push(x),
 localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
 Blob:class{constructor(parts){blob=parts.join('');}},
 URL:{createObjectURL(){return 'synthetic-url';},revokeObjectURL(){}},setTimeout(){}});
const script=input.html.split('<script>')[1].split('</script>')[0];
vm.runInContext(script,context);
(async()=>{
 vm.runInContext("state.reviewer='synthetic-human';",context);
 if(input.responses)vm.runInContext('state.responses='+JSON.stringify(input.responses),context);
 if(input.import){await nodes.get('import').onchange({target:{files:[{text:async()=>JSON.stringify(input.import)}]}});}
 nodes.get('export').onclick();
 const result=vm.runInContext('draft()',context);
 const ids=vm.runInContext('games().map(g=>g.game_id)',context);
 const key=vm.runInContext('key',context);
 console.log(JSON.stringify({result,ids,key,alerts,downloads,exported:blob?JSON.parse(blob):null}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""


def execute(html, **inputs):
    if not shutil.which("node"):
        pytest.fail("Node.js is required to execute the review UI regression")
    result = subprocess.run(["node", "-e", NODE_HARNESS],
                            input=json.dumps({"html": html, **inputs}), text=True,
                            capture_output=True, check=True)
    return json.loads(result.stdout)


def answers(pkg, numbers=(0, 7)):
    result = {}
    for i in numbers:
        game = pkg["manifest"]["games"][i]
        for batch in game["batches"]:
            result[game["game_id"] + "::" + batch["batch"]] = {
                "decision": "confirm", "corrected_heroes": [], "source_inspected": True,
                "source_timestamp_note": "Synthetic source 00:01", "comment": "synthetic note",
                "reviewed_at": "2026-10-30T00:00:00Z",
            }
    return result


def test_original_bug_is_reproduced_without_real_data(tmp_path):
    source, _ = synthetic_package(tmp_path)
    result = execute((source / "index.html").read_text(encoding="utf-8"))
    assert len(result["result"]["payload"]["cases"]) == 8
    assert len(scope(0, 7)) == 2  # The original export ignores this intended scope.


@pytest.mark.parametrize("numbers", [(0,), (0, 7)])
def test_generated_ui_scopes_actual_export_and_navigation(tmp_path, numbers):
    source, pkg = synthetic_package(tmp_path)
    before = snapshots(source)
    output = build(source, tmp_path / "scoped", scope(*numbers))
    html = (output / "index.html").read_text(encoding="utf-8")
    responses = answers(pkg, numbers)
    result = execute(html, responses=responses)
    assert result["ids"] == [case[0] for case in scope(*numbers)]
    payload = result["exported"]
    assert payload == result["result"]["payload"]
    assert len(result["downloads"]) == 1
    assert len(payload["cases"]) == len(numbers)
    assert payload["schema_version"] == 1
    assert payload["kind"] == "draft_human_batch_review"
    assert payload["independently_verified"] is False
    for field in ("candidate_manifest_sha256", "holdout_manifest_sha256", "candidate_source_sha256"):
        expected = {"candidate_manifest_sha256": pkg["manifest_sha256"],
                    "holdout_manifest_sha256": pkg["holdout_sha256"],
                    "candidate_source_sha256": "b" * 64}[field]
        assert payload[field] == expected
    for i, case in zip(numbers, payload["cases"], strict=True):
        assert (case["game_id"], case["match_id"], case["game_no"]) == scope(i)[0]
        assert case["kind"] == "candidate" and case["independently_verified"] is False
        assert len(case["batches"]) == 7
        for proposal, batch in zip(pkg["manifest"]["games"][i]["batches"], case["batches"], strict=True):
            assert batch == {"batch": proposal["batch"], "original_proposal": proposal["heroes"],
                             **responses[case["game_id"] + "::" + batch["batch"]]}
    assert snapshots(source) == before
    for path in ("candidates/original_candidates.json", "holdout/blank_cases.json", "assets/synthetic.jpg"):
        assert (output / path).read_bytes() == (source / path).read_bytes()
    assert set(snapshots(output)) == {"index.html", "README.md", "candidates/original_candidates.json",
                                     "holdout/blank_cases.json", "assets/synthetic.jpg"}
    assert "-scope-" in result["key"]
    original = execute((source / "index.html").read_text(encoding="utf-8"))
    assert result["key"] != original["key"]
    round_trip = execute(html, **{"import": payload})
    assert round_trip["exported"]["cases"] == payload["cases"]
    assert not round_trip["alerts"]


def test_preserves_corrections_uncertainty_unreviewed_and_reviewer(tmp_path):
    source, pkg = synthetic_package(tmp_path)
    output = build(source, tmp_path / "scoped", scope(0, 7))
    html = (output / "index.html").read_text(encoding="utf-8")
    responses = answers(pkg)
    responses["synthetic-0::B1"].update(decision="correct", corrected_heroes=["Blue0-0"])
    responses["synthetic-0::R3"].update(decision="uncertain", comment="Synthetic uncertainty")
    responses["synthetic-7::R5"].update(decision="unreviewed")
    original = execute(html, responses=responses)["exported"]
    original["reviewer_id"] = "original-synthetic-reviewer"
    result = execute(html, **{"import": original})
    assert not result["alerts"]
    assert result["exported"] == original


@pytest.mark.parametrize("requested", [[], scope(0, 0), [("unknown", "match-0", 1)],
                                      [("synthetic-0", "wrong", 1)],
                                      [("synthetic-0", "match-0", True)],
                                      [("synthetic-0", "match-0", 2)],
                                      [("", "match-0", 1)],
                                      [("synthetic-0", "match-0", 1), ("other", "match-0", 1)]])
def test_invalid_requested_identity_creates_nothing(tmp_path, requested):
    source, _ = synthetic_package(tmp_path)
    with pytest.raises(ScopeError):
        build(source, tmp_path / "scoped", requested)
    assert not (tmp_path / "scoped").exists()


@pytest.mark.parametrize("mutation", ["duplicate", "ambiguous", "missing", "holdout"])
def test_source_identity_violations_create_nothing(tmp_path, mutation):
    def mutate(manifest, holdouts):
        if mutation == "duplicate":
            manifest["games"].append(deepcopy(manifest["games"][0]))
        elif mutation == "ambiguous":
            manifest["games"][1].update(match_id="match-0", game_no=1)
        elif mutation == "missing":
            del manifest["games"][0]["game_id"]
        else:
            holdouts["games"] = [{"batches": [{"protected": "DO_NOT_DECODE"}]}]
    source, _ = synthetic_package(tmp_path, mutate)
    with pytest.raises(ScopeError):
        build(source, tmp_path / "scoped", scope(0))
    assert not (tmp_path / "scoped").exists()


def test_no_batch_payload_decoded_during_build(tmp_path, monkeypatch):
    source, _ = synthetic_package(tmp_path)
    loads = json.loads

    def guarded(value, *args, **kwargs):
        assert "Blue0-0" not in value and "DO_NOT_DECODE" not in value
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", guarded)
    build(source, tmp_path / "scoped", scope(0, 7))


@pytest.mark.parametrize("mutation", ["extra", "missing", "duplicate", "identity", "empty"])
def test_import_rejects_any_other_case_scope(tmp_path, mutation):
    source, pkg = synthetic_package(tmp_path)
    output = build(source, tmp_path / "scoped", scope(0, 7))
    html = (output / "index.html").read_text(encoding="utf-8")
    original = execute(html, responses=answers(pkg))["exported"]
    imported = deepcopy(original)
    if mutation == "extra":
        extra = deepcopy(imported["cases"][0])
        extra.update(game_id="synthetic-1", match_id="match-1", game_no=2)
        imported["cases"].append(extra)
    elif mutation == "missing":
        imported["cases"].pop()
    elif mutation == "duplicate":
        imported["cases"][1] = deepcopy(imported["cases"][0])
    elif mutation == "identity":
        imported["cases"][0]["game_no"] = 123
    else:
        imported["cases"] = []
    result = execute(html, responses=answers(pkg), **{"import": imported})
    assert len(result["alerts"]) == 1 and result["alerts"][0].startswith("Import failed:")
    assert result["exported"]["cases"] == original["cases"]
    assert result["exported"]["reviewer_id"] == original["reviewer_id"]


@pytest.mark.parametrize("mutation", ["marker", "embedded", "hash", "trailing", "duplicate_key"])
def test_unsupported_or_changed_package_fails_before_output(tmp_path, mutation):
    source, _ = synthetic_package(tmp_path)
    index = source / "index.html"
    html = index.read_text(encoding="utf-8")
    if mutation == "marker":
        index.write_text(html.replace("const next={};", "const next = {};"), encoding="utf-8")
    elif mutation == "embedded":
        index.write_text(html.replace('"Blue0-0"', '"CHANGED"'), encoding="utf-8")
    elif mutation == "hash":
        index.write_text(html.replace('"holdout_sha256": "', '"holdout_sha256": "f'), encoding="utf-8")
    else:
        path = source / "candidates/original_candidates.json"
        raw = path.read_text()
        path.write_text(raw + " {}" if mutation == "trailing" else '{"games": [],' + raw[1:])
    with pytest.raises(ScopeError):
        build(source, tmp_path / "scoped", scope(0, 7))
    assert not (tmp_path / "scoped").exists()


def test_preserves_existing_output_and_rejects_nested_output(tmp_path):
    source, _ = synthetic_package(tmp_path)
    before = snapshots(source)
    with pytest.raises(ScopeError):
        build(source, source / "scoped", scope(0))
    with pytest.raises(ScopeError):
        build(source, source, scope(0))
    output = tmp_path / "exists"
    output.mkdir()
    (output / "sentinel").write_bytes(b"preserve")
    with pytest.raises(ScopeError):
        build(source, output, scope(0))
    assert (output / "sentinel").read_bytes() == b"preserve"
    assert snapshots(source) == before


@pytest.mark.skipif(not os.environ.get("ML2_REVIEW_ORACLE_ROOT"),
                    reason="optional external strict importer oracle; see usage documentation")
def test_external_strict_importer_accepts_exact_export_and_rejects_extras(tmp_path):
    source, pkg = synthetic_package(tmp_path)
    output = build(source, tmp_path / "scoped", scope(0, 7))
    html = (output / "index.html").read_text(encoding="utf-8")
    payload = execute(html, responses=answers(pkg))["exported"]
    review = tmp_path / "synthetic-review.json"
    review.write_text(json.dumps(payload), encoding="utf-8")
    contract = {
        "games": {g["game_id"]: {**g, "kind": "candidate"} for g in pkg["manifest"]["games"]},
        "candidate_manifest_sha256": pkg["manifest_sha256"],
        "holdout_manifest_sha256": pkg["holdout_sha256"], "candidate_source_sha256": "b" * 64,
    }
    finalization = {"schema_version": 1, "kind": "finalized_batch_review",
                    "review_sha256": sha(review.read_bytes()), "finalized_by": "synthetic-human",
                    "finalized_at": "2026-10-30T00:00:00Z", "source_verified": True,
                    "game_ids": ["synthetic-0", "synthetic-7"], "holdout_prediction_blind": False,
                    "prior_exposure_note": "Invented synthetic proposals for test only"}
    program = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from backend.scripts.experimental_development_review_source import read_development_review
from backend.scripts.experimental_batch_review import ReviewError
data=json.load(sys.stdin)
try:
    selected=read_development_review(sys.argv[2],data['contract'],data['finalization'])
    print(json.dumps([c['game_id'] for c in selected['cases']]))
except ReviewError as exc:
    print(str(exc)); sys.exit(3)
'''
    command = [sys.executable, "-c", program, os.environ["ML2_REVIEW_ORACLE_ROOT"], str(review)]
    result = subprocess.run(command, input=json.dumps({"contract": contract, "finalization": finalization}),
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == finalization["game_ids"]
    original = execute((source / "index.html").read_text(encoding="utf-8"),
                       responses=answers(pkg))["exported"]
    review.write_text(json.dumps(original), encoding="utf-8")
    finalization["review_sha256"] = sha(review.read_bytes())
    result = subprocess.run(command, input=json.dumps({"contract": contract, "finalization": finalization}),
                            text=True, capture_output=True)
    assert result.returncode == 3
    assert "unexpected unfinalized development source game" in result.stdout
