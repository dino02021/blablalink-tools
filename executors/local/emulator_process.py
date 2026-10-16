"""Owned Windows-only Miniflare test process tree; never a business executor profile."""

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from adapters.windows_child_job import WindowsChildJob
from adapters.mock_d1_workspace import require_mock_d1_workspace
from adapters.mock_d1_lock import mock_d1_access
from adapters.mock_d1_maintenance import maintenance_payload, maintenance_storage
from executors.local.process_guard import _stop


def run_d1_emulator(payload,seconds=30,*,mock_directory=None,schema_id=None):
    return _run_emulator(payload,seconds,mock_directory,schema_id,False)


def run_d1_object_session(payload,*,mock_directory,schema_id,seconds=60):
    """Local fixture orchestration; every simulated control frame remains <=64 KiB."""
    if mock_directory is None:
        raise ValueError('persistent marked mock D1 session required')
    return _run_emulator(payload,seconds,mock_directory,schema_id,True)


def run_d1_maintenance_batch(statements, capability, *, seconds=30):
    payload = maintenance_payload(statements)
    # Validate the grant before accessing any attributes or starting a child.
    maintenance_storage(capability, getattr(capability, 'directory', None),
                        getattr(getattr(capability, 'intent', None), 'source_schema_id', None), payload)
    return _run_owned_emulator(payload, seconds, capability.directory, capability.intent.source_schema_id,
                               False, maintenance_capability=capability)


def _run_emulator(payload,seconds,mock_directory,schema_id,is_object_session):
    if mock_directory is not None:
        with mock_d1_access(mock_directory, schema_id):
            return _run_owned_emulator(payload,seconds,mock_directory,schema_id,is_object_session)
    return _run_owned_emulator(payload,seconds,mock_directory,schema_id,is_object_session)


def _run_owned_emulator(payload,seconds,mock_directory,schema_id,is_object_session,*,maintenance_capability=None):
    if sys.platform!='win32':raise ValueError('local emulator process tree requires verified Windows supervision')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not .05<=seconds<=60:
        raise ValueError('bounded local emulator duration required')
    serialized=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(serialized)>(24*1024*1024 if is_object_session else 65536):
        raise ValueError('bounded local SQL contract payload required')
    if is_object_session:
        if type(payload) is not dict or set(payload)!={'schema_statements','operations'}:
            raise ValueError('bounded object session fixture required')
        if not isinstance(payload['operations'],list) or not 1<=len(payload['operations'])<=2048:
            raise ValueError('bounded object session frames required')
        if len(json.dumps(payload['schema_statements']).encode())>65536:
            raise ValueError('bounded object schema frame required')
        for operation in payload['operations']:
            if type(operation) is not dict or operation.get('kind')!='batch' or not 1<=len(operation.get('statements',[]))<=50:
                raise ValueError('bounded object batch frame required')
            if len(json.dumps(operation,ensure_ascii=False,allow_nan=False).encode())>65536:
                raise ValueError('object control frame exceeds 64 KiB')
    storage=None
    if maintenance_capability is not None:
        storage = maintenance_storage(maintenance_capability, mock_directory, schema_id, payload)
    elif mock_directory is not None:
        storage=require_mock_d1_workspace(mock_directory,schema_id)/'storage'
    elif schema_id is not None:
        raise ValueError('mock D1 directory required for schema identity')
    node=shutil.which('node')
    folder=Path(__file__).resolve().parents[2]/'coordination/offline_d1'
    script=folder/'runner.mjs'
    if node is None or not script.is_file() or not (folder/'node_modules/miniflare/package.json').is_file():
        raise ValueError('pinned local Miniflare dependency installation required')
    environment={key:os.environ[key] for key in ('SystemRoot','WINDIR','TEMP','TMP','LANG','LC_ALL') if key in os.environ}
    fence=WindowsChildJob(512*1024*1024,max_processes=4)
    process=None
    started=time.monotonic()
    outcome='completed'
    try:
        with tempfile.TemporaryDirectory(prefix='owned-d1-input-') as temporary, tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            input_path=Path(temporary)/'contract.json'
            reply_path=Path(temporary)/'reply.json'
            input_path.write_bytes(serialized)
            arguments=(str(Path(node).resolve()),str(script),str(input_path))
            if storage is not None:arguments+= (str(storage),)
            if is_object_session:arguments+= (str(reply_path),)
            process=subprocess.Popen(arguments,cwd=folder,env=environment,
                stdin=subprocess.PIPE,stdout=output,stderr=errors,shell=False,creationflags=subprocess.CREATE_NO_WINDOW)
            fence.attach(process)
            process.stdin.write(b'start\n')
            process.stdin.close()
            while True:
                if any(os.fstat(stream.fileno()).st_size>65536 for stream in (output,errors)):
                    outcome='output_limit_terminated'
                    break
                if process.poll() is not None:
                    outcome='completed' if process.returncode==0 else 'child_failed'
                    break
                if time.monotonic()-started>=seconds:
                    outcome='deadline_terminated'
                    break
                try:process.wait(timeout=.05)
                except subprocess.TimeoutExpired:pass
            try:resource_usage=fence.usage()
            except OSError as error:resource_usage={'query_error_code':error.errno}
            _stop(process,fence)
            output.seek(0)
            errors.seek(0)
            stdout=output.read(65536).decode('utf-8',errors='replace')
            stderr=errors.read(65537)
            receipt={'is_mock':True,'outcome':outcome,'exit_code':process.returncode,'is_child_stopped':process.poll() is not None,
                'max_processes':4,'job_memory_limit_bytes':512*1024*1024,'seconds':time.monotonic()-started,
                'stdout':stdout,'stderr_hash':hashlib.sha256(stderr).hexdigest(),'is_stderr_truncated':len(stderr)>65536}
            receipt['sdk_resource_usage']=resource_usage
            if is_object_session and outcome!='completed':
                receipt['sdk_diagnostic']=stderr[-2000:].decode('utf-8',errors='replace')
            if is_object_session and outcome=='completed':
                metadata=json.loads(stdout)
                if reply_path.is_symlink() or not reply_path.is_file() or not 0<reply_path.stat().st_size<=32*1024*1024:
                    raise ValueError('bounded owned D1 object reply required')
                reply=reply_path.read_bytes()
                if set(metadata)!={'reply_size','reply_sha256'} or metadata['reply_size']!=len(reply) or metadata['reply_sha256']!=hashlib.sha256(reply).hexdigest():
                    raise ValueError('owned D1 object reply integrity differs')
                receipt['object_reply']=json.loads(reply)
                receipt['object_frame_count']=len(payload['operations'])
            return receipt
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:process.stdin.close()
                except BrokenPipeError:pass
            _stop(process,fence)
        else:fence.close()
