import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('benchmark', Path(__file__).parents[1]/'scripts/census_benchmark.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)

def test_missing_and_extra_rows_cannot_score_as_correct_blanks():
    truth=[{'Line Number':1,'Surname':'Jones','Race':None},
           {'Line Number':2,'Surname':'Smith','Race':None}]
    pred={'records':[truth[0],{'Line Number':3,'Surname':'Other','Race':None}]}
    metrics,cells=benchmark.score(pred,truth,['Surname','Race'])
    assert metrics['missing_rows']==1 and metrics['extra_rows']==1
    assert metrics['correct_cells']==2 and metrics['cells_scored']==6
    assert metrics['row_accuracy']==pytest.approx(1/3)

def test_duplicate_line_numbers_are_rejected():
    with pytest.raises(ValueError,match='Duplicate'):
        benchmark.score({'records':[{'Line Number':1},{'Line Number':1}]},[{'Line Number':1}],['Age'])

def test_numeric_tolerance_does_not_apply_to_race_or_sex():
    assert benchmark.matches('Age','40',41)
    assert not benchmark.matches('Race','White','Whiite')
    assert not benchmark.matches('Sex','Male','Female')

def test_unverified_plan_stops_before_any_api_call(tmp_path,monkeypatch):
    plan=tmp_path/'plan.json'
    plan.write_text('{"models":["test"],"jobs":[{"year":1880,"image":"scan.jpg","gt_page":1,"alignment_verified":false}]}')
    monkeypatch.setattr(benchmark,'extract',lambda *a:pytest.fail('API must not be called'))
    with pytest.raises(ValueError,match='alignment'):
        benchmark.run(plan,tmp_path/'out',True)


def test_explicit_truth_range_splits_continuing_numbered_scan_sides(monkeypatch):
    import pandas as pd
    df=pd.DataFrame({'Line Number':list(range(1,101))+list(range(51,101)),
                     'Surname':['Jones']*150})
    monkeypatch.setattr(benchmark.pd,'read_excel',lambda *a,**k:df)
    truth=benchmark.truth_for({'workbook':'unused','sheet':'Bastrop 1',
                               'gt_page':1,'gt_row_start':1,'gt_row_count':50})
    assert len(truth)==50 and truth[-1]['Line Number']==50
    metrics,_=benchmark.score({'records':truth},truth,['Surname'])
    assert metrics['row_accuracy']==1


def test_portkey_request_uses_modern_openai_token_parameter(tmp_path,monkeypatch):
    import portkey_extract
    from PIL import Image
    path=tmp_path/'scan.jpg';Image.new('RGB',(20,20),'white').save(path)
    def fake(endpoint,payload):
        assert payload['max_completion_tokens']==16000
        assert 'max_tokens' not in payload
        return {'choices':[{'finish_reason':'stop','message':{'content':'{"records":[{"values":{"Line Number":1,"Surname":"Jones"},"confidence":{"Surname":"high"}}]}'}}]},{}
    monkeypatch.setattr(portkey_extract,'request_json',fake)
    result=portkey_extract.extract(path,1880,['Surname'],portkey_extract.COMPARISON_MODEL)
    assert result['raw_records'][0]['Surname']=='Jones'


def test_existing_extractor_routes_catalog_models_without_gemini(monkeypatch):
    import extract,portkey_extract
    monkeypatch.setattr(portkey_extract,'extract',lambda *a:{'raw_records':[], 'records':[],
        'confidence':[],'usage':{},'resolved_model':'test','elapsed_seconds':1,
        'image_sha256':'hash','prompt_sha256':'hash','request_id':'id'})
    monkeypatch.setattr(extract,'get_client',lambda:pytest.fail('Gemini must not be initialized'))
    records,diagnostics,candidates=extract.extract_with_review_data('unused',1880,model='@catalog/model')
    assert diagnostics['strategy']=='portkey_full_page' and records==[]
