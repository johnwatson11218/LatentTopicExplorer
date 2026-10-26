select * from documents



CREATE OR REPLACE PROCEDURE run_enqueue_available_work()
LANGUAGE plpgsql
AS $$
BEGIN
    -- Document-level work
    INSERT INTO pipeline_queue (document_id, step)
    SELECT d.id, 'split'
    FROM documents d
    WHERE NOT EXISTS (SELECT 1 FROM pages p WHERE p.document_id = d.id)
    ON CONFLICT (document_id, step) WHERE page_id IS NULL
    DO NOTHING;

    -- Page-level: extract_text
    INSERT INTO pipeline_queue (document_id, page_id, step)
    SELECT p.document_id, p.id, 'extract_text'
    FROM pages p
    WHERE p.extracted_text IS NULL
    ON CONFLICT (document_id, page_id, step) WHERE page_id IS NOT NULL
    DO NOTHING;

    -- Page-level: embed
    INSERT INTO pipeline_queue (document_id, page_id, step)
    SELECT p.document_id, p.id, 'embed'
    FROM pages p
    WHERE p.extracted_text IS NOT NULL
      AND p.embedding IS NULL
    ON CONFLICT (document_id, page_id, step) WHERE page_id IS NOT NULL
    DO NOTHING;
END;
$$;

call run_enqueue_available_work()

select count( * ) from pages 
select pq.step, pq.status, count( * ) from pipeline_queue pq group by pq.step, pq.status



CREATE DATABASE second_brain_10_25 TEMPLATE second_brain;
