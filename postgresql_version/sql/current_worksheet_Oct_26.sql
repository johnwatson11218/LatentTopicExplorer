select  *  from documents d
where d.id = 1048;
select count ( * ) from pages p where p.document_id = 1048



select count ( * ) from pipeline_queue;
select * from pipeline_queue;

call run_enqueue_pass()

CREATE OR REPLACE PROCEDURE run_enqueue_pass()
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



select count(*) from pages 


select * from pipeline_queue pq where pq.error_msg is not null 

select count( *  ) from documents d where not exists ( select 1 from pages p where p.document_id = d.id )




select length( extracted_text ), count( * ) from pages p group by length( extracted_text ) order by length( extracted_text  ) --  count( * ) desc 



select count( * ) from pages p where p.embedding is   null 
select count( * ) from documents d where d.embedding is null;

select status, count( * ) from pipeline_queue pq where pq.step = 'embed' group by status
delete from pipeline_queue pq where pq.status = 'processing'

and status = 'en'






                        update documents as d set embedding  = ps.embedding from 
							(
								select document_id as document_id ,   avg( embedding  ) as embedding  from pages p group by p.document_id
							) as ps( document_id, embedding ) 
					     where id = ps.document_id;  



alter table pages add column if not exists  page_size int generated always as (length( extracted_text )) stored;

                        alter table documents add column if not exists size int default 0;


						                        update documents d set size = sub.x 
                        from ( select p.document_id, sum( length( p.extracted_text ) ) as x from pages p group by p.document_id ) sub
                        where d.id = sub.document_id 




						                            alter table document_categories add column if not exists color varchar;


            alter table documents add column if not exists logically_deleted bool default false;


			
alter table documents add column if not exists total_terms int default 0; 

select * from page_terms limit 10 ;


update documents d set total_terms = sub.x from (
	select p.document_id as document_id , sum( pt.count  ) as x   from page_terms_llm pt, pages p
	where p.id = pt.page_id 
	group by p.document_id ) sub where d.id = sub.document_id ;



select * from documents d order by d.total_terms desc 
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
select * from document_terms  -- id, document_id, term_id, tf, raw_count, page_count  .... so page_count is. 
		 

					insert into document_terms ( document_id, term_id, page_count, raw_count )  
						 select p.document_id as doc_id, ptl.term_id as term_id, count( ptl.page_id ) as page_count, sum( ptl.count ) as raw_count
						 from page_terms_llm ptl, pages p 
						 where 
						 	p.id = ptl.page_id 
							 group by p.document_id, ptl.term_id
							 limit 10 

select  *  from document_terms;
delete from document_terms;