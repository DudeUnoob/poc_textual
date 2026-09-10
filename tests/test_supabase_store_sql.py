"""Real PostgreSQL transaction/permissions tests; opt in with RUN_SQL_STORE_TESTS=1.

Uses an isolated disposable Docker container and an already available Supabase
Postgres image. Never connects to a configured production project.
"""
import concurrent.futures
import os
from pathlib import Path
import subprocess
import time
import uuid
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_SQL_STORE_TESTS') != '1', reason='Local Docker SQL integration is opt-in')


@pytest.fixture(scope='module')
def sql():
    container = 'census-store-sql-' + uuid.uuid4().hex[:10]
    subprocess.run(['docker', 'run', '-d', '--rm', '--name', container, '-e', 'POSTGRES_PASSWORD=local-test-only',
                    'supabase/postgres:15.8.1.060'], check=True, capture_output=True)
    def execute(statement, check=True):
        result = subprocess.run(['docker', 'exec', '-i', container, 'psql', '-U', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1'],
                                input=statement, text=True, capture_output=True)
        if check and result.returncode:
            pytest.fail(result.stderr)
        return result
    try:
        for _ in range(60):
            if subprocess.run(['docker', 'exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], capture_output=True).returncode == 0:
                break
            time.sleep(0.25)
        # The image ships old Auth migrations; model the current GoTrue columns
        # consumed by our service-role RPC without requiring the Auth service.
        execute('''create table if not exists auth.sessions (id uuid primary key, user_id uuid, not_after timestamptz);
            alter table auth.users add column if not exists deleted_at timestamptz;
            alter table auth.users add column if not exists banned_until timestamptz;
            alter table storage.buckets add column if not exists public boolean not null default false;''')
        for migration in sorted((Path(__file__).parents[1] / 'supabase/migrations').glob('*.sql')):
            execute(migration.read_text())
        yield execute
    finally:
        subprocess.run(['docker', 'stop', container], capture_output=True)


def test_real_sql_serializes_competing_commit_and_rolls_back_bad_batch(sql):
    revision = sql('select public.workbench_version();').stdout.strip()
    query = f"select public.workbench_commit({revision}, '[{{\"path\":\"rows/one\",\"value\":{{\"revision\":1}}}}]');"
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: sql(query).stdout.strip(), range(2)))
    assert sorted(outcomes) == ['f', 't']
    revision = sql('select public.workbench_version();').stdout.strip()
    bad = sql(f'''select public.workbench_commit({revision}, '[{{"path":"audit/one","value":{{"ok":true}}}},{{"path":"invalid","value":{{}}}}]');''', check=False)
    assert bad.returncode != 0
    assert sql("select public.workbench_read('audit/one') is null;").stdout.strip() == 't'
    assert sql('select public.workbench_version();').stdout.strip() == revision


@pytest.mark.parametrize('role', ['anon', 'authenticated'])
def test_browser_roles_cannot_read_write_or_call_rpc(sql, role):
    for query in ["select public.workbench_version();", "select public.workbench_read('rows/one');",
                  "select public.workbench_list('rows');", "select public.workbench_commit(0, '[]');",
                  "select public.workbench_session_active(null,null);",
                  'select * from workbench_private.documents;',
                  "insert into workbench_private.documents values ('rows/hack','{}',now());"]:
        result = sql(f'set role {role}; {query}', check=False)
        assert result.returncode != 0, query
        assert 'permission denied' in result.stderr


def test_backend_role_only_rpc_and_session_expiry(sql):
    assert sql('set role service_role; select public.workbench_version();').returncode == 0
    assert sql('set role service_role; select * from workbench_private.documents;', check=False).returncode != 0
    uid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    sql(f"insert into auth.users (id) values ('{uid}'); insert into auth.sessions values ('{sid}','{uid}',null);")
    active = f"set role service_role; select public.workbench_session_active('{sid}','{uid}');"
    assert sql(active).stdout.strip().endswith('t')
    sql(f"update auth.sessions set not_after=now()-interval '1 minute' where id='{sid}';")
    assert sql(active).stdout.strip().endswith('f')
    sql(f"update auth.sessions set not_after=null where id='{sid}'; update auth.users set banned_until=now()+interval '1 hour' where id='{uid}';")
    assert sql(active).stdout.strip().endswith('f')
    sql(f"update auth.users set banned_until=null,deleted_at=now() where id='{uid}';")
    assert sql(active).stdout.strip().endswith('f')


def test_signup_hook_exact_domain_and_private_media(sql):
    for email, allowed in [('person@utexas.edu', True), ('person@UTEXAS.EDU', True),
                           ('person@evilutexas.edu', False), ('person@utexas.edu.evil.com', False),
                           ('person@dept.utexas.edu', False), ('a@b@utexas.edu', False), ('', False)]:
        event = '{"user":{"email":"' + email + '"}}'
        result = sql("set role supabase_auth_admin; select public.workbench_before_user_created('" + event + "') ? 'error';")
        assert result.stdout.strip().endswith('f' if allowed else 't')
    for role in ('anon', 'authenticated', 'service_role'):
        assert sql("set role " + role + "; select public.workbench_before_user_created('{}');", check=False).returncode != 0
    assert sql("select public is false from storage.buckets where id='census-media';").stdout.strip() == 't'
    assert sql("select permissive from pg_policies where policyname='census_media_server_only';").stdout.strip() == 'RESTRICTIVE'


def test_explicit_email_exception_and_approved_subdomains(sql):
    for email, allowed in [('to.baladev@gmail.com', True), ('TO.BALADEV@GMAIL.COM', True),
                           ('someone@gmail.com', False), ('a@eid.utexas.edu', True),
                           ('a@my.utexas.edu', True), ('a@my.utexas.edu.evil.com', False)]:
        event = '{"user":{"email":"' + email + '"}}'
        result = sql("set role supabase_auth_admin; select public.workbench_before_user_created('" + event + "') ? 'error';")
        assert result.stdout.strip().endswith('f' if allowed else 't')
    for role in ('anon', 'authenticated', 'service_role', 'supabase_auth_admin'):
        assert sql('set role ' + role + '; select * from workbench_private.allowed_emails;', check=False).returncode != 0
