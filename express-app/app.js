import express from 'express';

import { getTotalDocumentCount, getTopicsAndAssociatedDocuments, getDocumentCoords }  from './db_functions.js';
import { publishString  } from './redis_functions.js';

const app = express();
app.set( 'view engine' , 'ejs' );
app.set( 'views', './views'); // optional, this is the default
const port = 3000;

app.use((req, res, next) => {
  console.log(`📥 ${new Date().toISOString()} - ${req.method} ${req.url}`);
  next();
});

app.get('/', async (req, res) => {
  res.render('index', { 
    chartData : await getDocumentCoords(),
    count: await getTotalDocumentCount(),
    topicData: await getTopicsAndAssociatedDocuments()
  });
});

app.get('/load_docs', async (req, res) => {
  await publishString( 'process_pdfs');
  res.send(`Message pushed to redis`);
});

app.get( '/embed_docs', async ( req, res ) => {
  await publishString( 'embed_pdfs');
  res.send(`messages pushed to redis`);
});

app.get( '/umap', async ( req, res ) => {
  await publishString( 'umap');
  res.send(`Message pushed to redis`);
});

app.get( '/topics', async ( req, res ) => {
  await publishString( 'topics');
  res.send(`Message pushed to redis`);
});

app.get( '/terms', async ( req, res ) => {
  await publishString( 'terms');
  res.send(`Message pushed to redis`);
});

app.get( '/tf_idf', async ( req, res ) => {
  await publishString( 'tf_idf');
  res.send(`Message pushed to redis`);
});

app.listen(port, () => {
  console.log(`App listening at http://localhost:${port}`);
});