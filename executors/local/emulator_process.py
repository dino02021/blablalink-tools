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
from executors.local.process_guard import _stop


def run_d1_emulator(payload,seconds=30,*,mock_directory=None,schema_id=None):
    if sys.platform!='win32':raise ValueError('local emulator process tree requires verified Windows supervision')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not .05<=seconds<=60:
        raise ValueError('bounded local emulator duration required')
    serialized=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(serialized)>65536:raise ValueError('bounded local SQL contract payload required')
    storage=None
    if mock_directory is not None:
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
            input_path.write_bytes(serialized)
            arguments=(str(Path(node).resolve()),str(script),str(input_path))
            if storage is not None:arguments+= (str(storage),)
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
            _stop(process,fence)
            output.seek(0)
            errors.seek(0)
            stdout=output.read(65536).decode('utf-8',errors='replace')
            stderr=errors.read(65537)
            return {'is_mock':True,'outcome':outcome,'exit_code':process.returncode,'is_child_stopped':process.poll() is not None,
                'max_processes':4,'job_memory_limit_bytes':512*1024*1024,'seconds':time.monotonic()-started,
                'stdout':stdout,'stderr_hash':hashlib.sha256(stderr).hexdigest(),'is_stderr_truncated':len(stderr)>65536}
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                try:process.stdin.close()
                except BrokenPipeError:pass
            _stop(process,fence)
        else:fence.close()
