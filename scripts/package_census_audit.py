"""Package only explicit benchmark inputs, outputs and code; never credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit',type=Path);parser.add_argument('zip',type=Path);parser.add_argument('--workbook',type=Path)
    args=parser.parse_args();audit=json.loads(args.audit.read_text())
    allowed=set()
    for job in audit['plan']['jobs']:
        allowed.update((ROOT/job['image'],ROOT/job['workbook']))
    for result in audit['scan_results']:allowed.add(ROOT/result['artifact'])
    allowed.update(p for p in args.audit.parent.iterdir() if p.suffix.lower() in ('.json','.csv','.md'))
    allowed.update(ROOT/p for p in ['requirements.txt','PORTKEY_WORKFLOW.md',
        'src/portkey_extract.py','src/provider_config.py','src/paths.py','src/utils.py','src/compare.py',
        'scripts/census_benchmark.py','scripts/census_audit.py','scripts/export_benchmark.mjs',
        'scripts/package_census_audit.py','skills/census-ground-test/SKILL.md'])
    allowed.update((ROOT/'schemas').glob('*.json'))
    for name in ('portkey-pilot.json','portkey-1950-confidence.json'):
        allowed.add(ROOT/'configs'/name)
    if args.workbook:allowed.add(args.workbook.resolve())
    secret=None
    env=ROOT/'.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith('PORTKEY_API_KEY='):secret=line.split('=',1)[1].encode()
    manifest=[]
    args.zip.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.zip,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(allowed):
            path=path.resolve();name=path.relative_to(ROOT).as_posix()
            if path.name=='.env' or not path.is_file():raise ValueError('Invalid package file: '+name)
            data=path.read_bytes()
            if secret and secret in data:raise ValueError('Credential found in package input: '+name)
            z.writestr(name,data)
            manifest.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        z.writestr('PACKAGE_MANIFEST.json',json.dumps(manifest,indent=2))
    with zipfile.ZipFile(args.zip) as z:
        bad=z.testzip()
        if bad:raise ValueError('ZIP integrity failed: '+bad)
    print(f'{len(manifest)} files; {args.zip.stat().st_size} bytes; ZIP integrity and credential checks passed')

if __name__=='__main__':main()
