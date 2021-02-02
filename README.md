# aiodbx

rough async Python implementation of the Dropbox HTTP API using aiohttp

check out example.py for a simple use case

## currently implemented

| endpoint                                                     | function call                 |
| ------------------------------------------------------------ | ----------------------------- |
| [/check/user](https://www.dropbox.com/developers/documentation/http/documentation#check-user) | dbx.validate()                |
| [/get_shared_link_file](https://www.dropbox.com/developers/documentation/http/documentation#sharing-get_shared_link_file) | dbx.download_shared_link()    |
| [/upload_session/start](https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-start) | dbx.upload_start()            |
| [/upload_session/finish_batch](https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-finish_batch), [/upload_session/finish_batch/check](https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-finish_batch) | dbx.upload_finish()           |
| [/create_shared_link_with_settings](https://www.dropbox.com/developers/documentation/http/documentation#sharing-create_shared_link_with_settings) | dbx.filename_to_shared_link() |
| [/get_shared_link_metadata](https://www.dropbox.com/developers/documentation/http/documentation#sharing-get_shared_link_metadata) | dbx.shared_link_to_filename() |