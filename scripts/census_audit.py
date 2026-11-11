"""Reanalyze benchmark JSON at scan, row, field, and confidence-stratum level.

No API calls. Historical categorical confidence is never converted into a
fabricated probability. Intervals resample whole scans, not correlated cells.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from census_benchmark import csv_write, truth_for, clean
from compare import split_into_physical_pages
from fuzzywuzzy.fuzz import ratio
import pandas as pd


def same(a,b):
    a,b=clean(a),clean(b)
    if a is None or b is None:return a is None and b is None
    return str(a).strip().casefold()==str(b).strip().casefold()


def exact_agreement(row_status, a, b):
    """Absent people cannot earn an exact match from two empty placeholders."""
    return row_status == 'aligned' and same(a,b)


def scan_interval(cells, repetitions=1500):
    scans=defaultdict(list)
    for c in cells:scans[c['scan_id']].append(int(c['match']))
    # Five is a reporting floor, not proof of representativeness or independence.
    if len(scans)<5:return None,None,'insufficient scans (need >=5; more recommended)'
    totals=[(sum(v),len(v)) for v in scans.values()]
    rng=random.Random(20261104)
    draws=[]
    for _ in range(repetitions):
        sample=rng.choices(totals,k=len(totals))
        draws.append(sum(a for a,b in sample)/sum(b for a,b in sample))
    draws.sort()
    return draws[int(.025*repetitions)],draws[min(repetitions-1,int(.975*repetitions))],'95% scan-cluster percentile bootstrap; same-domain only'


def summarize(group, keys):
    n=len(group);correct=sum(c['match'] for c in group)
    nonblank=[c for c in group if c['truth_nonblank']]
    lo,hi,method=scan_interval(group)
    return {**keys,'scans':len({c['scan_id'] for c in group}),
        'rows':len({c['row_id'] for c in group}), 'observations':n,'correct':correct,
        'discrepancies':n-correct,'observed_accuracy':correct/n if n else None,
        'nonblank_observations':len(nonblank),
        'nonblank_accuracy':sum(c['match'] for c in nonblank)/len(nonblank) if nonblank else None,
        'accuracy_ci_low':lo,'accuracy_ci_high':hi,'interval_method':method,
        'outside_district_accuracy_established':False}


def build(report):
    jobs=report['plan']['jobs'];positions={};payloads={};columns={};scan_provenance=[]
    for i,job in enumerate(jobs,1):
        frame=pd.read_excel(job['workbook'],sheet_name=job['sheet'])
        columns[i]=list(frame.columns)
        if 'gt_row_start' in job:offset=job['gt_row_start']-1
        else:
            pages=split_into_physical_pages(frame,'Line Number')
            offset=sum(len(p) for p in pages[:job['gt_page']-1])
        for ix,row in enumerate(truth_for(job)):
            positions[(i,int(float(row['Line Number'])))]=offset+ix+2 # Excel row, including header
    for summary in report['summary']:
        path=Path(summary['artifact'])
        if not path.is_absolute():path=ROOT/path
        payload=json.loads(path.read_text())
        payloads[(summary['job'],summary['model'])]=payload
        scan_provenance.append({'job':summary['job'],'year':summary['year'],'model':summary['model'],
            'resolved_model':payload.get('resolved_model'),'request_id':payload.get('request_id'),
            'image_sha256':payload['image_sha256'],'prompt_sha256':payload['prompt_sha256'],
            'input_image_size':payload.get('input_image_size'),'compression':payload.get('compression'),'pipeline_version':payload.get('pipeline_version'),
            'image':summary['image']})
    cells=[]
    for original in report['cells']:
        c=dict(original);job=jobs[c['job']-1];payload=payloads[(c['job'],c['model'])]
        c['scan_id']=payload['image_sha256'][:20]
        c['row_id']=f'{c["scan_id"]}:line:{c["line"]}'
        c['entry_id']=f'{c["row_id"]}:{c["field"]}'
        c['model_entry_id']=hashlib.sha256((c['model']+c['entry_id']).encode()).hexdigest()[:24]
        c['ground_truth_workbook']=job['workbook'];c['ground_truth_excel_row']=positions.get((c['job'],c['line']))
        n=columns[c['job']].index(c['field'])+1;letter=''
        while n:
            n,r=divmod(n-1,26);letter=chr(65+r)+letter
        c['ground_truth_excel_column']=letter
        c['ground_truth_excel_cell']=letter+str(c['ground_truth_excel_row']) if c['ground_truth_excel_row'] is not None else None
        c['alignment_evidence']=job.get('alignment_evidence','not recorded')
        c['requested_model']=payload['model'];c['resolved_model']=payload.get('resolved_model')
        c['image_sha256']=payload['image_sha256'];c['prompt_sha256']=payload['prompt_sha256']
        c['request_id']=payload.get('request_id');c['raw_exact_match']=exact_agreement(c['row_status'],c['raw'],c['ground_truth'])
        c['normalized_exact_match']=exact_agreement(c['row_status'],c['extracted'],c['ground_truth'])
        c['similarity_0_100']=ratio(str(c['extracted']).casefold(),str(c['ground_truth']).casefold()) if c['extracted'] is not None and c['ground_truth'] is not None else None
        c['confidence_probability']=None
        for confidence in payload.get('confidence',[]):
            if confidence['line_number']==c['line']:
                c['confidence_probability']=confidence.get('probabilities',{}).get(c['field'])
        c['confidence_format']='numeric self-report' if c['confidence_probability'] is not None else 'ordinal label only'
        c['discrepancy_type']=('none' if c['match'] else c['row_status']+'_row' if c['row_status']!='aligned'
            else 'blank_vs_value' if c['raw'] is None or c['ground_truth'] is None
            else 'code_or_vocabulary_review' if c['field']=='Race' else 'value_mismatch')
        if c['field']=='Age' and re.match(r'^\d{4}-\d{2}-\d{2}',str(c['ground_truth'])):
            c['reference_note']='Age stored as a date. Review the scan and cleaning protocol; no correction was applied.'
            if not c['match'] and c['row_status']=='aligned':c['discrepancy_type']='ground_truth_age_date_review'
        c['review_disposition']='unreviewed' if not c['match'] else 'match_under_declared_rule'
        c['ground_truth_issue_confirmed']=False
        cells.append(c)
    groups=defaultdict(list)
    for c in cells:groups[(c['model'],c['row_id'])].append(c)
    rows=[]
    for (model,row_id),g in groups.items():
        first=g[0]
        rows.append({'row_id':row_id,'scan_id':first['scan_id'],'model':model,'year':first['year'],
            'image':first['image'],'sheet':first['sheet'],'line':first['line'],
            'ground_truth_excel_row':first['ground_truth_excel_row'],'row_status':first['row_status'],
            'fields_scored':len(g),'fields_correct':sum(c['match'] for c in g),
            'strict_row_match':all(c['match'] for c in g),
            'high_confidence_fields':sum(c['model_confidence']=='high' for c in g),
            'high_confidence_errors':sum(c['model_confidence']=='high' and not c['match'] for c in g),
            'discrepant_fields':'; '.join(c['field'] for c in g if not c['match'])})
    calibration=[];thresholds=[];year_fields=[]
    strata=defaultdict(list)
    for c in cells:strata[(c['model'],c['year'],c['field'])].append(c)
    for (model,year,field),group in strata.items():
        tags={'model':model,'year':year,'field':field}
        year_fields.append(summarize(group,tags))
        aligned=[c for c in group if c['row_status']=='aligned']
        for label in ('high','medium','low','unknown'):
            g=[c for c in aligned if c['model_confidence']==label]
            calibration.append(summarize(g,{**tags,'confidence_label':label}))
        policies=[('high only',lambda c:c['model_confidence']=='high'),
                  ('high + medium',lambda c:c['model_confidence'] in {'high','medium'}),
                  ('all labeled',lambda c:c['model_confidence'] in {'high','medium','low'})]
        if any(c['confidence_probability'] is not None for c in group):
            for threshold in (.90,.95,.99):
                policies.append((f'numeric self-report >= {threshold:.0%}',
                    lambda c,t=threshold:c['confidence_probability'] is not None and c['confidence_probability']>=t))
        for policy,accept in policies:
            # A real automation rule cannot consult ground-truth match flags.
            # Nonempty predicted fields are eligible; extra predictions count as wrong.
            eligible=[c for c in group if c['row_status']!='missing' and c['extracted'] not in (None,'')]
            accepted=[c for c in eligible if accept(c)]
            summary=summarize(accepted,{**tags,'policy':policy})
            precision=summary.pop('observed_accuracy')
            summary.update({'accepted_predictions':len(accepted),'eligible_predictions':len(eligible),
                'precision_on_accepted':precision,
                'accepted_error_rate':1-precision if precision is not None else None,
                'acceptance_coverage':len(accepted)/len(eligible) if eligible else None,
                'missing_truth_rows':len({c['row_id'] for c in group if c['row_status']=='missing'}),
                'reference_errors_per_1000_accepted':1000*(1-precision) if precision is not None else None,
                'reference_errors_low':1000*(1-summary['accuracy_ci_high']) if summary['accuracy_ci_high'] is not None else None,
                'reference_errors_high':1000*(1-summary['accuracy_ci_low']) if summary['accuracy_ci_low'] is not None else None,
                'projection_assumption':'Conditional on new data matching this tested year/field/domain; not validated outside district',
                'automation_validated':False})
            thresholds.append(summary)
    # Historical labels contain no numerical probabilities: no manufactured ECE/Brier score.
    probability=[]; reliability=[]
    numeric=[c for c in cells if isinstance(c['confidence_probability'],(float,int)) and c['row_status']=='aligned']
    for model in sorted({c['model'] for c in numeric}):
        g=[c for c in numeric if c['model']==model]
        probability.append({'model':model,'numeric_observations':len(g),
            'brier_score':sum((c['confidence_probability']-int(c['match']))**2 for c in g)/len(g),
            'target':'match under declared comparison rule; not guaranteed literal transcription'})
    numeric_groups=defaultdict(list)
    for c in numeric:numeric_groups[(c['model'],c['year'],c['field'],min(9,int(c['confidence_probability']*10)))].append(c)
    for (model,year,field,bin_id),g in numeric_groups.items():
        reliability.append({**summarize(g,{'model':model,'year':year,'field':field,'bin_lower':bin_id/10,'bin_upper':(bin_id+1)/10}),
            'mean_self_reported_probability':sum(c['confidence_probability'] for c in g)/len(g),
            'brier_score':sum((c['confidence_probability']-int(c['match']))**2 for c in g)/len(g)})
    notes=report['notes']+[
        'Confidence calibration is grouped by model, decade, field and label; pooled accuracy can hide subgroup failures.',
        'Bootstrap intervals resample scans and are withheld below five scans per stratum. Even five scans is not evidence of transportability.',
        'Errors per 1000 are same-domain reference projections, not validated forecasts for uncleaned districts.',
        'Calibration, threshold choice and evaluation should use disjoint scan/district partitions. This pilot has no held-out district.',
        'Numeric confidence, Brier scores and reliability bins use only responses that actually contain numeric self-reports. Historical label-only responses have no numeric estimate.',
        'Codes/blank-vs-value flags are review suggestions, not confirmed mistakes in ground truth. No discrepancies were excluded.',
        'Age cells stored as dates are flagged for reference review. Their original values and mismatches are retained without guessing the intended fraction.',
        'All actual observed fields are supplied; fields not requested in the original pilot were not tested and must not be treated as validated.'
    ]
    overview=[]
    for model,year in sorted({(r['model'],r['year']) for r in report['summary']}):
        group=[c for c in cells if c['model']==model and c['year']==year]
        scans=[r for r in report['summary'] if r['model']==model and r['year']==year]
        aligned_high=[c for c in group if c['row_status']=='aligned' and c['model_confidence']=='high']
        row_group=[r for r in rows if r['model']==model and r['year']==year]
        overview.append({**summarize(group,{'year':year,'model':model}),
            'strict_rows_matched':sum(r['strict_row_match'] for r in row_group),
            'rows_scored_including_extra':len(row_group),
            'strict_row_agreement':sum(r['strict_row_match'] for r in row_group)/len(row_group) if row_group else None,
            'missing_rows':sum(r['missing_rows'] for r in scans),
            'extra_rows':sum(r['extra_rows'] for r in scans),
            'aligned_high_confidence_values':len(aligned_high),
            'aligned_high_confidence_errors':sum(not c['match'] for c in aligned_high),
            'aligned_high_confidence_agreement':sum(c['match'] for c in aligned_high)/len(aligned_high) if aligned_high else None,
            'elapsed_seconds':sum(r.get('elapsed_seconds',0) for r in scans),
            'input_tokens':sum(r.get('input_tokens') or 0 for r in scans),
            'output_tokens':sum(r.get('output_tokens') or 0 for r in scans)})
    return {'created_utc':report['created_utc'],'started_utc':report.get('started_utc'),
            'overview':overview,'provider_configs':report.get('provider_configs',[]),
            'scan_results':report['summary'],'scan_provenance':scan_provenance,'rows':rows,'cells':cells,
            'discrepancies':[c for c in cells if not c['match']], 'confidence_calibration':calibration,
            'threshold_policies':thresholds,'decade_fields':year_fields,'numeric_confidence':probability,'reliability_bins':reliability,
            'failures':report['failures'],'notes':notes,'plan':report['plan']}


def inventory():
    index=json.loads((ROOT/'schemas/ground_truth_index.json').read_text())
    rows=[]
    for year,data in index.items():
        folders=[p for p in (ROOT/'data/raw_images').glob('*') if p.is_dir() and p.name.startswith(year+'_')]
        images=[p for d in folders for p in d.iterdir() if p.suffix.lower() in ('.jpg','.jpeg','.png','.tif','.tiff')]
        rows.append({'year':int(year),'workbook':data['file'],'tabs':'; '.join(data['sheets']),
            'local_scans':len(images),'status':'candidate scans; verify alignment' if images else 'needs matched scan acquisition',
            'alignment_method':'household order required; currently unsupported' if year=='1860' else 'verify scan/line anchors; explicit row ranges as needed',
            'sampling_plan':'Sample multiple enumerators/pages and hold out entire scans/districts; retain a random QA sample of high-confidence accepts'})
    return rows


def variable_coverage(audit):
    index=json.loads((ROOT/'schemas/ground_truth_index.json').read_text())
    out=[]
    for year,book in index.items():
        columns=sorted({c for sheet in book['sheets'].values() for c in sheet['columns'] if c and not c.startswith('Unnamed:')})
        for field in columns:
            tested=[c for c in audit['cells'] if c['year']==int(year) and c['field']==field]
            out.append({'year':int(year),'field':field,'tested_cells_including_models':len(tested),
                'distinct_scans_tested':len({c['scan_id'] for c in tested}),
                'status':'tested on pilot only' if tested else 'not tested',
                'source_visibility':'requires codebook/form review; do not assume every GT column is image-recoverable'})
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('report',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();audit=build(json.loads(args.report.read_text()));audit['decade_inventory']=inventory();audit['variable_coverage']=variable_coverage(audit)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'audit.json').write_text(json.dumps(audit,indent=2,default=str))
    for key in ('overview','scan_results','scan_provenance','rows','cells','discrepancies','confidence_calibration','threshold_policies','decade_fields','numeric_confidence','reliability_bins','decade_inventory','variable_coverage','failures'):
        csv_write(args.output/(key+'.csv'),audit[key])
    print(f'{len(audit["scan_results"])} scan/model results; {len(audit["rows"])} rows; {len(audit["cells"])} cells; {len(audit["discrepancies"])} discrepancies')

if __name__=='__main__':main()
