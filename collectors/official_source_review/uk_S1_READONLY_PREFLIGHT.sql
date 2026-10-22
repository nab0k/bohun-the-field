-- EXP-009 stage 1: read-only check after M1 (one SELECT, one JSON value, no secrets).
SELECT jsonb_pretty(jsonb_build_object(
  'notices_columns', (SELECT jsonb_agg(column_name ORDER BY ordinal_position)
                      FROM information_schema.columns
                      WHERE table_schema = 'bohun_uk_intake' AND table_name = 'notices'),
  'versions_columns', (SELECT jsonb_agg(column_name ORDER BY ordinal_position)
                       FROM information_schema.columns
                       WHERE table_schema = 'bohun_uk_intake' AND table_name = 'notice_versions'),
  'constraints', (SELECT jsonb_agg(c.conrelid::regclass::text || '.' || c.conname ORDER BY 1)
                  FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
                  WHERE n.nspname = 'bohun_uk_intake'),
  'rls', (SELECT jsonb_object_agg(c.relname, c.relrowsecurity)
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'bohun_uk_intake' AND c.relkind = 'r'),
  'policies', (SELECT jsonb_agg(tablename || '.' || policyname || ':' || cmd ORDER BY 1)
               FROM pg_policies WHERE schemaname = 'bohun_uk_intake'),
  'writer_must_be_true', jsonb_build_object(
      'notices_insert_ocid', has_column_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notices', 'ocid', 'INSERT'),
      'notices_insert_cpv', has_column_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notices', 'cpv_codes', 'INSERT'),
      'notices_update_deadline', has_column_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notices', 'tender_deadline', 'UPDATE'),
      'versions_insert_json', has_column_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notice_versions', 'release_json', 'INSERT'),
      'versions_select', has_table_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notice_versions', 'SELECT'),
      'receipts_insert_versions', has_column_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.run_receipts', 'versions_inserted', 'INSERT')),
  'writer_must_be_false', jsonb_build_object(
      'versions_update', has_any_column_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notice_versions', 'UPDATE'),
      'versions_delete', has_table_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notice_versions', 'DELETE'),
      'notices_delete', has_table_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notices', 'DELETE'),
      'notices_update_qa', has_column_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notices', 'qa_status', 'UPDATE'),
      'notices_update_public', has_column_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.notices', 'public_release', 'UPDATE'),
      'checkpoints', has_table_privilege('bohun_uk_ingest_writer', 'bohun_uk_intake.checkpoints', 'SELECT,INSERT,UPDATE,DELETE')),
  'api_roles_must_be_false', (SELECT jsonb_object_agg(r,
      has_schema_privilege(r, 'bohun_uk_intake', 'USAGE')
      OR has_table_privilege(r, 'bohun_uk_intake.notice_versions', 'SELECT,INSERT,UPDATE,DELETE')
      OR has_table_privilege(r, 'bohun_uk_intake.notices', 'SELECT,INSERT,UPDATE,DELETE'))
      FROM unnest(ARRAY['anon', 'authenticated', 'service_role']) AS r),
  'counts', jsonb_build_object(
      'notices', (SELECT count(*) FROM bohun_uk_intake.notices),
      'versions', (SELECT count(*) FROM bohun_uk_intake.notice_versions),
      'receipts', (SELECT count(*) FROM bohun_uk_intake.run_receipts),
      'checkpoints', (SELECT count(*) FROM bohun_uk_intake.checkpoints))
)) AS stage1_preflight;
