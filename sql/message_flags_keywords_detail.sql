SELECT
    vd.id AS document_id,
    COALESCE(vd.source_id, vd.document_key) AS message_ref,
    vd.title,
    vd.metadata ->> 'sender' AS sender,
    (vd.metadata ->> 'sent_at')::timestamptz AS sent_at,
    folder.folder_name,
    COALESCE(flags.flags, ARRAY[]::text[]) AS flags,
    COALESCE(keywords.keywords, ARRAY[]::text[]) AS keywords
FROM vector_documents vd
CROSS JOIN LATERAL (
    SELECT key AS folder_name
    FROM jsonb_each(COALESCE(vd.metadata -> 'folder_flags', '{}'::jsonb))

    UNION

    SELECT key AS folder_name
    FROM jsonb_each(COALESCE(vd.metadata -> 'folder_keywords', '{}'::jsonb))
) AS folder
LEFT JOIN LATERAL (
    SELECT ARRAY_AGG(value ORDER BY ordinality) AS flags
    FROM jsonb_array_elements_text(COALESCE(vd.metadata -> 'folder_flags' -> folder.folder_name, '[]'::jsonb))
        WITH ORDINALITY AS flag(value, ordinality)
) AS flags ON TRUE
LEFT JOIN LATERAL (
    SELECT ARRAY_AGG(value ORDER BY ordinality) AS keywords
    FROM jsonb_array_elements_text(COALESCE(vd.metadata -> 'folder_keywords' -> folder.folder_name, '[]'::jsonb))
        WITH ORDINALITY AS keyword(value, ordinality)
) AS keywords ON TRUE
WHERE vd.source_type = 'email'
ORDER BY sent_at DESC NULLS LAST, message_ref, folder.folder_name;