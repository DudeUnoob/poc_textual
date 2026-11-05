import io
import json
import urllib.error
import pytest
from fastapi.testclient import TestClient


def test_provider_configuration_and_secret_file(monkeypatch,tmp_path):
    from provider_config import get_provider_config,credential
    monkeypatch.setenv('CENSUS_PROVIDER','portkey')
    monkeypatch.setenv('PORTKEY_MODEL','@anthropic-test/vision')
    monkeypatch.setenv('PORTKEY_COMPARISON_MODEL','@openai-test/vision')
    monkeypatch.delenv('PORTKEY_API_KEY',raising=False)
    secret=tmp_path/'secret';secret.write_text('secret-file-value\n')
    monkeypatch.setenv('PORTKEY_API_KEY_FILE',str(secret))
    settings=get_provider_config()
    assert settings.model=='@anthropic-test/vision'
    assert credential('PORTKEY_API_KEY')=='secret-file-value'
    assert 'secret-file-value' not in json.dumps(settings.snapshot())
    monkeypatch.setenv('PORTKEY_BASE_URL','https://user:password@example.com/v1')
    with pytest.raises(ValueError,match='HTTPS'):get_provider_config()


def test_gateway_retries_transient_errors_and_redacts_secret(monkeypatch):
    import portkey_extract
    monkeypatch.setenv('PORTKEY_API_KEY','super-secret-test')
    monkeypatch.setenv('PORTKEY_MAX_ATTEMPTS','2')
    calls=[];events=[]
    def transient(req,timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url,503,'busy',{},io.BytesIO(b'super-secret-test unavailable'))
    monkeypatch.setattr(portkey_extract.urllib.request,'urlopen',transient)
    monkeypatch.setattr(portkey_extract.time,'sleep',lambda _:None)
    with pytest.raises(RuntimeError) as exc:portkey_extract.request_json('/models',event_callback=events.append)
    assert len(calls)==2 and 'super-secret-test' not in str(exc.value)
    assert any(e['type']=='api_error' for e in events)


def test_unauthorized_requests_do_not_retry(monkeypatch):
    import portkey_extract
    monkeypatch.setenv('PORTKEY_API_KEY','test-secret')
    calls=[]
    def denied(req,timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url,401,'denied',{},io.BytesIO(b'denied'))
    monkeypatch.setattr(portkey_extract.urllib.request,'urlopen',denied)
    with pytest.raises(RuntimeError,match='HTTP 401'):portkey_extract.request_json('/models')
    assert len(calls)==1


def test_portkey_mode_blocks_legacy_gemini_requests(monkeypatch):
    from provider_config import require_credentials
    monkeypatch.setenv('CENSUS_PROVIDER','portkey')
    monkeypatch.setenv('GEMINI_API_KEY','unused-test-key')
    with pytest.raises(RuntimeError,match='disabled'):
        require_credentials('gemini-3.5-flash')


def test_analysts_can_queue_both_models_and_compare_drafts(monkeypatch):
    monkeypatch.setenv('PORTKEY_API_KEY','test-secret')
    monkeypatch.setenv('PORTKEY_MODEL','@anthropic-test/vision')
    monkeypatch.setenv('PORTKEY_COMPARISON_MODEL','@openai-test/vision')
    from workbench.web import app
    from workbench.db import Batch,Page,ExtractionRun,FieldCandidate,SessionLocal
    from sqlalchemy import select
    with TestClient(app) as client:
        assert client.get('/healthz').json()=={'status':'ok'}
        with SessionLocal() as session:
            batch=Batch(name='Both models',county='Bastrop',state='Texas',census_year=1950,enumeration_district='test')
            session.add(batch);session.flush()
            page=Page(batch_id=batch.id,original_filename='page.jpg',stored_path='/tmp/page.jpg',sha256='test',page_number=1,kind='census',metadata_confirmed=True)
            session.add(page);session.commit();batch_id,page_id=batch.id,page.id
        response=client.post(f'/batches/{batch_id}/runs',data={'model_choice':'both'},follow_redirects=False)
        assert response.status_code==303
        with SessionLocal() as session:
            runs=session.scalars(select(ExtractionRun).where(ExtractionRun.batch_id==batch_id).order_by(ExtractionRun.id)).all()
            assert [r.model for r in runs]==['@anthropic-test/vision','@openai-test/vision']
            assert all(r.config['strategy']=='portkey_full_page' and r.prompt_version=='portkey-v2' for r in runs)
            for run,text in zip(runs,['Jones','Jonas']):
                run.status='completed'
                session.add(FieldCandidate(run_id=run.id,page_id=page_id,line_number=1,field_name='Surname',raw_value=text,normalized_value=text,model_confidence='high',source_pass='portkey_full_page',row_legibility='partial'))
            session.commit()
        compared=client.get(f'/batches/{batch_id}/models')
        assert compared.status_code==200 and 'Jones' in compared.text and 'Jonas' in compared.text
        assert 'not proof of correctness' in compared.text
        assert client.post(f'/batches/{batch_id}/runs',data={'model_choice':'gemini'}).status_code==400


def test_portkey_cannot_inherit_legacy_auto_accept_bands(monkeypatch):
    from workbench.db import Batch,Page,ExtractionRun,CalibrationBand,FieldCandidate,SessionLocal,init_db
    from workbench.services import persist_extraction,reclassify_run_candidates
    from sqlalchemy import select
    init_db()
    with SessionLocal() as session:
        batch=Batch(name='Scoped calibration',county='Bastrop',state='Texas',census_year=1950,enumeration_district='test')
        session.add(batch);session.flush()
        page=Page(batch=batch,original_filename='x.jpg',stored_path='/tmp/x.jpg',sha256='x',page_number=1,metadata_confirmed=True)
        session.add(page);session.flush()
        run=ExtractionRun(batch_id=batch.id,model='@catalog/model');session.add(run);session.flush()
        session.add(CalibrationBand(census_year=1950,field_name='Surname',confidence='high',total=1000,correct=1000));session.flush()
        payload={'records':[{'Line Number':1,'Surname':'Jones'}],'field_candidates':[{'line_number':1,'field':'Surname','raw_value':'Jones','normalized_value':'Jones','model_confidence':'high','row_legibility':'clear','source_pass':'full_page'}]}
        persist_extraction(session,run,page,payload)
        candidate=session.scalar(select(FieldCandidate).where(FieldCandidate.run_id==run.id))
        assert not candidate.auto_accepted
        reclassify_run_candidates(session,run.id)
        assert not candidate.auto_accepted
