"""Package explicit census evidence with portable paths and no credentials."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dotenv import dotenv_values
from provider_config import credential


def package(audit_path, destination, workbook=None):
    audit_path, destination = Path(audit_path).resolve(), Path(destination).resolve()
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    files, translations = {}, {}

    def add(value, category=None):
        path = Path(value)
        path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file() or path.name.startswith('.env') and path.name != '.env.example':
            raise ValueError('Invalid package input')
        if category:
            sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            name = f'inputs/{category}/{sha}-{path.name}'
        else:
            name = path.relative_to(ROOT).as_posix()
        if name in files and files[name] != path:
            raise ValueError('Package name collision')
        files[name] = path
        translations[str(value)] = name
        translations[str(path)] = name
        return name

    for job in audit['plan']['jobs']:
        if Path(job['image']).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.tif', '.tiff'):
            raise ValueError('A tested scan must be an image')
        if Path(job['workbook']).suffix.lower() != '.xlsx':
            raise ValueError('Ground truth must be an XLSX workbook')
        add(job['image'], 'scans')
        add(job['workbook'], 'ground_truth')
    for result in audit['scan_results']:
        add(result['artifact'], 'responses')
    for path in audit_path.parent.iterdir():
        if path.name == 'audit.json' or (path.suffix == '.csv' and path.stem in audit):
            files['audit/' + path.name] = path
    source_names = ['requirements.txt', '.env.example', 'PORTKEY_WORKFLOW.md',
        'src/portkey_extract.py', 'src/provider_config.py', 'src/paths.py', 'src/utils.py', 'src/compare.py',
        'scripts/census_benchmark.py', 'scripts/census_audit.py', 'scripts/census_ground_test.py',
        'scripts/export_benchmark.mjs', 'scripts/package_census_audit.py',
        'skills/census-ground-test/SKILL.md', 'skills/census-ground-test/references/workflow.md',
        'docs/WORKBOOK_GUIDE.md', 'docs/census-workbook-reading-guide.docx', 'configs/verified-alignments.json']
    for name in source_names:
        add(ROOT / name)
    for path in (ROOT / 'schemas').glob('*.json'):
        add(path)
    if workbook:
        path = Path(workbook).resolve()
        files['granular-audit.xlsx'] = path

    # Include actual source report when available; its paths are rewritten too.
    source_report = audit_path.parent.parent / 'raw/report.json'
    if source_report.is_file():
        files['raw/report.json'] = source_report
    events = audit_path.parent.parent / 'raw/attempt_events.jsonl'
    if events.is_file():
        files['raw/attempt_events.jsonl'] = events

    def rewrite(value):
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        return translations.get(value, value) if isinstance(value, str) else value

    values = dotenv_values(ROOT / '.env')
    secrets = [v.encode() for k, v in values.items() if v and any(s in k for s in ('KEY', 'TOKEN', 'SECRET'))]
    for key in ('PORTKEY_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY'):
        value = credential(key)
        if value:
            secrets.append(value.encode())

    def check(data):
        chunks = [data]
        if data[:4] == b'PK\x03\x04':
            with zipfile.ZipFile(io.BytesIO(data)) as nested:
                chunks.extend(nested.read(n) for n in nested.namelist())
        if any(secret in chunk for secret in secrets for chunk in chunks):
            raise ValueError('Configured credential detected in package input')

    contents = {}
    for name, path in sorted(files.items()):
        if path.suffix == '.json':
            data = json.dumps(rewrite(json.loads(path.read_text(encoding='utf-8'))), indent=2).encode()
        elif path.suffix == '.jsonl':
            data = ('\n'.join(json.dumps(rewrite(json.loads(line))) for line in path.read_text().splitlines()) + '\n').encode()
        elif path.suffix == '.csv':
            rows = list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            out = io.StringIO(newline='')
            csv.writer(out).writerows([[rewrite(v) for v in row] for row in rows])
            data = out.getvalue().encode('utf-8-sig')
        else:
            data = path.read_bytes()
        check(data)
        contents[name] = data
    # This is the runnable plan after unpacking, independent of either user's path.
    contents['run_plan.json'] = json.dumps(rewrite(audit['plan']), indent=2).encode()
    check(contents['run_plan.json'])
    manifest = [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                for name, data in sorted(contents.items())]
    contents['PACKAGE_MANIFEST.json'] = json.dumps(manifest, indent=2).encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip():
            raise ValueError('ZIP integrity failed')
    print(f'{len(manifest)} files; {destination.stat().st_size} bytes; ZIP integrity and credential checks passed')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit', type=Path)
    parser.add_argument('zip', type=Path)
    parser.add_argument('--workbook', type=Path)
    args = parser.parse_args()
    package(args.audit, args.zip, args.workbook)


if __name__ == '__main__':
    main()
