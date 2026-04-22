WITH extracted_terms AS (
    SELECT
        vd.id AS document_id,
        COALESCE(vd.source_id, vd.document_key) AS message_ref,
        ff.key AS folder_name,
        flag.value AS term,
        'flag' AS term_type
    FROM vector_documents vd
    CROSS JOIN LATERAL jsonb_each(COALESCE(vd.metadata -> 'folder_flags', '{}'::jsonb)) AS ff(key, value)
    CROSS JOIN LATERAL jsonb_array_elements_text(ff.value) AS flag(value)
    WHERE vd.source_type = 'email'

    UNION ALL

    SELECT
        vd.id AS document_id,
        COALESCE(vd.source_id, vd.document_key) AS message_ref,
        fk.key AS folder_name,
        keyword.value AS term,
        'keyword' AS term_type
    FROM vector_documents vd
    CROSS JOIN LATERAL jsonb_each(COALESCE(vd.metadata -> 'folder_keywords', '{}'::jsonb)) AS fk(key, value)
    CROSS JOIN LATERAL jsonb_array_elements_text(fk.value) AS keyword(value)
    WHERE vd.source_type = 'email'
)
SELECT
    term_type,
    COALESCE(folder_name, 'ALL_FOLDERS') AS folder_name,
    term,
    COUNT(*) AS term_occurrences,
    COUNT(DISTINCT document_id) AS message_count,
    COUNT(DISTINCT message_ref) AS unique_message_refs
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