select count( * ) from documents d where exists ( select 1 from pages p where p.document_id = d.id );

select * from documents d where d.id in ( 108, 89 )
select * from pages d where d.document_id in ( 108, 89 )

delete from pages p where p.document_id in ( 108, 89 )
delete from documents p where p.id in ( 108, 89 )
select count  ( * ) from pages p where p.extracted_text is null ;


updat



select count( * ) from documents d where not exists ( select 1 from document_terms dt where dt.document_id = d.id  ) 

select count( *) from document_terms;

select * from document_terms limit 10;

select * from 


update pages p set content = null ;
update documents d set content = null;

vacuum full pages 
vacuum full documents;
vacuum full terms
vacuum full document_terms;
analyze



select count ( * ) from pages p where p.embedding is  null 

with averages as ( select document_id ,   avg( embedding  ) from pages p group by p.document_id order by p.document_id  )
update documents d set d.embeddi

where p.document_id = 1


update documents as d set embedding  = ps.embedding from (select document_id as document_id ,   avg( embedding  ) as embedding from pages p group by p.document_id) as ps( document_id, embedding ) where id = ps.document_id  

select * from document_coordinates
select * from document_coordinates 
select dc.category_id, dc.document_id  from document_categories dc order by dc.category_id

select c.label, d.filename from categories c, document_categories dc, documents d 
where c.id = dc.category_id and dc.document_id = d.id order by 1, 2