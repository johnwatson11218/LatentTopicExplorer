# LatentTopicExplorer
Application to discover and explore topics in a pdf corpus. 



This is a docker compose based application stack that allows you to import a collection of pdf files, extract the text, perform cluster analysis, and automated topic discovery. 

![Description of image](image.png)

If you clone the code, then do "docker compose up -d" from the root you should have two web applications running on localhost:3000 and localhost:8000. 
The process is as follows. 

* Place the pdf files to be imported into the 'data' directory
* You have to access each of these urls in order. The only way to know if a step is done is to monitory the docker logs for the nlp_pipeline container.
* Access the url http://localhost:3000/load_docs
* Access the url http://localhost:3000/embed_docs
* Access the url http://localhost:3000/umap
* Access the url http://localhost:3000/topics
* Access the url http://localhost:3000/terms
* Access the url http://localhost:3000/tf_idf






