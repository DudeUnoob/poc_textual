-- Shared backend state. Browser roles have no direct reads, writes, or RPC access.
-- Apply with the Supabase migration runner as the database owner.
create schema if not exists workbench_private;
revoke all on schema workbench_private from public, anon, authenticated, service_role;

create table workbench_private.documents (
    path text primary key check (path ~ '^[^/]+(/[^/]+)+$' and length(path) <= 2048),
    value jsonb not null check (jsonb_typeof(value) = 'object'),
    updated_at timestamptz not null default now()
);
create index documents_collection_idx
    on workbench_private.documents ((regexp_replace(path, '/[^/]+$', '')));
create table workbench_private.version (
    singleton boolean primary key default true check (singleton),
    revision bigint not null default 0 check (revision >= 0)
);
insert into workbench_private.version (singleton) values (true);
alter table workbench_private.documents enable row level security;
alter table workbench_private.version enable row level security;
revoke all on all tables in schema workbench_private from public, anon, authenticated, service_role;

-- Explicitly qualified references plus fixed search_path prevent object shadowing.
-- Only the backend service role can execute these exposed RPC entrypoints.
create function public.workbench_version()
returns bigint language sql stable security definer set search_path = ''
as $$ select revision from workbench_private.version where singleton; $$;

create function public.workbench_read(document_path text)
returns jsonb language sql stable security definer set search_path = ''
as $$ select value from workbench_private.documents where path = document_path; $$;

create function public.workbench_list(collection_path text)
returns jsonb language sql stable security definer set search_path = ''
as $$
    select coalesce(jsonb_agg(value || jsonb_build_object('id', regexp_replace(path, '^.*/', '')) order by path), '[]'::jsonb)
    from workbench_private.documents
    where regexp_replace(path, '/[^/]+$', '') = collection_path;
$$;

create function public.workbench_commit(expected_version bigint, writes jsonb)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
    current_version bigint;
    item jsonb;
begin
    if expected_version is null or writes is null or jsonb_typeof(writes) <> 'array' then
        raise exception 'Invalid transaction input';
    end if;
    if jsonb_array_length(writes) > 10000 or octet_length(writes::text) > 10000000 then
        raise exception 'Transaction exceeds supported size';
    end if;
    -- All mutation paths serialize through this lock. A comparison before the
    -- lock would allow two stale requests to commit and lose a review decision.
    select revision into current_version from workbench_private.version
        where singleton for update;
    if current_version <> expected_version then
        return false;
    end if;
    for item in select value from jsonb_array_elements(writes)
    loop
        if jsonb_typeof(item) <> 'object' or jsonb_typeof(item->'path') <> 'string'
           or jsonb_typeof(item->'value') <> 'object'
           or item->>'path' is null or item->'value' is null then
            raise exception 'Invalid document write';
        end if;
        insert into workbench_private.documents (path, value)
        values (item->>'path', item->'value')
        on conflict (path) do update set value = excluded.value, updated_at = now();
    end loop;
    if jsonb_array_length(writes) > 0 then
        update workbench_private.version set revision = revision + 1 where singleton;
    end if;
    return true;
end;
$$;

revoke all on function public.workbench_version() from public, anon, authenticated;
revoke all on function public.workbench_read(text) from public, anon, authenticated;
revoke all on function public.workbench_list(text) from public, anon, authenticated;
revoke all on function public.workbench_commit(bigint, jsonb) from public, anon, authenticated;
grant execute on function public.workbench_version() to service_role;
grant execute on function public.workbench_read(text) to service_role;
grant execute on function public.workbench_list(text) to service_role;
grant execute on function public.workbench_commit(bigint, jsonb) to service_role;

create function public.workbench_session_active(p_session_id uuid, p_uid uuid)
returns boolean language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1 from auth.sessions s join auth.users u on u.id = s.user_id
        where s.id = p_session_id and s.user_id = p_uid
          and (s.not_after is null or s.not_after > now())
          and u.deleted_at is null
          and (u.banned_until is null or u.banned_until <= now())
    );
$$;
revoke all on function public.workbench_session_active(uuid, uuid) from public, anon, authenticated;
grant execute on function public.workbench_session_active(uuid, uuid) to service_role;
