urls = []
with open( 'p_urls.txt') as f:
    [ urls.append( u.strip() ) for u in f.readlines() ]

import requests
import re
import time
for url in urls:
    response = requests.get( url )
    file_name = "".join( re.findall( r'\d+', url ) )        
    
    with open( 'downloaded_files/' + file_name  + ".pdf", 'wb' ) as f:
        f.write( response.content )
    time.sleep(1) # rate limit