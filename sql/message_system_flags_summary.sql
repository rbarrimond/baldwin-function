WITH extracted_flags AS (
    SELECT
        vd.id AS document_id,
        COALESCE(vd.source_id, vd.document_key) AS message_ref,
        ff.key AS folder_name,
        flag.value AS flag_name
    FROM vector_documents vd
    CROSS JOIN LATERAL jsonb_each(COALESCE(vd.metadata -> 'folder_flags', '{}'::jsonb)) AS ff(key, value)
    CROSS JOIN LATERAL jsonb_array_elements_text(ff.value) AS flag(value)
    WHERE vd.source_type = 'email'
      AND LEFT(flag.value, 1) = '\'
)
SELECT
    COALESCE(folder_name, 'ALL_FOLDERS') AS folder_name,
    flag_name,
    COUNT(*) AS flag_occurrences,
    COUNT(DISTINCT document_id) AS message_count,
    COUNT(DISTINCT message_ref) AS unique_message_refs
FROM extracted_flags
GROUP BY GROUPING SETS (
    (folder_name, flag_name),
    (flag_name)
)
ORDER BY
    folder_name NULLS FIRST,
    message_count DESC,
    flag_name;