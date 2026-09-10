-- Operator must enable public.workbench_before_user_created in Auth > Hooks >
-- Before User Created. Merely defining a function does not enable the hook.
-- Keep this exact-domain allowlist synchronized with WORKBENCH_ALLOWED_DOMAINS.
create table workbench_private.allowed_email_domains (
    domain text primary key check (domain = lower(domain) and domain ~ '^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$')
);
insert into workbench_private.allowed_email_domains values ('utexas.edu');
alter table workbench_private.allowed_email_domains enable row level security;
revoke all on workbench_private.allowed_email_domains from public, anon, authenticated, service_role, supabase_auth_admin;

create function public.workbench_before_user_created(event jsonb)
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
declare
    email text := lower(event->'user'->>'email');
    email_domain text;
begin
    -- Exactly one @, a nonempty local part, and no surrounding whitespace.
    if email is null or email !~ '^[^@[:space:]]+@[^@[:space:]]+$'
       or coalesce((event->'user'->>'is_anonymous')::boolean, false) then
        return jsonb_build_object('error', jsonb_build_object('http_code', 403,
            'message', 'Use an approved university email address.'));
    end if;
    email_domain := split_part(email, '@', 2);
    if not exists (select 1 from workbench_private.allowed_email_domains a where a.domain = email_domain) then
        return jsonb_build_object('error', jsonb_build_object('http_code', 403,
            'message', 'Use an approved university email address.'));
    end if;
    return '{}'::jsonb;
end;
$$;
revoke all on function public.workbench_before_user_created(jsonb) from public, anon, authenticated, service_role;
grant execute on function public.workbench_before_user_created(jsonb) to supabase_auth_admin;

insert into storage.buckets (id, name, public) values ('census-media', 'census-media', false)
on conflict (id) do update set public = false;
-- Restrictive policy also denies this bucket when a project already contains a
-- broad permissive policy for unrelated buckets. Service-role backend bypasses RLS.
create policy census_media_server_only on storage.objects as restrictive
for all to anon, authenticated
using (bucket_id <> 'census-media') with check (bucket_id <> 'census-media');
