select * from documents order by total_terms desc 

select p.document_id, count( p.* ) from pages p group by p.document_id order by 2 desc  

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


                        update documents as d set embedding  = ps.embedding from (select document_id as document_id ,   avg( embedding  ) as embedding 
                        from pages p group by p.document_id) as ps( document_id, embedding ) where id = ps.document_id;  


						                        alter table documents add column if not exists size int default 0;



												update documents d set size = sub.x 
                        from ( select p.document_id, sum( length( p.extracted_text ) ) as x from pages p group by p.document_id ) sub
                        where d.id = sub.document_id 




						    alter table document_categories add column if not exists color varchar;


							alter table documents add column if not exists logically_deleted bool default false;


							alter table documents add column if not exists total_terms int default 0; 

							------------ kicked off "uv run fromm_llm_.....py"  -------------------
							select count( * ) from terms;
							select count( * ) from page_terms_llm;



							----------------------- py terms from llm ( w/ multithreading ) finished w/ 5 failed pages, or batches ... not sure. 
							
							                update documents d set total_terms = sub.x 
												from (
								                    select p.document_id as document_id , sum( pt.count  ) as x   from page_terms_llm pt, pages p
								                    where p.id = pt.page_id 
								                    group by p.document_id ) sub 
												where d.id = sub.document_id ;



					insert into document_terms ( document_id, term_id, page_count, raw_count )  
						 select p.document_id as doc_id, ptl.term_id as term_id, count( ptl.page_id ) as page_count, sum( ptl.count ) as raw_count
						 from page_terms_llm ptl, pages p 
						 where 
						 	p.id = ptl.page_id 
							 group by p.document_id, ptl.term_id


						            alter table documents add column if not exists total_pages int default 0;


update documents d set total_pages = sub.x from ( select p.document_id, count ( p.* ) as x from pages p group by document_id  ) sub where d.id = sub.document_id 
