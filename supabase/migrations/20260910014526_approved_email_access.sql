-- Explicit personal account exception, plus approved UT email domains.
create table workbench_private.allowed_emails (
    email text primary key check (email = lower(email) and email ~ '^[^@[:space:]]+@[^@[:space:]]+$')
);
alter table workbench_private.allowed_emails enable row level security;
revoke all on workbench_private.allowed_emails from public, anon, authenticated, service_role, supabase_auth_admin;
insert into workbench_private.allowed_emails values ('to.baladev@gmail.com');
insert into workbench_private.allowed_email_domains values ('eid.utexas.edu'), ('my.utexas.edu') on conflict do nothing;

create or replace function public.workbench_before_user_created(event jsonb)
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
    if not exists (select 1 from workbench_private.allowed_email_domains a where a.domain = email_domain)
       and not exists (select 1 from workbench_private.allowed_emails a where a.email = lower(event->'user'->>'email')) then
        return jsonb_build_object('error', jsonb_build_object('http_code', 403,
            'message', 'Use an approved university email address.'));
    end if;
    return '{}'::jsonb;
end;
$$;
revoke all on function public.workbench_before_user_created(jsonb) from public, anon, authenticated, service_role;
grant execute on function public.workbench_before_user_created(jsonb) to supabase_auth_admin;
