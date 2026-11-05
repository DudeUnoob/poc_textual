import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from census_audit import scan_interval,summarize,exact_agreement


def test_one_thousand_cells_on_one_scan_do_not_manufacture_sample_size():
    cells=[dict(scan_id='one',row_id=str(i),match=True,truth_nonblank=True) for i in range(1000)]
    lo,hi,method=scan_interval(cells)
    assert lo is None and hi is None and 'insufficient' in method
    assert summarize(cells,{})['scans']==1


def test_bootstrap_preserves_scan_clusters_and_is_reproducible():
    cells=[dict(scan_id=str(s),row_id=f'{s}:{i}',match=s<3,truth_nonblank=True) for s in range(5) for i in range(10)]
    first=scan_interval(cells)
    assert first==scan_interval(cells)
    assert first[0] < .6 < first[1]


def test_blank_accuracy_is_separate_from_nonblank_accuracy():
    cells=[dict(scan_id='one',row_id='1',match=True,truth_nonblank=False),
           dict(scan_id='one',row_id='2',match=False,truth_nonblank=True)]
    out=summarize(cells,{})
    assert out['observed_accuracy']==.5 and out['nonblank_accuracy']==0
    assert out['outside_district_accuracy_established'] is False


def test_unmatched_rows_never_receive_exact_agreement_for_empty_values():
    assert not exact_agreement('extra',None,None)
    assert not exact_agreement('missing',None,None)
    assert exact_agreement('aligned',None,None)


def test_numeric_thresholds_penalize_extra_predictions_and_keep_missing_rows_visible(tmp_path,monkeypatch):
    import json
    import pandas as pd
    import census_audit as audit
    import census_benchmark as benchmark
    frame=pd.DataFrame({'Line Number':[1,2],'Surname':['Jones','Smith']})
    monkeypatch.setattr(audit.pd,'read_excel',lambda *a,**k:frame)
    payload={'records':[{'Line Number':1,'Surname':'Jones'},{'Line Number':3,'Surname':'Extra'}],
        'confidence':[{'line_number':i,'fields':{'Surname':'high'},'probabilities':{'Surname':.99}} for i in (1,3)],
        'image_sha256':'a'*64,'model':'@test/claude-test','prompt_sha256':'prompt'}
    path=tmp_path/'response.json';path.write_text(json.dumps(payload))
    job={'year':1880,'image':'scan.png','workbook':'mocked.xlsx','sheet':'Bastrop','gt_page':1,'fields':['Surname']}
    metrics,cells=benchmark.score(payload,frame.to_dict('records'),job['fields'])
    tags={'job':1,'year':1880,'model':payload['model'],'image':'scan.png','sheet':'Bastrop'}
    result=audit.build({'plan':{'jobs':[job]},'summary':[{**tags,**metrics,'artifact':str(path)}],
        'cells':[{**tags,**c} for c in cells],'created_utc':'test','notes':[],'failures':[]})
    row=next(r for r in result['threshold_policies'] if r['policy']=='numeric self-report >= 99%')
    assert row['accepted_predictions']==2 and row['precision_on_accepted']==.5
    assert row['missing_truth_rows']==1
    assert result['overview'][0]['observations']==3
    assert result['overview'][0]['correct']==1 and result['overview'][0]['discrepancies']==2
