import os
import json
import typing
import logging
import aiohttp
import asyncio
import aiofiles


class DropboxApiError(Exception):
    # exception for errors thrown by the API

    def __init__(self, status: int, message: typing.Union[str, dict]):
        self.status = status
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        if isinstance(self.message, str):
            try:
                self.message = json.loads(self.message)
                return f'{self.status} {self.message["error_summary"]}'
            except:
                return f'{self.status} {self.message}'
        else:
            return f'{self.status} {self.message}'


class Request:
    def __init__(self,
                 request: typing.Callable[..., typing.Any],
                 url: str,
                 log: logging.Logger = None,
                 ok_statuses: list[int] = [200],
                 retry_count: int = 5,
                 retry_statuses: list[int] = [429],
                 **kwargs: typing.Any):
        self.request = request
        self.url = url
        self.log = log
        self.ok_statuses = ok_statuses
        self.retry_count = retry_count
        self.retry_statuses = retry_statuses
        self.kwargs = kwargs
        self.trace_request_ctx = kwargs.pop('trace_request_ctx', {})

        self.current_attempt = 0
        self.resp: typing.Optional[aiohttp.ClientResponse] = None

        self.log = log or logging.getLogger('null')

    async def _do_request(self) -> aiohttp.ClientResponse:
        self.current_attempt += 1
        if self.current_attempt > 1:
            self.log.debug(
                f'Attempt {self.current_attempt} out of {self.retry_count}')

        resp: aiohttp.ClientResponse = await self.request(
            self.url,
            **self.kwargs,
            trace_request_ctx={
                'current_attempt': self.current_attempt,
                **self.trace_request_ctx,
            },
        )

        if resp.status in self.ok_statuses or resp.status < 400:
            endpoint_name = self.url[self.url.index('2') + 1:]
            self.log.debug(
                f'Request OK: {endpoint_name} returned {resp.status}')
        else:
            raise DropboxApiError(resp.status, await resp.text())

        if self.current_attempt < self.retry_count and resp.status in self.retry_statuses:
            if 'Retry-After' in resp.headers:
                sleep_time = int(resp.headers['Retry-After'])
            else:
                sleep_time = 1
            await asyncio.sleep(sleep_time)
            return await self._do_request()

        self.resp = resp
        return resp

    def __await__(
            self
    ) -> typing.Generator[typing.Any, None, aiohttp.ClientResponse]:
        return self.__aenter__().__await__()

    async def __aenter__(self) -> aiohttp.ClientResponse:
        return await self._do_request()

    async def __aexit__(self, *excinfo) -> None:
        if self.resp is not None:
            if not self.resp.closed:
                self.resp.close()


class AsyncDropboxAPI:
    def __init__(self,
                 token: str,
                 retry_statuses: list[int] = [429],
                 allowed_retries: int = 5,
                 log: logging.Logger = None):
        self.token = token
        self.retry_statuses = retry_statuses
        self.allowed_retries = allowed_retries
        self.client_session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit_per_host=50))
        self.upload_session: list[dict] = []

        self.log = log or logging.getLogger('null')

    async def validate(self):
        # validates the user authentication token by querying a simple string
        # if the API returns the same string, the token is valid
        # a DropboxApiError will be raised by the request handler if the token is invalid
        # https://www.dropbox.com/developers/documentation/http/documentation#check-user

        self.log.debug('Validating token')

        url = 'https://api.dropboxapi.com/2/check/user'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        data = json.dumps({'query': 'aiodbx'})

        async with Request(self.client_session.post,
                           url,
                           self.log,
                           headers=headers,
                           data=data) as resp:
            resp_data = await resp.json()
            if resp_data['result'] == 'aiodbx':
                # token is valid, continue
                self.log.debug('Token is valid')
                return True
            else:
                raise DropboxApiError(resp.status, 'Token is invalid')

    async def download_file(self,
                            dropbox_path: str,
                            local_path: str = None) -> str:
        # downloads the file at dropbox_path to local_path
        # returns the path the file was downloaded to

        # default to current directory
        if local_path == None:
            local_path = os.path.basename(dropbox_path)

        self.log.info(f'Downloading {os.path.basename(local_path)}')
        self.log.debug(f'from {dropbox_path}')

        url = 'https://content.dropboxapi.com/2/files/download'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Dropbox-API-Arg": json.dumps({"path": dropbox_path})
        }

        async with Request(self.client_session.post,
                           url,
                           self.log,
                           headers=headers) as resp:
            async with aiofiles.open(local_path, 'wb') as f:
                async for chunk, _ in resp.content.iter_chunks():
                    await f.write(chunk)
                return local_path

    async def download_folder(self,
                              dropbox_path: str,
                              local_path: str = None) -> str:
        # downloads an entire folder as a zip file
        # returns the local_path of the zip file
        # https://www.dropbox.com/developers/documentation/http/documentation#files-download_zip

        # default to current directory
        if local_path == None:
            local_path = os.path.basename(dropbox_path)

        self.log.info(f'Downloading {os.path.basename(local_path)}')
        self.log.debug(f'from {dropbox_path}')

        url = 'https://content.dropboxapi.com/2/files/download_zip'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Dropbox-API-Arg": json.dumps({"path": dropbox_path})
        }

        async with Request(self.client_session.post,
                           url,
                           self.log,
                           headers=headers) as resp:
            async with aiofiles.open(local_path, 'wb') as f:
                async for chunk, _ in resp.content.iter_chunks():
                    await f.write(chunk)
                return local_path

    async def download_shared_link(self,
                                   shared_link: str,
                                   local_path: str = None) -> str:
        # downloads a file from a shared link
        # returns the path the file was downloaded to
        # https://www.dropbox.com/developers/documentation/http/documentation#sharing-get_shared_link_file

        # default to current directory, with the path in the shared link
        if local_path == None:
            local_path = os.path.basename(shared_link[:shared_link.index('?')])

        self.log.info(f'Downloading {os.path.basename(local_path)}')
        self.log.debug(f'from {shared_link}')

        url = 'https://content.dropboxapi.com/2/sharing/get_shared_link_file'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Dropbox-API-Arg": json.dumps({"url": shared_link})
        }

        async with Request(self.client_session.post,
                           url,
                           self.log,
                           headers=headers) as resp:
            async with aiofiles.open(local_path, 'wb') as f:
                async for chunk, _ in resp.content.iter_chunks():
                    await f.write(chunk)
                return local_path

    async def upload_start(self, local_path: str, dropbox_path: str) -> dict:
        # uploads a single file to an upload session
        # returns an UploadSessionFinishArg dict with information on the upload
        # https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-start

        if not os.path.exists(local_path):
            raise ValueError(f"local_path {local_path} does not exist")
        if len(self.upload_session) >= 1000:
            raise RuntimeError(
                'upload_session is too large, you must call upload_finish to commit the batch'
            )

        self.log.info(f'Uploading {os.path.basename(local_path)}')
        self.log.debug(f'to {dropbox_path}')

        url = 'https://content.dropboxapi.com/2/files/upload_session/start'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Dropbox-API-Arg": json.dumps({"close": True}),
            "Content-Type": "application/octet-stream"
        }

        async with aiofiles.open(local_path, 'rb') as f:
            data = await f.read()
            async with Request(self.client_session.post,
                               url,
                               self.log,
                               headers=headers,
                               data=data) as resp:
                resp_data = await resp.json()

                # construct commit entry for finishing batch later
                commit = {
                    "cursor": {
                        "session_id": resp_data['session_id'],
                        "offset": os.path.getsize(local_path)
                    },
                    "commit": {
                        "path": dropbox_path,
                        "mode": "add",
                        "autorename": False,
                        "mute": False
                    }
                }
                self.upload_session.append(commit)
                return commit

    async def upload_finish(self, check_interval: float = 5) -> list[dict]:
        # finishes an upload batch
        # returns a list of FileMetadata dicts
        # https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-finish_batch

        if len(self.upload_session) == 0:
            raise RuntimeError(
                "upload_session is empty, have you uploaded any files yet?")

        self.log.info('Finishing upload batch')
        self.log.debug(f'Batch size is {len(self.upload_session)}')

        url = 'https://api.dropboxapi.com/2/files/upload_session/finish_batch'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        data = json.dumps({"entries": self.upload_session})

        async with Request(self.client_session.post,
                           url,
                           self.log,
                           headers=headers,
                           data=data) as resp:
            resp_data = await resp.json()
            self.upload_session = []  # empty the local upload session

            if resp_data['.tag'] == 'async_job_id':
                # check regularly for job completion
                return await self._upload_finish_check(
                    resp_data['async_job_id'], check_interval=check_interval)
            elif resp_data['.tag'] == 'complete':
                self.log.info('Upload batch finished')
                return resp_data['entries']
            else:
                err = await resp.text()
                raise DropboxApiError(
                    resp.status, f'Unknown upload_finish response: {err}')

    async def _upload_finish_check(self,
                                   job_id: str,
                                   check_interval: float = 5) -> list[dict]:
        # checks on an upload_finish async job every check_interval seconds
        # returns a list of FileMetadata dicts
        # https://www.dropbox.com/developers/documentation/http/documentation#files-upload_session-finish_batch-check:w

        self.log.debug(
            f'Batch not finished, checking every {check_interval} seconds')

        url = 'https://api.dropboxapi.com/2/files/upload_session/finish_batch/check'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        data = json.dumps({"async_job_id": job_id})

        while True:
            await asyncio.sleep(check_interval)
            async with Request(self.client_session.post,
                               url,
                               self.log,
                               headers=headers,
                               data=data) as resp:
                resp_data = await resp.json()

                if resp_data['.tag'] == 'complete':
                    self.log.info('Upload batch finished')
                    return resp_data['entries']
                elif resp_data['.tag'] == 'in_progress':
                    self.log.debug(
                        f'Checking again in {check_interval} seconds')
                    continue

    async def upload_single(self,
                            local_path: str,
                            dropbox_path: str,
                            args: dict = None) -> dict:
        # upload a single file from local_path to dropbox_path
        # returns the FileMetadata of the uploaded file
        # https://www.dropbox.com/developers/documentation/http/documentation#files-upload

        if not os.path.exists(local_path):
            raise ValueError(f"local_path {local_path} does not exist")
        if not args:
            args = {'mode': 'add', 'autorename': False, 'mute': False}
        args['path'] = dropbox_path

        self.log.info(f'Uploading {os.path.basename(local_path)}')
        self.log.debug(f'to {dropbox_path}')

        url = 'https://content.dropboxapi.com/2/files/upload'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Dropbox-API-Arg": json.dumps(args),
            "Content-Type": "application/octet-stream"
        }

        async with aiofiles.open(local_path, 'rb') as f:
            data = await f.read()
            async with Request(self.client_session.post,
                               url,
                               self.log,
                               headers=headers,
                               data=data) as resp:
                resp_data = await resp.json()
                return resp_data

    async def create_shared_link(self, dropbox_path: str) -> str:
        # create a shared link from a dropbox filename
        # https://www.dropbox.com/developers/documentation/http/documentation#sharing-create_shared_link_with_settings

        self.log.info(
            f'Creating shared link for file {os.path.basename(dropbox_path)}')
        self.log.debug(f'Full path is {dropbox_path}')

        url = 'https://api.dropboxapi.com/2/sharing/create_shared_link_with_settings'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        data = json.dumps({'path': dropbox_path})

        # accept 409 status to check for existing shared link
        async with Request(self.client_session.post,
                           url,
                           self.log,
                           headers=headers,
                           data=data,
                           ok_statuses=[200, 409]) as resp:
            resp_data = await resp.json()

            if resp.status == 200:
                return resp_data['url']
            else:
                if 'shared_link_already_exists' in resp_data['error_summary']:
                    self.log.warning(
                        f'Shared link already exists for {os.path.basename(dropbox_path)}, using existing link'
                    )
                    return resp_data['error']['shared_link_already_exists'][
                        'metadata']['url']
                elif 'not_found' in resp_data['error_summary']:
                    raise DropboxApiError(
                        resp.status, f'Path {dropbox_path} does not exist')
                else:
                    err = await resp.text()
                    raise DropboxApiError(resp.status,
                                          f'Unknown Dropbox error: {err}')

    async def get_shared_link_metadata(self, shared_link: str) -> str:
        # get the dropbox path of a file given its shared link
        # https://www.dropbox.com/developers/documentation/http/documentation#sharing-get_shared_link_metadata

        self.log.info(f'Getting filename from shared link {shared_link}')

        url = 'https://api.dropboxapi.com/2/sharing/get_shared_link_metadata'
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        data = json.dumps({'url': shared_link})

        async with Request(self.client_session.post,
                           url,
                           self.log,
                           headers=headers,
                           data=data) as resp:
            resp_data = await resp.json()
            return resp_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *excinfo):
        await self.client_session.close()