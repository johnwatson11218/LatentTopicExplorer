CREATE OR REPLACE PROCEDURE run_enqueue_pass()
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO pipeline_queue (document_id, step)
    SELECT d.id, 'split'
    FROM documents d
    WHERE NOT EXISTS (SELECT 1 FROM pages p WHERE p.document_id = d.id)
    ON CONFLICT (document_id, step) WHERE page_id IS NULL DO NOTHING;

    INSERT INTO pipeline_queue (document_id, page_id, step)
    SELECT p.document_id, p.id, 'extract_text'
    FROM pages p
    WHERE p.extracted_text IS NULL
    ON CONFLICT (page_id, step) DO NOTHING;

    INSERT INTO pipeline_queue (document_id, page_id, step)
    SELECT p.document_id, p.id, 'embed'
    FROM pages p
    WHERE p.extracted_text IS NOT NULL AND p.embedding IS NULL
    ON CONFLICT (page_id, step) DO NOTHING;

    COMMIT;
END;
$$;