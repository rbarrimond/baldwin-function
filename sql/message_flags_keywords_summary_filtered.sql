WITH params AS (
    SELECT
        NULL::timestamptz AS start_at,
        NULL::timestamptz AS end_at,
        NULL::text AS folder_name
),
extracted_terms AS (
    SELECT
        vd.id AS document_id,
        COALESCE(vd.source_id, vd.document_key) AS message_ref,
        ff.key AS folder_name,
        flag.value AS term,
        'flag' AS term_type,
        (vd.metadata ->> 'sent_at')::timestamptz AS sent_at
    FROM vector_documents vd
    CROSS JOIN params p
    CROSS JOIN LATERAL jsonb_each(COALESCE(vd.metadata -> 'folder_flags', '{}'::jsonb)) AS ff(key, value)
    CROSS JOIN LATERAL jsonb_array_elements_text(ff.value) AS flag(value)
    WHERE vd.source_type = 'email'
      AND (p.start_at IS NULL OR (vd.metadata ->> 'sent_at')::timestamptz >= p.start_at)
      AND (p.end_at IS NULL OR (vd.metadata ->> 'sent_at')::timestamptz < p.end_at)
      AND (p.folder_name IS NULL OR ff.key = p.folder_name)

    UNION ALL

    SELECT
        vd.id AS document_id,
        COALESCE(vd.source_id, vd.document_key) AS message_ref,
        fk.key AS folder_name,
        keyword.value AS term,
        'keyword' AS term_type,
        (vd.metadata ->> 'sent_at')::timestamptz AS sent_at
    FROM vector_documents vd
    CROSS JOIN params p
    CROSS JOIN LATERAL jsonb_each(COALESCE(vd.metadata -> 'folder_keywords', '{}'::jsonb)) AS fk(key, value)
    CROSS JOIN LATERAL jsonb_array_elements_text(fk.value) AS keyword(value)
    WHERE vd.source_type = 'email'
      AND (p.start_at IS NULL OR (vd.metadata ->> 'sent_at')::timestamptz >= p.start_at)
      AND (p.end_at IS NULL OR (vd.metadata ->> 'sent_at')::timestamptz < p.end_at)
      AND (p.folder_name IS NULL OR fk.key = p.folder_name)
)
SELECT
    term_type,
    COALESCE(folder_name, 'ALL_FOLDERS') AS folder_name,
    term,
    COUNT(*) AS term_occurrences,
    COUNT(DISTINCT document_id) AS message_count,
    COUNT(DISTINCT message_ref) AS unique_message_refs,
    MIN(sent_at) AS first_seen_sent_at,
    MAX(sent_at) AS last_seen_sent_at
FROM extracted_terms
GROUP BY GROUPING SETS (
    (term_type, folder_name, term),
    (term_type, term)
)
ORDER BY
    term_type,
    folder_name NULLS FIRST,
    message_count DESC,
    term;