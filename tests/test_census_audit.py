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
