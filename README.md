# aiodbx

rough async Python implementation of the Dropbox HTTP API using aiohttp

## example program

this program downloads three files, changes them and uploads the changed files back to Dropbox.

```python
import os
import asyncio
import logging

import aiodbx


async def task(api: aiodbx.AsyncDropboxAPI, shared_link: str):
    # download from the URL shared_link to local_path
    # if no local path is provided, it is downloaded to the current directory
    # to preserve folder structures you should provide local paths yourself
    local_path = await api.download_shared_link(shared_link)

    # do some work on the file. for this example we just rename it
    new_path = f'{local_path}_new'
    os.rename(local_path, new_path)

    # upload the new file to an upload session
    # this returns a "commit" dict, which will be passed to upload_finish later
    # the commit is saved in the AsyncDropboxAPI object already, so unless you need
    # information from it you can discard the return value
    await api.upload_start(new_path, f'/{new_path}')

    return new_path


async def run_all(api: aiodbx.AsyncDropboxAPI, shared_links: list[str]):
    # first, validate our API token
    await dbx.validate()

    # create a coroutine for each link in shared_links
    # run them and print a simple confirmation message when we have a result
    coroutines = [task(api, link) for link in shared_links]
    for coro in asyncio.as_completed(coroutines):
        try:
            res = await coro
        except aiodbx.DropboxApiError as e:
            # this exception is raised when the API returns an error
            print('Encountered an error')
            print(e)
        else:
            print(f'Processed {res}')

    # once everything is uploaded, finish the upload batch
    # this returns the metadata of all of the uploaded files
    uploaded_files = await api.upload_finish()

    # print out some info
    print('\nThe files we just uploaded are:')
    for file in uploaded_files:
        print(file['name'])


if __name__ == '__main__':
    # init API
    with open('tokenfile', 'r') as tokenfile:
        dbx = aiodbx.AsyncDropboxAPI(tokenfile.read().rstrip())

    # logging config
    dbx.log.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('[%(levelname)s] - %(message)s'))
    dbx.log.addHandler(handler)

    # the shared links we want to download from
    shared_links = [
        'https://www.dropbox.com/s/blahblah/foo?dl=0',
        'https://www.dropbox.com/s/blahblah/bar?dl=0',
        'https://www.dropbox.com/s/blahblah/baz?dl=0',
    ]

    # run our main task
    asyncio.get_event_loop().run_until_complete(run_all(dbx, shared_links))
```

## currently implemented

| endpoint                                                     | function call                 |
| ------------------------------------------------------------ | ----------------------------- |
| [/check/user](https://www.dropbox.com/developers/documentation/http/documentation#check-user) | dbx.validate()                |
| [/get_shared_link_file](https://www.dropbox.com/developers/documentation/http/documentation#sharing-get_shared_link_file) | dbx.download_shared_link()    |
| [/upload_session/start](https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-start) | dbx.upload_start()            |
| [/upload_session/finish_batch](https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-finish_batch), [/upload_session/finish_batch/check](https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-finish_batch) | dbx.upload_finish()           |
| [/create_shared_link_with_settings](https://www.dropbox.com/developers/documentation/http/documentation#sharing-create_shared_link_with_settings) | dbx.filename_to_shared_link() |
| [/get_shared_link_metadata](https://www.dropbox.com/developers/documentation/http/documentation#sharing-get_shared_link_metadata) | dbx.shared_link_to_filename() |