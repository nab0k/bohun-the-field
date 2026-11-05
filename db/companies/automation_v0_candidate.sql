-- EXP-016 REVIEW CANDIDATE ONLY. NOT EXECUTED. Not a numbered migration.
-- Requires the reviewed companies baseline. No keys, credentials, extensions,
-- public API exposure, scheduler, transport or profile/source loader are installed.
-- Direct private DB caller; disposable PostgreSQL proof is still mandatory.
begin;

create role bohun_companies_owner_v0 nologin nosuperuser nocreatedb nocreaterole nobypassrls;
create role bohun_companies_worker_v0 nologin nosuperuser nocreatedb nocreaterole nobypassrls;
grant usage on schema companies to bohun_companies_owner_v0, bohun_companies_worker_v0;

create function companies._automation_canon(v jsonb) returns text
language plpgsql immutable strict set search_path = '' as $$
declare r text;
begin
  case pg_catalog.jsonb_typeof(v)
    when 'object' then
      select '{'||coalesce(pg_catalog.string_agg(pg_catalog.to_jsonb(k)::text||':'||companies._automation_canon(x),',' order by k collate "C"),'')||'}'
      into r from pg_catalog.jsonb_each(v) e(k,x);
    when 'array' then
      select '['||coalesce(pg_catalog.string_agg(companies._automation_canon(x),',' order by n),'')||']'
      into r from pg_catalog.jsonb_array_elements(v) with ordinality e(x,n);
    else r := v::text;
  end case;
  return r;
end $$;
create function companies._automation_hash(v jsonb) returns text language sql immutable strict
set search_path = '' as $$select pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(companies._automation_canon(v),'UTF8')),'hex')$$;
create function companies._automation_plain(v jsonb) returns jsonb language plpgsql immutable strict
set search_path = '' as $$
declare r jsonb;
begin
 if pg_catalog.jsonb_typeof(v)='object' then
  select coalesce(pg_catalog.jsonb_object_agg(k,companies._automation_plain(x)),'{}') into r
  from pg_catalog.jsonb_each(v) e(k,x) where k<>'_type';
 elsif pg_catalog.jsonb_typeof(v)='array' then
  select coalesce(pg_catalog.jsonb_agg(companies._automation_plain(x) order by n),'[]') into r
  from pg_catalog.jsonb_array_elements(v) with ordinality e(x,n);
 else r:=v; end if;
 return r;
end $$;
create function companies._automation_time(v timestamptz) returns text language sql immutable strict
set search_path = '' as $$select pg_catalog.to_char(v at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')$$;
create function companies._automation_require(ok boolean, code text) returns void language plpgsql
set search_path = '' as $$begin if ok is distinct from true then raise exception using errcode='P1600',message='EXP016_REJECTED',detail=code; end if; end$$;

create table companies.automation_control (
 singleton boolean primary key default true check(singleton),
 enabled boolean not null default false,
 mode text not null default 'dry_run' check(mode in ('dry_run','private_write')),
 policy_hash text check(policy_hash ~ '^[0-9a-f]{64}$'),
 policy_json jsonb not null default '{}',
 code_manifest text check(code_manifest ~ '^[0-9a-f]{64}$'),
 company_ids text[] not null default '{}', scope_all boolean not null default false,
 updated_at timestamptz not null default clock_timestamp()
);
insert into companies.automation_control(singleton) values(true);

create table companies.automation_job (
 job_key text primary key check(job_key ~ '^[0-9a-f]{64}$'),
 company_id text references companies.company(id), section_key text,
 job jsonb not null, policy_hash text not null,
 state text not null check(state in ('READY','LEASED','SUCCEEDED','WAIT_SOURCE','WAIT_QUOTA','NEEDS_REVIEW','FAILED','CANCELLED')),
 not_before timestamptz not null default clock_timestamp(),
 attempts integer not null default 0 check(attempts>=0),
 lease_token uuid, lease_until timestamptz,
 prepared jsonb, result_hash text check(result_hash ~ '^[0-9a-f]{64}$'), receipt jsonb,
 error_code text check(error_code ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 created_at timestamptz not null default clock_timestamp(), updated_at timestamptz not null default clock_timestamp(),
 check((state='LEASED') = (lease_token is not null and lease_until is not null)),
 check(pg_catalog.octet_length(prepared::text)<=4194304),
 check(section_key is null or section_key in ('IDENTITY','CLASSIFICATION','PRODUCTS','TECHNOLOGIES','PROGRAMMES','EVENTS','RELATIONSHIPS','FINANCIALS','GEOGRAPHY','SOURCES'))
);
create index automation_job_due on companies.automation_job(policy_hash,state,not_before,created_at);
create index automation_job_company on companies.automation_job(company_id,section_key);

create table companies.automation_checkpoint (
 checkpoint_key text primary key,
 kind text not null check(kind in ('company','sweep','batch')),
 company_id text references companies.company(id),
 revision bigint not null default 0 check(revision>=0),
 state_json jsonb not null default '{}',
 policy_hash text not null,
 updated_at timestamptz not null default clock_timestamp(),
 check((kind='company') = (company_id is not null)),
 check(pg_catalog.octet_length(state_json::text)<=4194304)
);
create unique index automation_checkpoint_company on companies.automation_checkpoint(company_id) where kind='company';
create index automation_observation_trigger on companies.observation((payload->>'company_id'))
 where source='exp016.a26_trigger';

alter table companies.automation_control enable row level security;
alter table companies.automation_job enable row level security;
alter table companies.automation_checkpoint enable row level security;
revoke all on companies.automation_control,companies.automation_job,companies.automation_checkpoint from public,anon,authenticated,bohun_companies_worker_v0;
grant select on companies.automation_control to bohun_companies_owner_v0;
grant select,insert,update on companies.automation_job,companies.automation_checkpoint to bohun_companies_owner_v0;
create policy automation_control_owner on companies.automation_control for select to bohun_companies_owner_v0 using(true);
-- FOR SHARE in automation_rpc_v0 needs UPDATE on one column and an UPDATE policy.
-- WITH CHECK(false) keeps the row lock but denies any real change by this role.
grant update(updated_at) on companies.automation_control to bohun_companies_owner_v0;
create policy automation_control_owner_lock on companies.automation_control for update to bohun_companies_owner_v0 using(true) with check(false);
create policy automation_job_owner on companies.automation_job to bohun_companies_owner_v0 using(true) with check(true);
create policy automation_checkpoint_owner on companies.automation_checkpoint to bohun_companies_owner_v0 using(true) with check(true);

grant select on companies.company,companies.profile_source,companies.profile_claim,companies.profile_claim_source,
 companies.g4_section_review,companies.observation to bohun_companies_owner_v0;
grant update(country,website,updated_at) on companies.company to bohun_companies_owner_v0;
grant insert on companies.profile_claim,companies.profile_claim_source,companies.observation to bohun_companies_owner_v0;
grant insert,update on companies.g4_section_review to bohun_companies_owner_v0;
-- Resolve only these two baseline-owned sequences from the catalog during future proof.
do $grant_sequences$
declare tbl text; seq text;
begin
 foreach tbl in array array['companies.profile_claim','companies.observation'] loop
  seq:=pg_catalog.pg_get_serial_sequence(tbl,'id');
  if seq is null then raise exception 'BASELINE_SEQUENCE_MISSING'; end if;
  execute pg_catalog.format('grant usage,select on sequence %s to bohun_companies_owner_v0',seq);
 end loop;
end $grant_sequences$;
create policy automation_company_owner on companies.company to bohun_companies_owner_v0 using(true) with check(true);
create policy automation_source_owner on companies.profile_source for select to bohun_companies_owner_v0 using(true);
create policy automation_claim_owner on companies.profile_claim to bohun_companies_owner_v0 using(true) with check(public_release=false and qa_status='HOLD');
create policy automation_claim_source_owner on companies.profile_claim_source to bohun_companies_owner_v0 using(true) with check(true);
create policy automation_section_owner on companies.g4_section_review to bohun_companies_owner_v0 using(true) with check(public_release=false);
create policy automation_observation_owner on companies.observation to bohun_companies_owner_v0 using(true)
 with check(source='exp016.a26_trigger');

create function companies._automation_job_key(j jsonb) returns text language plpgsql immutable
set search_path = '' as $$
begin
 perform companies._automation_require(j->>'_type'='JobSpec' and j->>'kind' in ('ENRICH_SECTION','DISCOVER_SOURCE')
  and j->>'initial_state' in ('READY','NEEDS_REVIEW','CANCELLED') and j->>'input_hash' ~ '^[0-9a-f]{64}$'
  and j->>'policy_hash' ~ '^[0-9a-f]{64}$' and pg_catalog.length(j->>'revision') between 1 and 256,'JOB_INVALID');
 perform companies._automation_require((j->>'kind'='ENRICH_SECTION' and j->>'company_id' is not null and
  j->>'section' in ('IDENTITY','CLASSIFICATION','PRODUCTS','TECHNOLOGIES','PROGRAMMES','EVENTS','RELATIONSHIPS','FINANCIALS','GEOGRAPHY','SOURCES'))
  or (j->>'kind'='DISCOVER_SOURCE' and j->>'company_id' is null and j->>'section' is null and j->>'source_ref' is not null),'JOB_SCOPE_INVALID');
 return companies._automation_hash(pg_catalog.jsonb_build_array(j->'policy_hash',j->'policy_version',j->'kind',j->'company_id',j->'section',j->'source_ref',j->'input_hash',j->'revision'));
end $$;

-- Admission hooks are deliberately NOT installed here. Their trusted implementations
-- must be reviewed with the real reader/native/archive integration, never supplied by
-- the worker. Missing hook fails closed, not as an empty company/profile/source.
-- automation_profile_read_v0(text[]) -> {source_revision,cards,contexts,section_states}
-- automation_source_read_v0(text,text,text,text) -> {text_hash,binding,admitted,verified_content_hashes}
create function companies._automation_reader(ids text[]) returns jsonb language plpgsql
set search_path = '' as $$
declare r jsonb;
begin
 perform companies._automation_require(pg_catalog.to_regprocedure('companies.automation_profile_read_v0(text[])') is not null,'PROFILE_READER_NOT_READY');
 execute 'select companies.automation_profile_read_v0($1)' into r using ids;
 perform companies._automation_require(r->>'source_revision' ~ '^[0-9a-f]{64}$'
  and pg_catalog.jsonb_typeof(r->'cards')='array' and pg_catalog.jsonb_typeof(r->'contexts')='object'
  and pg_catalog.jsonb_typeof(r->'section_states')='array','PROFILE_READER_NOT_READY');
 return r;
end $$;

create function companies._automation_snapshot(ids text[], ph text, reader jsonb) returns jsonb
language plpgsql set search_path = '' as $$
declare cp jsonb; ks jsonb; seen jsonb; inputs jsonb; hashes jsonb; rev jsonb;
begin
 select coalesce(pg_catalog.jsonb_agg(job_key order by job_key),'[]') into ks from companies.automation_job where company_id=any(ids);
 select coalesce(pg_catalog.jsonb_agg(source_key order by source_key),'[]') into seen from companies.observation
 where source='exp016.a26_trigger' and payload->>'company_id'=any(ids);
 perform companies._automation_require(pg_catalog.jsonb_array_length(ks)<=5000 and pg_catalog.jsonb_array_length(seen)<=500,'SNAPSHOT_LIMIT');
 select coalesce(pg_catalog.jsonb_agg(e.x order by e.x->>'company_id',e.x->>'section'),'[]') into inputs
 from companies.automation_checkpoint c cross join lateral pg_catalog.jsonb_array_elements(coalesce(c.state_json->'section_inputs','[]')) e(x)
 where c.kind='company' and c.company_id=any(ids);
 select coalesce(pg_catalog.jsonb_agg(e.x order by e.x->>'company_id',e.x->>'section'),'[]') into hashes
 from companies.automation_checkpoint c cross join lateral pg_catalog.jsonb_array_elements(coalesce(c.state_json->'profile_hashes','[]')) e(x)
 where c.kind='company' and c.company_id=any(ids);
 cp:=pg_catalog.jsonb_build_object('existing_keys',ks,'seen_triggers',seen,'section_inputs',inputs,'profile_hashes',hashes,'section_states',reader->'section_states');
 rev:=pg_catalog.jsonb_build_array(ph,ids,reader->'source_revision',cp,
  (select coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) order by c.id),'[]') from companies.company c where id=any(ids)),
  (select coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) order by c.id),'[]') from companies.profile_claim c where company_id=any(ids)),
  (select coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) order by c.id),'[]') from companies.profile_source c where company_id=any(ids)),
  (select coalesce(pg_catalog.jsonb_agg(pg_catalog.to_jsonb(c) order by c.company_id,c.section_key),'[]') from companies.g4_section_review c where company_id=any(ids)),
  (select coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(c.company_id,c.revision,c.state_json) order by c.company_id),'[]') from companies.automation_checkpoint c where kind='company' and company_id=any(ids)));
 return pg_catalog.jsonb_build_object('revision',companies._automation_hash(rev),'policy_hash',ph,'company_ids',pg_catalog.to_jsonb(ids),'cards',reader->'cards','checkpoint',cp);
end $$;

create function companies._automation_changes_hash(ch jsonb) returns text language plpgsql immutable
set search_path = '' as $$
declare rows jsonb:='[]'; h jsonb; p jsonb; ev jsonb; item jsonb; content text;
begin
 for h in select value from pg_catalog.jsonb_array_elements(ch->'claims') loop
  p:=h->'proposal';
  content:=companies._automation_hash(pg_catalog.jsonb_build_array(p->'company_id',p->'section',p->'field_path',p->'value_json',companies._automation_plain(p->'qualifiers')));
  ev:='[]';
  for item in select value from pg_catalog.jsonb_array_elements(p->'evidence') loop
   ev:=ev||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_array(item#>'{source,source_ref}',item#>'{source,url}',item->'source_hash',item->'start',item->'end',item->'binding',item->'company_id'));
  end loop;
  rows:=rows||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_array(p->'claim_ref',content,h->'assessment',ev));
 end loop;
 return companies._automation_hash(pg_catalog.jsonb_build_array(ch->'company_id',ch->'section',ch->'base_fingerprint',ch->'policy_hash',rows,
  companies._automation_plain(ch->'fills'),companies._automation_plain(ch->'issues'),ch->'coverage'));
end $$;

create function companies.automation_rpc_v0(action text, request jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare ctl companies.automation_control%rowtype; j companies.automation_job%rowtype;
 a jsonb:=request->'args'; outdata jsonb; ids text[]; cid text; reader jsonb; snap jsonb;
 item jsonb; x jsonb; ch jsonb; h jsonb; p jsonb; ev jsonb; src jsonb; admission jsonb; ctx jsonb; viewj jsonb;
 kh text; content text; source_id bigint; claim_id bigint; resultstate text; token uuid; t timestamptz:=clock_timestamp();
 batchbody jsonb; checkpoint jsonb; newstate jsonb; n integer; verified boolean; sweep_after text;
 sweep_revision bigint; expected_ids text[]; expected_next text; section_input text;
begin
 perform companies._automation_require(session_user='bohun_companies_worker_v0','CALLER_DENIED');
 perform companies._automation_require(pg_catalog.jsonb_typeof(request)='object' and request->'wire_version'='1'
  and pg_catalog.jsonb_typeof(a)='object' and pg_catalog.octet_length(request::text)<=4194304,'WIRE_INVALID');
 if action in ('status','read_current','read_registry_page','context','read_prepared','lookup_result','lookup_batch') then
  select * into strict ctl from companies.automation_control where singleton;
 else
  select * into strict ctl from companies.automation_control where singleton for share;
 end if;
 -- Locks may have waited: never use the pre-wait clock to authorize a lease.
 t:=clock_timestamp();
 if action='status' then outdata:=pg_catalog.jsonb_build_object('enabled',ctl.enabled,'mode',ctl.mode);
 else
  perform companies._automation_require(ctl.enabled and ctl.policy_hash=request->>'policy_hash'
   and ctl.code_manifest is not null and companies._automation_hash(ctl.policy_json)=ctl.policy_hash,'POLICY_DISABLED');
  perform companies._automation_require(pg_catalog.current_setting('transaction_isolation')='serializable'
   or (action in ('read_current','read_registry_page','lookup_result','lookup_batch')
    and pg_catalog.current_setting('transaction_isolation')='repeatable read'),'SERIALIZABLE_REQUIRED');
  if action in ('lookup_result','lookup_batch') then
   if action='lookup_result' then select receipt into outdata from companies.automation_job where job_key=a->>'job_key' and result_hash=a->>'result_hash';
   else select state_json into outdata from companies.automation_checkpoint where checkpoint_key='batch:'||(a->>'batch_key') and policy_hash=ctl.policy_hash; end if;
  elsif action in ('read_current','read_registry_page','commit_batch','plan_page') then
   if action='read_registry_page' then
    perform companies._automation_require((a->>'limit')::integer between 1 and 100,'REGISTRY_LIMIT_INVALID');
    select state_json->>'after',revision into sweep_after,sweep_revision from companies.automation_checkpoint where checkpoint_key='sweep:registry';
    sweep_revision:=coalesce(sweep_revision,0);
    perform companies._automation_require(a->>'after' is null or a->>'after'=sweep_after,'REGISTRY_CURSOR_CHANGED');
    -- Never infer legal identity from status/flags: only the admitted reader supplies CompanySnapshot.
    select pg_catalog.array_agg(id order by id) into ids from (select id from companies.company
     where (sweep_after is null or id>sweep_after) and (ctl.scope_all or id=any(ctl.company_ids))
     order by id limit (a->>'limit')::integer+1) q;
    ids:=coalesce(ids,'{}'); n:=pg_catalog.cardinality(ids); ids:=ids[1:(a->>'limit')::integer];
    if n=0 then outdata:=pg_catalog.jsonb_build_object('companies','[]'::jsonb,'checkpoint',sweep_after,'has_more',false,'revision',null,'after',sweep_after,
     'state','{"existing_keys":[],"seen_triggers":[],"section_inputs":[],"profile_hashes":[],"section_states":[]}'::jsonb);
    else reader:=companies._automation_reader(ids);
     snap:=companies._automation_snapshot(ids,ctl.policy_hash,reader);
     outdata:=pg_catalog.jsonb_build_object('companies',(select pg_catalog.jsonb_agg(v->'company' order by v#>>'{company,company_id}') from pg_catalog.jsonb_array_elements(reader->'cards') e(v)),
      'checkpoint',ids[pg_catalog.cardinality(ids)],'has_more',n>(a->>'limit')::integer,
      'revision',companies._automation_hash(pg_catalog.jsonb_build_array(snap->'revision',sweep_revision)),
      'state',snap->'checkpoint','after',sweep_after);
    end if;
   else
    select pg_catalog.array_agg(v order by v) into ids from pg_catalog.jsonb_array_elements_text(a->'company_ids') e(v);
    perform companies._automation_require(pg_catalog.cardinality(ids) between 1 and case when action='plan_page' then 100 else 10 end
     and (select count(distinct v)=count(*) from pg_catalog.unnest(ids) e(v)),'COMPANY_SET_INVALID');
    perform companies._automation_require(ctl.scope_all or ids<@ctl.company_ids,'SCOPE_DENIED');
    if action<>'read_current' then perform 1 from companies.company where id=any(ids) order by id for update; end if;
    perform companies._automation_require((select count(*) from companies.company where id=any(ids))=pg_catalog.cardinality(ids),'COMPANY_MISSING');
    if action<>'read_current' then
     perform 1 from companies.automation_checkpoint where kind='company' and company_id=any(ids) order by company_id for update;
    end if;
    reader:=companies._automation_reader(ids); snap:=companies._automation_snapshot(ids,ctl.policy_hash,reader);
    if action='plan_page' then
     select revision into sweep_revision from companies.automation_checkpoint where checkpoint_key='sweep:registry';
     snap:=pg_catalog.jsonb_set(snap,'{revision}',pg_catalog.to_jsonb(companies._automation_hash(
      pg_catalog.jsonb_build_array(snap->'revision',coalesce(sweep_revision,0)))));
    end if;
    if action='read_current' then outdata:=snap;
    else
     batchbody:=a-'batch_key';
     perform companies._automation_require(companies._automation_hash(batchbody)=a->>'batch_key','BATCH_KEY_INVALID');
     select state_json into outdata from companies.automation_checkpoint where checkpoint_key='batch:'||(a->>'batch_key');
     if outdata is null then
      if a->>'revision' is distinct from snap->>'revision' then outdata:='{"state":"CONFLICT"}';
      else
       if action='plan_page' then
        insert into companies.automation_checkpoint(checkpoint_key,kind,policy_hash) values('sweep:registry','sweep',ctl.policy_hash) on conflict do nothing;
        select state_json->>'after' into sweep_after from companies.automation_checkpoint where checkpoint_key='sweep:registry' for update;
        perform companies._automation_require(sweep_after is not distinct from a->>'sweep_after','REGISTRY_CURSOR_CHANGED');
        select pg_catalog.array_agg(id order by id) into expected_ids from (select id from companies.company
         where (sweep_after is null or id>sweep_after) and (ctl.scope_all or id=any(ctl.company_ids))
         order by id limit pg_catalog.cardinality(ids)+1) q;
        expected_next:=case when pg_catalog.cardinality(expected_ids)>pg_catalog.cardinality(ids) then ids[pg_catalog.cardinality(ids)] else null end;
        perform companies._automation_require(ids=expected_ids[1:pg_catalog.cardinality(ids)]
         and (a->>'sweep_next') is not distinct from expected_next,'REGISTRY_PAGE_CHANGED');
       end if;
       checkpoint:=a->'checkpoint';
       perform companies._automation_require(pg_catalog.jsonb_array_length(a->'jobs')<=1000
        and pg_catalog.jsonb_array_length(checkpoint->'existing_keys')<=5000 and pg_catalog.jsonb_array_length(checkpoint->'seen_triggers')<=500,'SNAPSHOT_LIMIT');
       perform companies._automation_require((checkpoint->'existing_keys') @> (snap#>'{checkpoint,existing_keys}')
        and (checkpoint->'seen_triggers') @> (snap#>'{checkpoint,seen_triggers}'),'CHECKPOINT_HISTORY_LOST');
       for item in select value from pg_catalog.jsonb_array_elements(a->'jobs') loop
        kh:=companies._automation_job_key(item);
        perform companies._automation_require(item->>'company_id'=any(ids) and item->>'policy_hash'=ctl.policy_hash
         and item->>'policy_version'=ctl.policy_json->>'version' and checkpoint->'existing_keys' ? kh,'JOB_SCOPE_INVALID');
        insert into companies.automation_job(job_key,company_id,section_key,job,policy_hash,state)
         values(kh,item->>'company_id',item->>'section',item,ctl.policy_hash,item->>'initial_state') on conflict(job_key) do nothing;
       end loop;
       perform companies._automation_require(not exists(select 1 from pg_catalog.jsonb_array_elements_text(checkpoint->'existing_keys') k(v)
        where not exists(select 1 from companies.automation_job where job_key=k.v)),'UNBACKED_JOB_KEY');
       for item in select value from pg_catalog.jsonb_array_elements(a->'triggers') loop
        perform companies._automation_require(item->>'company_id'=any(ids) and item->>'content_hash' ~ '^[0-9a-f]{64}$'
         and pg_catalog.jsonb_typeof(item->'sections')='array' and item->>'key'=companies._automation_hash(pg_catalog.jsonb_build_array(item->'company_id',item->'origin_id',item->'content_hash',item->'sections')),'TRIGGER_INVALID');
        insert into companies.observation(source,source_key,content_hash,fetched_at,payload)
         values('exp016.a26_trigger',item->>'key',companies._automation_hash(item-'key'),t,item-'key')
         on conflict(source,source_key,content_hash) do nothing;
       end loop;
       perform companies._automation_require(not exists(select 1 from pg_catalog.jsonb_array_elements_text(checkpoint->'seen_triggers') k(v)
        where not exists(select 1 from companies.observation where source='exp016.a26_trigger' and source_key=k.v)),'UNBACKED_TRIGGER_KEY');
       foreach cid in array ids loop
        newstate:=pg_catalog.jsonb_build_object(
         'section_inputs',(select coalesce(pg_catalog.jsonb_agg(v order by v->>'section'),'[]') from pg_catalog.jsonb_array_elements(checkpoint->'section_inputs') e(v) where v->>'company_id'=cid),
         'profile_hashes',(select coalesce(pg_catalog.jsonb_agg(v order by v->>'section'),'[]') from pg_catalog.jsonb_array_elements(checkpoint->'profile_hashes') e(v) where v->>'company_id'=cid));
        insert into companies.automation_checkpoint(checkpoint_key,kind,company_id,state_json,policy_hash)
         values('company:'||cid,'company',cid,newstate,ctl.policy_hash) on conflict(checkpoint_key) do update
         set state_json=excluded.state_json,revision=automation_checkpoint.revision+1,updated_at=t
         where automation_checkpoint.state_json is distinct from excluded.state_json;
       end loop;
       outdata:=pg_catalog.jsonb_build_object('state','APPLIED','batch_key',a->>'batch_key');
       insert into companies.automation_checkpoint(checkpoint_key,kind,state_json,policy_hash)
        values('batch:'||(a->>'batch_key'),'batch',outdata,ctl.policy_hash);
       if action='plan_page' then
        insert into companies.automation_checkpoint(checkpoint_key,kind,state_json,policy_hash)
         values('sweep:registry','sweep',pg_catalog.jsonb_build_object('after',a->'sweep_next'),ctl.policy_hash)
         on conflict(checkpoint_key) do update set state_json=excluded.state_json,revision=automation_checkpoint.revision+1,updated_at=t;
       end if;
      end if;
     end if;
    end if;
   end if;
  elsif action='claim' then
   perform companies._automation_require((a->>'lease_seconds')::integer=(ctl.policy_json->>'lease_seconds')::integer,'POLICY_CHANGED');
   select * into j from companies.automation_job where policy_hash=ctl.policy_hash
    and (ctl.scope_all or company_id=any(ctl.company_ids)) and attempts<(ctl.policy_json->>'max_attempts')::integer
    and ((state in ('READY','WAIT_SOURCE','WAIT_QUOTA') and not_before<=t) or (state='LEASED' and lease_until<=t))
    order by not_before,created_at,job_key limit 1 for update skip locked;
   if found then
    token:=pg_catalog.gen_random_uuid();
    update companies.automation_job set state='LEASED',attempts=attempts+1,lease_token=token,
     lease_until=t+pg_catalog.make_interval(secs=>(ctl.policy_json->>'lease_seconds')::integer),updated_at=t
     where job_key=j.job_key returning * into j;
    outdata:=pg_catalog.jsonb_build_object('_type','Lease','job',j.job,'token',j.lease_token::text,'expires_at',companies._automation_time(j.lease_until),'attempt',j.attempts);
   end if;
  elsif action in ('context','read_prepared','save_prepared','renew','defer','commit_result') then
   select * into j from companies.automation_job where job_key=a->>'job_key' for update;
   t:=clock_timestamp();
   perform companies._automation_require(found and j.policy_hash=ctl.policy_hash
    and (ctl.scope_all or j.company_id=any(ctl.company_ids)),'JOB_SCOPE_INVALID');
   if action='commit_result' and j.receipt is not null and j.result_hash=a->>'result_hash' then outdata:=j.receipt;
   else
    perform companies._automation_require(j.state='LEASED' and j.lease_token::text=a->>'token' and j.lease_until>t,'LEASE_LOST');
    if action='renew' then
     update companies.automation_job set lease_until=t+pg_catalog.make_interval(secs=>(ctl.policy_json->>'lease_seconds')::integer),updated_at=t
      where job_key=j.job_key returning * into j;
     outdata:=pg_catalog.jsonb_build_object('_type','Lease','job',j.job,'token',j.lease_token::text,'expires_at',companies._automation_time(j.lease_until),'attempt',j.attempts);
    elsif action='defer' then
     perform companies._automation_require(a->>'state' in ('WAIT_SOURCE','WAIT_QUOTA','NEEDS_REVIEW','FAILED')
      and a->>'reason' ~ '^[A-Z][A-Z0-9_]{0,79}$','DEFER_INVALID');
     perform companies._automation_require((a->>'state' in ('WAIT_SOURCE','WAIT_QUOTA') and (a->>'not_before')::timestamptz>t)
      or (a->>'state' in ('NEEDS_REVIEW','FAILED') and a->>'not_before' is null),'DEFER_TIME_INVALID');
     update companies.automation_job set state=a->>'state',error_code=a->>'reason',not_before=coalesce((a->>'not_before')::timestamptz,t),
      lease_token=null,lease_until=null,updated_at=t where job_key=j.job_key;
     outdata:=pg_catalog.jsonb_build_object('state',a->>'state');
    elsif action='read_prepared' then outdata:=j.prepared;
    else
     perform 1 from companies.company where id=j.company_id for update;
     reader:=companies._automation_reader(array[j.company_id]); ctx:=reader#>array['contexts',j.company_id]; viewj:=ctx->'view';
     t:=clock_timestamp();
     perform companies._automation_require(j.lease_until>t,'LEASE_EXPIRED');
     -- A section input saved atomically with its jobs outranks the company-wide reader value.
     select e.v->>'value' into section_input from companies.automation_checkpoint c
      cross join lateral pg_catalog.jsonb_array_elements(coalesce(c.state_json->'section_inputs','[]')) e(v)
      where c.checkpoint_key='company:'||j.company_id and e.v->>'section'=j.section_key;
     perform companies._automation_require(ctx->>'_type'='WorkContext' and coalesce(section_input,ctx->>'input_hash')=j.job->>'input_hash'
      and viewj->>'company_id'=j.company_id and viewj->'protected'='false','INPUT_VERSION_OR_SCOPE_CHANGED');
     ctx:=pg_catalog.jsonb_set(ctx,'{input_hash}',j.job->'input_hash');
     if action='context' then outdata:=ctx;
     else
      ch:=case when action='save_prepared' then a->'changes' else j.prepared end;
      perform companies._automation_require(ch->>'_type'='ChangeSet' and ch->>'company_id'=j.company_id
       and ch->>'section'=j.section_key and ch->>'policy_hash'=ctl.policy_hash
       and ch->>'base_fingerprint'=companies._automation_hash(companies._automation_plain(viewj))
       and companies._automation_changes_hash(ch)=a->>'result_hash'
       and (ch->>'reviewed_at')::timestamptz<=t,'STALE_CONTEXT');
      if action='save_prepared' then
       perform companies._automation_require(j.prepared is null or j.prepared=ch,'PREPARED_RESULT_IMMUTABLE');
       update companies.automation_job set prepared=ch,result_hash=a->>'result_hash',updated_at=t where job_key=j.job_key;
       outdata:=pg_catalog.jsonb_build_object('result_hash',a->>'result_hash');
      else
       perform companies._automation_require(ctl.mode='private_write','PRIVATE_WRITER_DISABLED');
       perform companies._automation_require(pg_catalog.to_regprocedure('companies.automation_source_read_v0(text,text,text,text)') is not null,'SOURCE_READER_NOT_READY');
       perform companies._automation_require(pg_catalog.jsonb_array_length(ch->'claims')<=(ctl.policy_json->>'max_claims')::integer
        and ch->>'coverage' in ('HAS_HOLD_CLAIMS','RESEARCHED_NOT_FOUND','HAS_APPROVED_CLAIMS'),'CLAIMS_INVALID');
       for h in select value from pg_catalog.jsonb_array_elements(ch->'claims') loop
        p:=h->'proposal';
        perform companies._automation_require(h->>'qa_status'='HOLD' and h->'public_release'='false'
         and p->>'company_id'=j.company_id and p->>'section'=j.section_key and pg_catalog.split_part(p->>'field_path','/',2)=pg_catalog.lower(j.section_key)
         and h->>'assessment' in ('registry_supported','company_claim','cross_source','inferred_needs_review')
         and pg_catalog.jsonb_array_length(p->'evidence') between 1 and 10,'CLAIM_SCOPE_INVALID');
        content:=companies._automation_hash(pg_catalog.jsonb_build_array(p->'company_id',p->'section',p->'field_path',p->'value_json',companies._automation_plain(p->'qualifiers')));
        perform companies._automation_require(not exists(select 1 from companies.profile_claim where company_id=j.company_id and content_sha256=content and qa_status='REJECTED'),'REJECTED_CONTENT');
        perform companies._automation_require((p->>'value_json')::jsonb<>'null'::jsonb and (p->>'value_json')::jsonb<>'""'::jsonb,'EMPTY_CLAIM');
        verified:=false;
        for ev in select value from pg_catalog.jsonb_array_elements(p->'evidence') loop
         src:=ev->'source';
         perform companies._automation_require(ev->>'company_id'=j.company_id and ev->'observed'=p->'qualifiers'
          and ev->'observed_field_path'=p->'field_path' and ev->'observed_value_json'=p->'value_json'
          and ev->>'binding' in ('identifier','official_name_country_domain','saved_binding')
          and (src->>'captured_at')::timestamptz<=t
          and ev->>'source_hash'=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(src->>'text','UTF8')),'hex')
          and (ev->>'start')::integer>=0 and (ev->>'end')::integer>(ev->>'start')::integer
          and pg_catalog.substring(src->>'text',(ev->>'start')::integer+1,(ev->>'end')::integer-(ev->>'start')::integer)=ev->>'quote','EVIDENCE_INVALID');
         execute 'select companies.automation_source_read_v0($1,$2,$3,$4)' into admission
          using j.company_id,src->>'source_ref',src->>'url',ev->>'source_hash';
         perform companies._automation_require(admission->'admitted'='true' and admission->>'text_hash'=ev->>'source_hash'
          and admission->>'binding'=ev->>'binding','SOURCE_ADMISSION_REQUIRED');
         verified:=verified or coalesce((admission->'verified_content_hashes') ? content,false);
         select id into source_id from companies.profile_source where company_id=j.company_id
          and source_ref=src->>'source_ref' and source_url=src->>'url';
         perform companies._automation_require(source_id is not null,'SOURCE_RECEIPT_MISSING');
        end loop;
        insert into companies.profile_claim(company_id,claim_ref,field_path,claim_value,assessment,review_note,content_sha256,qa_status,public_release)
         values(j.company_id,p->>'claim_ref',p->>'field_path',(p->>'value_json')::jsonb,h->>'assessment',
          companies._automation_canon(pg_catalog.jsonb_build_object('qualifiers',p->'qualifiers','evidence',p->'evidence','base_fill_admitted',verified)),content,'HOLD',false)
         on conflict(company_id,claim_ref,content_sha256) do nothing;
        select id into strict claim_id from companies.profile_claim where company_id=j.company_id and claim_ref=p->>'claim_ref' and content_sha256=content;
        for ev in select value from pg_catalog.jsonb_array_elements(p->'evidence') loop
         insert into companies.profile_claim_source(company_id,claim_id,source_id)
          select j.company_id,claim_id,id from companies.profile_source where company_id=j.company_id
          and source_ref=ev#>>'{source,source_ref}' and source_url=ev#>>'{source,url}' on conflict do nothing;
        end loop;
       end loop;
       -- Field writes require explicit authoritative ownership. No unknown-as-machine.
       for item in select value from pg_catalog.jsonb_array_elements(ch->'fills') loop
        perform companies._automation_require(item->>'field_path' in ('/identity/country','/identity/website')
         and ctl.policy_json->'fill_paths' ? (item->>'field_path'),'FILL_DENIED');
        select v->1 into x from pg_catalog.jsonb_array_elements(viewj->'fields') e(v) where v->>0=item->>'field_path';
        perform companies._automation_require(x->>'owner'='machine' and x->'known'='false','OWNERSHIP_NOT_ADMITTED');
        perform companies._automation_require(exists(select 1 from companies.profile_claim
         where company_id=j.company_id and content_sha256=item->>'supporting_hash'
          and (review_note::jsonb)->'base_fill_admitted'='true'),'AUTO_FILL_NOT_VERIFIED');
        perform companies._automation_require(exists(select 1 from pg_catalog.jsonb_array_elements(ch->'claims') q(v)
         where companies._automation_hash(pg_catalog.jsonb_build_array(v#>'{proposal,company_id}',v#>'{proposal,section}',v#>'{proposal,field_path}',v#>'{proposal,value_json}',companies._automation_plain(v#>'{proposal,qualifiers}')))=item->>'supporting_hash'
         and v#>>'{proposal,field_path}'=item->>'field_path' and v#>>'{proposal,value_json}'=item->>'value_json'
         and v#>>'{proposal,qualifiers,polarity}'='positive' and v#>>'{proposal,qualifiers,modality}'='actual'),'FILL_UNSUPPORTED');
        if item->>'field_path'='/identity/country' then
         perform companies._automation_require((item->>'value_json')::jsonb#>>'{}' ~ '^[A-Z]{2}$'
          and ((item->>'value_json')::jsonb#>>'{}') not in ('RU','CN'),'COUNTRY_INVALID');
         update companies.company set country=(item->>'value_json')::jsonb#>>'{}',updated_at=t where id=j.company_id and country is null;
        else
         perform companies._automation_require((item->>'value_json')::jsonb#>>'{}' ~ '^https://[^/@[:space:]]+','WEBSITE_INVALID');
         update companies.company set website=(item->>'value_json')::jsonb#>>'{}',updated_at=t where id=j.company_id and website is null;
        end if;
        perform companies._automation_require(found,'STALE_CONTEXT');
       end loop;
       if j.section_key='GEOGRAPHY' then
        perform companies._automation_require(not exists(select 1 from companies.company where id=j.company_id and (country='UA' or country is null))
         or not exists(select 1 from pg_catalog.jsonb_array_elements(ch->'claims') q(v) where v#>>'{proposal,field_path}'<>'/geography/country'),'SENSITIVE_GEOGRAPHY');
       end if;
       -- Negative-search / approved coverage cannot be self-certified by an empty payload.
       if pg_catalog.jsonb_array_length(ch->'claims')=0 then
        perform companies._automation_require(reader#>array['research_admitted',j.company_id,j.section_key]='true','RESEARCH_RECEIPT_REQUIRED');
       end if;
       perform companies._automation_require(ch->>'coverage'<>'HAS_APPROVED_CLAIMS' or exists(
        select 1 from pg_catalog.jsonb_array_elements(viewj->'section_states') e(v)
        where v->>0=j.section_key and v->>1='HAS_APPROVED_CLAIMS'),'APPROVED_COVERAGE_NOT_ADMITTED');
       insert into companies.g4_section_review(company_id,section_key,coverage_state,reviewed_at,review_note,qa_status,public_release,updated_at)
        values(j.company_id,j.section_key,ch->>'coverage',t,'EXP016 private automation','HOLD',false,t)
        on conflict(company_id,section_key) do update set coverage_state=case when g4_section_review.coverage_state='HAS_APPROVED_CLAIMS' then g4_section_review.coverage_state else excluded.coverage_state end,
         reviewed_at=t,updated_at=t;
       resultstate:=case when pg_catalog.jsonb_array_length(ch->'issues')>0 then 'NEEDS_REVIEW' else 'SUCCEEDED' end;
       perform companies._automation_require(j.lease_until>clock_timestamp(),'LEASE_EXPIRED');
       outdata:=pg_catalog.jsonb_build_object('_type','CommitReceipt','job_key',j.job_key,'result_hash',j.result_hash,'state',resultstate);
       update companies.automation_job set state=resultstate,receipt=outdata,lease_token=null,lease_until=null,updated_at=t where job_key=j.job_key;
       insert into companies.automation_checkpoint(checkpoint_key,kind,company_id,policy_hash)
        values('company:'||j.company_id,'company',j.company_id,ctl.policy_hash) on conflict(checkpoint_key)
        do update set revision=automation_checkpoint.revision+1,updated_at=t;
      end if;
     end if;
    end if;
   end if;
  else perform companies._automation_require(false,'ACTION_DENIED'); end if;
 end if;
 perform companies._automation_require(pg_catalog.octet_length(coalesce(outdata,'null')::text)<=4194304,'RESPONSE_LIMIT');
 return pg_catalog.jsonb_build_object('wire_version',1,'status','OK','data',coalesce(outdata,'null'));
end $$;

-- Fixed private function boundary. No grants to authenticated/anon/service_role.
revoke all on function companies.automation_rpc_v0(text,jsonb) from public;
alter function companies.automation_rpc_v0(text,jsonb) owner to bohun_companies_owner_v0;
grant execute on function companies.automation_rpc_v0(text,jsonb) to bohun_companies_worker_v0;
revoke all on function companies._automation_canon(jsonb),companies._automation_hash(jsonb),companies._automation_plain(jsonb),companies._automation_time(timestamptz),
 companies._automation_require(boolean,text),companies._automation_job_key(jsonb),companies._automation_reader(text[]),companies._automation_snapshot(text[],text,jsonb),companies._automation_changes_hash(jsonb) from public;
grant execute on function companies._automation_canon(jsonb),companies._automation_hash(jsonb),companies._automation_plain(jsonb),companies._automation_time(timestamptz),
 companies._automation_require(boolean,text),companies._automation_job_key(jsonb),companies._automation_reader(text[]),companies._automation_snapshot(text[],text,jsonb),companies._automation_changes_hash(jsonb) to bohun_companies_owner_v0;
commit;
