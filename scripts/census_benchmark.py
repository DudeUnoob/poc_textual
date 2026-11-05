"""Prepare auditable test plans, then run the same pages through Portkey models."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import pandas as pd
from compare import split_into_physical_pages
from fuzzywuzzy.fuzz import ratio
from portkey_extract import DEFAULT_MODEL, COMPARISON_MODEL, extract, request_json
from provider_config import get_provider_config

CORE = ['Surname', 'Given Name', 'Race', 'Gender', 'Sex', 'Age',
        'Relationship', 'Relation to Head of House', 'Relation to Head',
        'Birth Place', 'Birthplace', 'Occupation', 'Industry',
        "Father's Birthplace", "Mother's Birthplace"]


def clean(value):
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else value


def matches(field, a, b):
    a, b = clean(a), clean(b)
    if a is None or b is None:
        return a is None and b is None
    a, b = str(a).strip().casefold(), str(b).strip().casefold()
    if field == 'Age':
        try: return abs(float(a)-float(b)) <= 1
        except ValueError: return a == b
    if field in ('Race', 'Gender', 'Sex'): return a == b
    return ratio(a, b) >= (85 if field in ('Surname', 'Given Name') else 85)


def score(payload, truth, fields):
    """Fixed denominators; extra and missing rows count as errors, never disappear."""
    truth = [{k: clean(v) for k, v in r.items()} for r in truth]
    def keyed(rows):
        out = {}
        for row in rows:
            line = row.get('Line Number')
            if line is None or float(line) != int(float(line)):
                raise ValueError('Invalid ground truth/extraction line number')
            line = int(float(line))
            if line in out: raise ValueError('Duplicate line numbers: fix page alignment before scoring')
            out[line] = row
        return out
    gt, pred = keyed(truth), keyed(payload['records'])
    if not gt: raise ValueError('Empty truth page')
    conf = {r['line_number']: r['fields'] for r in payload.get('confidence', [])}
    raw = keyed(payload.get('raw_records', payload['records']))
    cells, row_ok = [], []
    for line in sorted(gt.keys() | pred.keys()):
        row_matches = []
        for field in fields:
            a, b = pred.get(line, {}).get(field), gt.get(line, {}).get(field)
            ok = line in gt and line in pred and matches(field, a, b)
            row_matches.append(ok)
            cells.append({'line': line, 'field': field, 'raw': raw.get(line, {}).get(field),
                'extracted': a, 'ground_truth': b, 'match': ok,
                'row_status': 'extra' if line not in gt else 'missing' if line not in pred else 'aligned',
                'model_confidence': conf.get(line, {}).get(field, 'unknown'),
                'truth_nonblank': b not in (None, ''),
                'review': not ok or conf.get(line, {}).get(field, 'unknown') != 'high'})
        row_ok.append(all(row_matches))
    n = len(gt.keys() | pred.keys())
    return {'truth_rows':len(gt), 'extracted_rows':len(pred), 'missing_rows':len(gt.keys()-pred.keys()),
            'extra_rows':len(pred.keys()-gt.keys()), 'matched_rows':sum(row_ok), 'rows_scored':n,
            'correct_cells':sum(c['match'] for c in cells), 'cells_scored':len(cells),
            'row_accuracy':sum(row_ok)/n, 'cell_accuracy':sum(c['match'] for c in cells)/len(cells)}, cells


def csv_write(path, rows):
    if not rows:
        path.write_text('status\nNo observations\n', encoding='utf-8-sig')
        return
    with path.open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
        writer.writeheader()
        for row in rows:
            # Excel CSV formula injection protection; preserve originals in JSON.
            writer.writerow({k: "'"+v if isinstance(v,str) and v.startswith(('=', '+', '-', '@')) else v for k,v in row.items()})


def truth_for(job):
    df = pd.read_excel(job['workbook'], sheet_name=job['sheet'])
    if 'gt_row_start' in job or 'gt_row_count' in job:
        start, count = job.get('gt_row_start'), job.get('gt_row_count')
        if not isinstance(start, int) or not isinstance(count, int) or start < 1 or count < 1 or start+count-1 > len(df):
            raise ValueError('Invalid explicit ground-truth data-row range')
        return df.iloc[start-1:start-1+count].to_dict('records')
    pages = split_into_physical_pages(df, 'Line Number')
    if job['gt_page'] > len(pages): raise ValueError('Ground truth page out of range')
    return pages[job['gt_page']-1].to_dict('records')


def prepare(year, images, sheet, output):
    workbook = ROOT / 'data/ground_truth' / f'Bastrop County {year} Clean.xlsx'
    df = pd.read_excel(workbook, sheet_name=sheet)
    if 'Line Number' not in df: raise ValueError('This workflow needs printed line numbers; 1860 is unsupported.')
    pages = split_into_physical_pages(df, 'Line Number')
    files = sorted(p for p in Path(images).resolve().iterdir() if p.suffix.lower() in ('.jpg','.jpeg','.png','.tif','.tiff'))
    if not files: raise ValueError('No images found; unzip the archive first.')
    plan = {'version':1, 'models':[DEFAULT_MODEL, COMPARISON_MODEL], 'jobs':[
        {'year':year,'image':str(p),'workbook':str(workbook),'sheet':sheet,
         'gt_page':None,'alignment_verified':False, 'fields':[f for f in CORE if f in df.columns]}
        for p in files]}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(plan,indent=2))
    anchors=[]
    for i,page in enumerate(pages,1):
        for row in page.head(3).to_dict('records'):
            anchors.append({'gt_page':i,**{f:clean(row.get(f)) for f in ['Line Number','Surname','Given Name','Age']}})
    csv_write(output.with_suffix('.anchors.csv'), anchors)
    print(f'Created {output}; verify names against anchors, set gt_page and alignment_verified=true for test pages. Remove unselected jobs.')


def run(plan_path, output, execute=False, limit=3):
    plan=json.loads(plan_path.read_text())
    jobs=plan['jobs']
    models=plan['models']
    if not jobs or not models or len(set(models)) != len(models): raise ValueError('Need jobs and unique models')
    if len(jobs)>limit: raise ValueError(f'{len(jobs)} pages exceeds --max-pages {limit}; increase explicitly for a full batch.')
    for job in jobs:
        if job['year']==1860: raise ValueError('1860 needs household alignment, not line numbers')
        if not job.get('alignment_verified') or not isinstance(job.get('gt_page'),int) or job['gt_page']<1:
            raise ValueError(f'Page alignment is not verified: {job["image"]}')
        df=pd.read_excel(job['workbook'],sheet_name=job['sheet'])
        if not job['fields'] or any(f not in df.columns or f=='Line Number' for f in job['fields']):raise ValueError('Invalid scoring fields')
        if len(set(job['fields'])) != len(job['fields']):raise ValueError('Repeated scoring fields')
        truth = truth_for(job)
        score({'records': []}, truth, job['fields'])  # Reject duplicate/invalid GT keys before spending.
        if not Path(job['image']).is_file():raise ValueError('Image missing')
        for name,key in [('image','image_sha256'),('workbook','workbook_sha256')]:
            if job.get(key) and hashlib.sha256(Path(job[name]).read_bytes()).hexdigest()!=job[key]:
                raise ValueError(f'Verified {name} changed; recheck page alignment before spending')
    config=get_provider_config()
    print(f'{len(jobs)} verified pages x {len(models)} models = {len(jobs)*len(models)} page calls (up to {config.attempts} transport attempts each).')
    if not execute: print('Plan is valid. Add --execute to call Portkey.');return
    output.mkdir(parents=True,exist_ok=True)
    summary,cells,failures=[],[],[]
    started_utc=datetime.now(timezone.utc).isoformat()
    def log_attempt(event):
        with (output/'attempt_events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),**event})+'\n')
    account_error = False
    for i,job in enumerate(jobs):
        truth=truth_for(job)
        for model in models:
            identity={'job':job,'model':model,'image_hash':hashlib.sha256(Path(job['image']).read_bytes()).hexdigest(),
                      'workbook_hash':hashlib.sha256(Path(job['workbook']).read_bytes()).hexdigest(),
                      'extractor_hash':hashlib.sha256((ROOT/'src/portkey_extract.py').read_bytes()).hexdigest(),
                      'normalizer_hash':hashlib.sha256((ROOT/'src/utils.py').read_bytes()).hexdigest(),
                      'provider_config':config.snapshot(model)}
            fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
            saved=output/(fingerprint+'.json')
            attempt={'job':i+1,'model':model,'image':job['image'],'fingerprint':fingerprint,
                     'provider_config':config.snapshot(model)}
            try:
                cached = saved.exists()
                log_attempt({**attempt,'status':'cache_read' if cached else 'started'})
                def on_event(event):
                    log_attempt({**attempt,'status':event['type'],**{k:v for k,v in event.items() if k!='type'}})
                payload=json.loads(saved.read_text()) if cached else extract(job['image'],job['year'],job['fields'],model,event_callback=on_event)
                saved.write_text(json.dumps(payload,indent=2))
                metrics,detail=score(payload,truth,job['fields'])
                tags={'job':i+1,'year':job['year'],'image':job['image'],'sheet':job['sheet'],'gt_page':job['gt_page'],
                      'gt_row_start':job.get('gt_row_start'), 'gt_row_count':job.get('gt_row_count'),'model':model}
                summary.append({**tags,**metrics,'elapsed_seconds':payload['elapsed_seconds'],
                                'input_tokens':payload['usage'].get('prompt_tokens'),
                                'output_tokens':payload['usage'].get('completion_tokens'), 'cached':cached, 'artifact':str(saved)})
                cells.extend({**tags,**d} for d in detail)
                log_attempt({**attempt,'status':'cached' if cached else 'completed','artifact':str(saved)})
                print(f'{job["year"]} {Path(job["image"]).name} {model.split("/")[-1]}: {metrics["cell_accuracy"]:.1%}' + (' [cached; no API call]' if cached else ''))
            except Exception as exc:
                log_attempt({**attempt,'status':'failed','error':str(exc)})
                failures.append({'job':i+1,'year':job['year'],'image':job['image'],'model':model,'error':str(exc)})
                print(f'Failed job {i+1}, {model}: {exc}')
                # Account/auth errors stop immediately; never spend through a broken configuration.
                if any(f'HTTP {code}' in str(exc) for code in (401,402,403)):
                    account_error = True
                    break
        if account_error:
            break
    fields=[]
    confidence=[]
    for model in models:
        for field in sorted({c['field'] for c in cells if c['model']==model}):
            group=[c for c in cells if c['model']==model and c['field']==field]
            nonblank=[c for c in group if c['truth_nonblank']]
            fields.append({'model':model,'field':field,'correct':sum(c['match'] for c in group),'scored':len(group),
                           'accuracy':sum(c['match'] for c in group)/len(group),
                           'nonblank_scored':len(nonblank),'nonblank_accuracy':sum(c['match'] for c in nonblank)/len(nonblank) if nonblank else None})
        for label in ('high','medium','low','unknown'):
            group=[c for c in cells if c['model']==model and c['model_confidence']==label and c['row_status']=='aligned']
            confidence.append({'model':model,'self_reported_label':label,'observations':len(group),
                               'observed_accuracy':sum(c['match'] for c in group)/len(group) if group else None})
    report={'created_utc':datetime.now(timezone.utc).isoformat(),'started_utc':started_utc,'plan':plan,'summary':summary,'fields':fields,
            'confidence':confidence,'cells':cells,'failures':failures,
            'provider_configs':[config.snapshot(m) for m in models],
            'notes':['Confidence labels are uncalibrated model self-reports; observed accuracy is measured separately.',
                     'Race uses literal-code normalization only; research recoding and ground-truth typos remain review items.',
                     'Missing and extra rows are errors. Blank agreement is included; nonblank accuracy is also reported.',
                     'Age tolerance +/-1; names and other text fuzzy threshold 85; race and sex exact ignoring case.',
                     'Pilot results do not establish a production accuracy guarantee. No Ancestry baseline supplied.']}
    (output/'report.json').write_text(json.dumps(report,indent=2,default=str))
    (output/'run_plan.json').write_text(json.dumps(plan,indent=2))
    for name,rows in [('summary',summary),('fields',fields),('confidence',confidence),('cells',cells),('failures',failures)]:csv_write(output/(name+'.csv'),rows)
    print(f'Report: {output / "report.json"}')
    if failures:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--year',type=int,required=True);p.add_argument('--images',required=True);p.add_argument('--sheet',required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('run');p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true');p.add_argument('--max-pages',type=int,default=3)
    p=sub.add_parser('models');p.add_argument('provider')
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.year,args.images,args.sheet,args.output)
    elif args.command=='run':run(args.plan,args.output,args.execute,args.max_pages)
    else:print(json.dumps(request_json('/models',provider=args.provider)[0],indent=2))
