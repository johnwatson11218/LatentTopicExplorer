This folder holds my systemd config unit files. 
Currently there are 2 files that watch a hard coded folder and run a python script when a file is added to the folder. 

to install 

1. copy the files to /etc/systemd/system
2. run  "sudo systemctl daemon-reload"
3. run "sudo systemctl start doc-import-notify.path"
4. run " journalctl -u doc-import-notify"

Try adding a file or two to the hard coded folder and you should see the python scripts output using the journalctl command above. 

