"""Low-setup census intake and report orchestration for the Codex skill.

The assistant verifies unfamiliar scans. The teammate never has to edit a plan.
Only hash-matched, previously reviewed alignments are reused automatically.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from PIL import Image, UnidentifiedImageError
import pandas as pd
from dotenv import set_key
import census_benchmark as benchmark
import census_audit as audit_module
from provider_config import credential, get_provider_config
from portkey_extract import request_json

YEARS = {1850, 1860, 1870, 1880, 1900, 1910, 1920, 1930, 1940, 1950}
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')


def unpack_images(archive, destination, max_bytes=5_000_000_000):
    """Extract only images, rejecting traversal, links and oversized archives first."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as z:
        members = []
        names = set()
        for item in z.infolist():
            name = PurePosixPath(item.filename.replace('\\', '/'))
            if name.is_absolute() or '..' in name.parts or any(':' in p for p in name.parts):
                raise ValueError('Unsafe ZIP member path')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('ZIP symbolic links are unsupported')
            if item.is_dir() or name.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            if any(p.startswith('.') or p == '__MACOSX' for p in name.parts):
                continue
            key = str(name).casefold()
            if key in names:
                raise ValueError('Duplicate ZIP image path')
            names.add(key)
            members.append((item, name))
        if len(members) > 10000 or sum(i.file_size for i, _ in members) > max_bytes or any(i.file_size > 100_000_000 for i, _ in members):
            raise ValueError('ZIP exceeds census intake size limit')
        for item, name in members:
            target = destination.joinpath(*name.parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError('ZIP target escapes intake directory')
            target.parent.mkdir(parents=True, exist_ok=True)
            data = z.read(item)
            if target.exists() and target.read_bytes() != data:
                raise ValueError('Intake would overwrite a different image')
            target.write_bytes(data)
    return [destination.joinpath(*name.parts) for _, name in members]


def infer_year(path, requested):
    # Nearest labeled directory wins, so an archive can contain several decades.
    for part in reversed(Path(path).parts):
        found = {int(y) for y in re.findall(r'(?<!\d)(18[5-8]0|19[0-5]0)(?!\d)', part)} & YEARS
        if len(found) == 1:
            return found.pop()
    return requested[0] if len(requested) == 1 else None


def prepare(inputs, years, output, truth_dir=None, pages_per_year=3, all_pages=False, models=None):
    output = Path(output).resolve()
    if (output / 'plan.json').exists():
        raise ValueError('This output already has a plan. Resume it with run; use a new folder for new intake.')
    years = list(dict.fromkeys(years or []))
    if any(y not in YEARS for y in years) or pages_per_year < 1:
        raise ValueError('Choose supported census years and a positive page limit')
    truth_dir = Path(truth_dir or ROOT / 'data/ground_truth').resolve()
    sources = list(inputs or [])
    if not sources:
        sources = [p for p in (ROOT / 'data/raw_images').glob('*')
                   if p.is_dir() and (not years or infer_year(p, []) in years)]
    files, source_years = [], {}
    for source in sources:
        source = Path(source).resolve()
        if source.suffix.lower() == '.zip':
            extracted = unpack_images(source, output / 'inputs' / digest(source)[:16])
            files.extend(extracted)
            source_years.update({p: infer_year(source, years) for p in extracted})
        elif source.is_dir():
            files.extend(p for p in source.rglob('*') if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file())
        elif source.suffix.lower() in IMAGE_SUFFIXES and source.is_file():
            files.append(source)
        else:
            raise ValueError('Input must be an image, ZIP or scan folder: ' + str(source))
    registry_path = ROOT / 'configs/verified-alignments.json'
    registry = read_json(registry_path).get('alignments', []) if registry_path.exists() else []
    registry = {r['image_sha256']: r for r in registry}
    inventory, jobs, pending, seen = [], [], [], set()
    counts = Counter()
    frames = {}
    anchors = []
    for path in sorted(set(files), key=lambda p: str(p).casefold()):
        sha = digest(path)
        record = registry.get(sha)
        year = record['year'] if record else infer_year(path, years) or source_years.get(path)
        entry = {'image': str(path), 'image_sha256': sha, 'year': year,
                 'selected': False, 'status': 'pending alignment'}
        if sha in seen:
            entry['status'] = 'duplicate image omitted'
        elif year is not None and years and year not in years:
            entry['status'] = 'outside requested years'
        elif year is not None and not all_pages and counts[year] >= pages_per_year:
            entry['status'] = 'outside selected sample'
        else:
            seen.add(sha)
            entry['selected'] = True
            if year is not None:
                counts[year] += 1
            workbook = truth_dir / f'Bastrop County {year} Clean.xlsx' if year else None
            job = {'year': year, 'image': str(path), 'image_sha256': sha,
                   'workbook': str(workbook) if workbook else None, 'sheet': None,
                   'gt_page': None, 'alignment_verified': False, 'fields': [],
                   'alignment_evidence': ''}
            try:
                with Image.open(path) as image:
                    entry.update(width=image.width, height=image.height)
                    image.verify()
            except (UnidentifiedImageError, OSError, ValueError):
                entry['status'] = 'unreadable image; not sent to a model'
                job['pending_reason'] = entry['status']
                pending.append(job)
                inventory.append(entry)
                continue
            if year == 1860:
                entry['status'] = 'unsupported: household alignment required'
            elif not workbook or not workbook.is_file():
                entry['status'] = 'missing census year or ground-truth workbook'
            else:
                if year not in frames:
                    frames[year] = pd.read_excel(workbook, sheet_name=None)
                    for sheet, frame in frames[year].items():
                        if 'Line Number' not in frame:
                            continue
                        # A candidate segment may be only one side of a reset block.
                        for idx, row in frame.iterrows():
                            line = row.get('Line Number')
                            if idx == 0 or line in (1, 51) or (idx and line <= frame.iloc[idx-1]['Line Number']):
                                for offset in range(idx, min(idx+3, len(frame))):
                                    anchor = frame.iloc[offset]
                                    anchors.append({'year': year, 'sheet': sheet, 'data_row': offset+1,
                                        **{k: benchmark.clean(anchor.get(k)) for k in ('Line Number', 'Surname', 'Given Name', 'Age')}})
                job['workbook_sha256'] = digest(workbook)
                if record and record['workbook_sha256'] == job['workbook_sha256']:
                    job.update({k: record[k] for k in ('sheet', 'gt_page', 'gt_row_start', 'gt_row_count', 'alignment_evidence') if k in record})
                    job['fields'] = [f for f in benchmark.CORE if f in frames[year][job['sheet']]]
                    job['alignment_verified'] = True
                    entry['status'] = 'verified by exact image and workbook hashes'
                entry['candidate_sheets'] = '; '.join(frames[year])
            if job['alignment_verified']:
                jobs.append(job)
            else:
                pending.append(job)
        inventory.append(entry)
    present = {r['year'] for r in inventory if r['selected']}
    for year in years:
        if year not in present:
            inventory.append({'year': year, 'selected': False, 'status': 'no scans supplied for requested year'})
    config = get_provider_config()
    plan = {'version': 2, 'created_utc': datetime.now(timezone.utc).isoformat(),
            'models': models or [config.portkey_model, config.comparison_model],
            'sampling_method': 'All supplied images requested' if all_pages else f'First {pages_per_year} unique images per year; convenience sample',
            'jobs': jobs, 'pending_jobs': pending, 'intake_inventory': inventory}
    write_json(output / 'plan.json', plan)
    benchmark.csv_write(output / 'intake.csv', inventory)
    benchmark.csv_write(output / 'alignment-anchors.csv', anchors)
    print(f'{len(jobs)} verified scans; {len(pending)} need assistant alignment. Plan: {output / "plan.json"}')
    return plan


def configure():
    """One-time local setup. Never accept a key in command arguments or echo it."""
    using_secret_file = bool(os.environ.get('PORTKEY_API_KEY_FILE')) and not os.environ.get('PORTKEY_API_KEY')
    key = credential('PORTKEY_API_KEY')
    if not key:
        key = getpass.getpass('Your Portkey API key (hidden): ').strip()
    if not key:
        raise ValueError('A Portkey API key is required')
    os.environ['PORTKEY_API_KEY'] = key
    catalog, _ = request_json('/models')
    ids = [m['id'] for m in catalog.get('data', []) if isinstance(m.get('id'), str)]
    families = {'PORTKEY_MODEL': ('claude', get_provider_config().portkey_model),
                'PORTKEY_COMPARISON_MODEL': ('gpt', get_provider_config().comparison_model)}
    chosen = {}
    for variable, (family, configured) in families.items():
        choices = [m for m in ids if m.startswith('@') and family in m.split('/')[-1].lower()]
        if configured in choices:
            chosen[variable] = configured
            continue
        same_model = [m for m in choices if m.split('/')[-1] == configured.split('/')[-1]]
        if len(same_model) == 1:
            chosen[variable] = same_model[0]
            continue
        print(f'{family} models in your workspace:')
        for number, model in enumerate(choices, 1):
            print(f'  {number}. {model}')
        answer = input('Choose a number or paste the model ID (@catalog/model): ').strip()
        model = choices[int(answer)-1] if answer.isdigit() and 1 <= int(answer) <= len(choices) else answer
        if model not in choices:
            raise ValueError('Select a listed model from the requested family')
        chosen[variable] = model
    env = ROOT / '.env'
    env.touch(exist_ok=True)
    settings = {'CENSUS_PROVIDER': 'portkey', **chosen}
    if not using_secret_file:
        settings['PORTKEY_API_KEY'] = key
    for name, value in settings.items():
        set_key(env, name, value)
        os.environ[name] = value
    print('Saved your local Portkey settings. Do not share .env. No census inference was requested.')


def finish(plan_path, output, node=None):
    output = Path(output).resolve()
    plan = read_json(plan_path)
    report_path = output / 'raw/report.json'
    report = read_json(report_path) if report_path.exists() else {
        'created_utc': datetime.now(timezone.utc).isoformat(), 'plan': plan,
        'summary': [], 'cells': [], 'failures': [], 'notes': ['No verified scan/model results are available.']}
    if report['plan']['jobs'] != plan['jobs'] or report['plan']['models'] != plan['models']:
        raise ValueError('The selected plan differs from these raw results; use the matching output folder')
    audit = audit_module.build(report)
    audit['intake_inventory'] = plan.get('intake_inventory', [
        {'year': j['year'], 'image': j['image'], 'selected': True, 'status': 'visually verified in supplied plan'}
        for j in plan['jobs']])
    audit['pending_alignments'] = plan.get('pending_jobs', [])
    audit['decade_inventory'] = audit_module.inventory()
    audit['variable_coverage'] = audit_module.variable_coverage(audit)
    audit['run_coverage'] = []
    done = {(r['job'], r['model']) for r in report['summary']}
    failed = {(r['job'], r['model']) for r in report['failures']}
    for i, job in enumerate(plan['jobs'], 1):
        for model in plan['models']:
            audit['run_coverage'].append({'job': i, 'year': job['year'], 'image': job['image'], 'model': model,
                'status': 'completed' if (i, model) in done else 'failed' if (i, model) in failed else 'not attempted'})
    audit['notes'].append('Sampling: ' + plan.get('sampling_method', 'See verified plan'))
    audit['notes'].append('The assistant verified page mappings. Discrepancies remain unadjudicated unless a review disposition explicitly says otherwise.')
    ledger = output / 'raw/attempt_events.jsonl'
    audit['attempt_events'] = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    prior = output / 'prior-attempts.json'
    if prior.exists():
        audit['attempt_events'] = read_json(prior) + audit['attempt_events']
    target = output / 'audit'
    write_json(target / 'audit.json', audit)
    for name, rows in audit.items():
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            benchmark.csv_write(target / (name + '.csv'), rows)
    workbook = target / 'granular-audit.xlsx'
    if node:
        subprocess.run([str(node), str(ROOT / 'scripts/export_benchmark.mjs'), str(target / 'audit.json'), str(workbook)], check=True)
    print(f'Audit: {target / "audit.json"}' + (f'\nWorkbook: {workbook}' if node else '\nXLSX pending: supply --node from Codex workspace dependencies.'))
    return audit


def run(plan_path, output, execute=False, node=None, max_output_tokens=32000):
    # Numeric confidence repeats every field name and needs a larger response.
    # This changes only this process, not the user's workbench environment file.
    os.environ['PORTKEY_MAX_OUTPUT_TOKENS'] = str(max_output_tokens)
    get_provider_config()  # Validate before preflight or paid requests.
    plan = read_json(plan_path)
    if not plan['jobs']:
        return finish(plan_path, output, node)
    benchmark.run(Path(plan_path), Path(output) / 'raw', False, len(plan['jobs']))
    if not execute:
        print('Preflight only. --execute runs the selected scans through the two models.')
        return
    failed = False
    try:
        benchmark.run(Path(plan_path), Path(output) / 'raw', True, len(plan['jobs']))
    except SystemExit:
        failed = True
    result = finish(plan_path, output, node)
    if failed:
        raise SystemExit(1)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--input', type=Path, action='append', default=[])
    p.add_argument('--years', nargs='+', type=int, default=[])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--truth-dir', type=Path)
    p.add_argument('--pages-per-year', type=int, default=3)
    p.add_argument('--all-pages', action='store_true')
    p.add_argument('--model', action='append')
    sub.add_parser('configure')
    for name in ('run', 'finish'):
        p = sub.add_parser(name)
        p.add_argument('plan', type=Path)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--node', type=Path)
        if name == 'run':
            p.add_argument('--execute', action='store_true')
            p.add_argument('--max-output-tokens', type=int, default=32000)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.input, args.years, args.output, args.truth_dir, args.pages_per_year, args.all_pages, args.model)
    elif args.command == 'configure':
        configure()
    elif args.command == 'run':
        run(args.plan, args.output, args.execute, args.node, args.max_output_tokens)
    else:
        finish(args.plan, args.output, args.node)


if __name__ == '__main__':
    main()
