# LatentTopicExplorer
Application to discover and explore topics in a pdf corpus. 

***bug** I just did an end to end test and 1) the import stalled and failed for some pdf files, I removed them and restarted. 2) You have to issue the following sql "update documents set title = file_path;" at the very end in ordre to see the final chart -oops. 

This is a docker compose based application stack that allows you to import a collection of pdf files, extract the text, perform cluster analysis, and automated topic discovery. 

![Description of image](image2.png)

If you clone the code, then do "docker compose up -d" from the root you should have two web applications running on localhost:3000 and localhost:8000. 
The process is as follows. 

* Place the pdf files to be imported into the 'data' directory
* You have to access each of these urls in order. The only way to know if a step is done is to monitor the docker logs for the nlp_pipeline container.
* http://localhost:3000/load_docs
* http://localhost:3000/embed_docs
* http://localhost:3000/umap
* http://localhost:3000/topics
* http://localhost:3000/terms
* http://localhost:3000/tf_idf

Once the final step completes you can view your dashboard at http://localhost:8000. 

This project is packaged as a docker compose file with five services. There is a postgresql database, a redis message queue, a python machine learning container, a flask app , and an express app. 
The code works by scanning the 'data' folder and using pdfplumber to import the text for each pdf file. 
This text is stored in a 'documents' table in the db. Next the text is chunked into 100 char chunks with a 10 char overlap and these are written to the chunked_embeddings child table. 
The 'embed_docs' step will take each 100 char chunks and embed it as a single vector in 384D space using the sentence_transformers library. 
Then a document embedding is calculated by simply averging all the chunks that make up the originial file. 
These document level embeddings are passed to the UMAP algorithim for dimensionality reduction and HDBScan for cluster identification. 
These clusters are unlabelled at this point and the ( x,y ) cooridinates and document2topic relational information is saved back in postgresql. 

The 'terms' step uses the Spacey NLP library to create all the terms and terms per document in additional db tables. 
Now that the terms and topics are known the 'tf_idf' step calculates a class based tf_idf score for the topics and saves off the top 5 terms from the tf_idf perspective. 
These top 5 terms are the 'topic label' and the right hand column lists the pdf files assigned to that cluster. 

The plot is created with plotly. The marker size is a scaled version of the page length and the colors are meant to group together documents in the same topic. 
When the code is running you can zoom in and click on the markers to navigate to the document page for that particular document. 








