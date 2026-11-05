import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from package_census_audit import package


def fixture_audit(tmp_path):
    external = tmp_path / 'another-persons-folders'
    external.mkdir()
    image = external / 'scan.png'
    image.write_bytes(b'scan fixture')
    truth = external / 'clean.xlsx'
    truth.write_bytes(b'truth fixture')
    response = external / 'response.json'
    response.write_text(json.dumps({'source_image': str(image), 'records': []}))
    audit = {'plan': {'models': ['@catalog/claude-test'], 'jobs': [
        {'year': 1880, 'image': str(image), 'workbook': str(truth), 'alignment_verified': True}]},
        'scan_results': [{'artifact': str(response)}]}
    directory = tmp_path / 'audit'
    directory.mkdir()
    path = directory / 'audit.json'
    path.write_text(json.dumps(audit))
    return path, image, truth, response


def test_evidence_package_is_portable_for_inputs_outside_repository(tmp_path):
    audit_path, image, truth, response = fixture_audit(tmp_path)
    destination = tmp_path / 'evidence.zip'
    package(audit_path, destination)
    with zipfile.ZipFile(destination) as z:
        plan = json.loads(z.read('run_plan.json'))
        for key in ('image', 'workbook'):
            assert not Path(plan['jobs'][0][key]).is_absolute()
            assert plan['jobs'][0][key] in z.namelist()
        portable_audit = json.loads(z.read('audit/audit.json'))
        payload = json.loads(z.read(portable_audit['scan_results'][0]['artifact']))
        assert payload['source_image'] == plan['jobs'][0]['image']
        for row in json.loads(z.read('PACKAGE_MANIFEST.json')):
            assert hashlib.sha256(z.read(row['path'])).hexdigest() == row['sha256']
        assert '.env' not in z.namelist()


def test_evidence_package_checks_credentials_inside_compressed_office_files(tmp_path, monkeypatch):
    audit_path, image, truth, response = fixture_audit(tmp_path)
    workbook = tmp_path / 'workbook.xlsx'
    secret = 'test-secret-that-must-not-be-published'
    with zipfile.ZipFile(workbook, 'w') as z:
        z.writestr('xl/sharedStrings.xml', secret)
    monkeypatch.setenv('PORTKEY_API_KEY', secret)
    with pytest.raises(ValueError, match='credential detected'):
        package(audit_path, tmp_path / 'unsafe.zip', workbook)
    assert not (tmp_path / 'unsafe.zip').exists()


def test_portkey_model_ids_are_provenance_rather_than_api_credentials(tmp_path, monkeypatch):
    import package_census_audit as packager
    audit_path, image, truth, response = fixture_audit(tmp_path)
    monkeypatch.setattr(packager, 'dotenv_values', lambda *a: {
        'PORTKEY_MODEL': '@catalog/claude-test', 'PORTKEY_API_KEY': 'unused-test-key'})
    package(audit_path, tmp_path / 'evidence.zip')
    with zipfile.ZipFile(tmp_path / 'evidence.zip') as z:
        assert '@catalog/claude-test' in z.read('run_plan.json').decode()
