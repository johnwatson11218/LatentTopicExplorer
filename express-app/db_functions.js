import { Pool } from 'pg';

const client = new Pool({
  user: 'postgres', host: 'postgres', database: 'second_brain', password: 'test_case', port: 5432,
  max: 20,                    // Max connections
  idleTimeoutMillis: 30000,   // Close idle connections after 30s
  connectionTimeoutMillis: 2000 // Fail fast if can't connect
});


async function getTotalDocumentCount() {
  let count = 0;
  const countResult = await client.query("SELECT COUNT(*) as count FROM documents d");
  if (countResult.rows.length > 0) 
    count = countResult.rows[0].count;
  return count;
}

async function getTopicsAndAssociatedDocuments() {
  try {
    let topicData = [];
    // Second query: Get topic data for the HTML table
    const topicSql = `
      WITH topic_titles AS (
        SELECT ttt.topic_id, 
               STRING_AGG(term_text, '-' ORDER BY rank DESC) AS label 
        FROM topic_top_terms ttt 
        GROUP BY ttt.topic_id
      ) 
      SELECT tt.label, 
             STRING_AGG(d.title, ', ') AS document_titles
      FROM topic_titles tt, 
           document_topics dt, 
           documents d 
      WHERE d.id = dt.document_id 
        AND dt.topic_id = tt.topic_id 
      GROUP BY tt.label order by count( d.title ) desc;
    `;


    const topicResult = await client.query(topicSql);

    if (topicResult.rows.length > 0) {
      topicData = topicResult.rows;
    }

    return topicData;

  } catch (err) {
    console.error('Error executing select: ', err.stack);
  }

}

async function getTopicDocumentMapping() { 
  try  {
    let sql = "select document_id , topic_id from document_topics ";
    let data = {};
    const topicResult = await client.query( sql );
    for( var i = 0; i < topicResult.rows.length; i++ ){
      let element = topicResult.rows[i];
      let doc_id = element['document_id' ];
      let topic_id = element['topic_id' ];
      data[doc_id] = topic_id;
      //data.element['document_id'] = element['topic_id'];
    }
    return data;
  } catch ( err ) {
    console.error( 'Error executing select: ', err.stack );
  }
}


async function getDocumentCoords() {
  try {

    // The data is coming out of the model , and will go into db, like this 
    // let data =  [
    //       {id :   1730,  x  :   -2.5284564, y  :    -2.507452, t_id  :   13 },
    //       {id :   1731,  x  :   -3.067392,  y  :    -2.27532,  t_id  :   -1 },
    //       {id :   1732,  x  :   3.50704,    y  :    1.6525944, t_id  :   1 },
    //       {id :   1733,  x  :   7.276127,   y  :    2.7509475, t_id  :   4 },

    // ];

    const topicData = await getTopicDocumentMapping();
    let data = [];
    const coordResult = await client.query(" select d.id as document_id,  substring( d.title from 1 for 20 )  as title, x, y, length( d.raw_text ) as size from doc_coords dc, documents d where  d.id = dc.document_id " );
    if( coordResult.rows.length > 0 ) 
      data = coordResult.rows;

    const colors= [ 'AliceBlue', 'Azure', 'Bisque', 'CadetBlue', 'BurlyWood', 'Coral','DarkCyan',
      'DarkKhaki', 'DarkOrange','DarkSlateBlue', 
    
      'Yellow', 'Violet','SteelBlue', 'Tan', 'Teal','SpringGreen', 
      'SlateGrey', 'Thistle','Tomato', 'Salmon', 'SandyBrown','SeaGreen' 
    ];

    //TODO make it also grab length( d.raw_text ) as size in above sql , push that to a new data element called sizes : []
    // later in the express template tell plotly to use that sizes for the sizes. normalize in this method if needed. 
    let ref_colors = [];
    let plotData = { x : [], y : [] , labels : [], originalSizes : [], sizes : [] , colors : [] } ;


    // this is the format that plotly.js expects it for a scatter plot. 
    for( var i = 0; i < data.length; i++ ){
      let element = data[i];
      const doc_id = element.document_id;
      plotData.x.push( element.x );
      plotData.y.push( element.y );
      plotData.labels.push( element.title );
      plotData.originalSizes.push( element.size  );
      
      plotData.colors.push(  colors[ topicData[doc_id] % colors.length]  );
    }


    // find min, max
    // edit the size to be w/in range 20 - 60 via scale factor
    let min = 0, max = 0;
    for( var i = 0; i < plotData.originalSizes.length; i++ ){
      let s = plotData.originalSizes[i];
      if( min == 0 || s <= min ){ min  = s;}
      if( max == 0 || s >= max ){ max = s; }
    }
    // // want to map ( max - min ) onto ( 50  - 10 ), and have a scale factor. 
    let scale = ( max - min ) / ( 50 - 10 );

    for( var i = 0; i < plotData.originalSizes.length; i++ ){
      plotData.sizes.push( 5 + ( plotData.originalSizes[i] /  scale ) );

    }

    return plotData;

  } catch (err) {
    console.error('Error executing select: ', err.stack);
  }

}

export { client, getTotalDocumentCount, getTopicsAndAssociatedDocuments, getDocumentCoords};
