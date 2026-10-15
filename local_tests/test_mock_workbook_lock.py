from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from adapters.local_backup import create_backup, restore_backup
from adapters.mock_sheets import MockSheets
from adapters.mock_workbook_lock import workbook_file_lock
from application.compute_budget import ComputeBudget, ComputeLimit
from domain.execution import execution_checks
from executors.local.demo import run_demo


@pytest.fixture
def book(tmp_path):
    sheets=MockSheets(tmp_path/'workbooks')
    sheets.create('book','layout',{'note':'original','counter':0})
    return sheets


def test_two_adapters_do_not_lose_independent_human_and_generated_updates(book):
    other=MockSheets(book.directory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(book.edit_inputs,'book',{'note':'human','counter':0})
        second=pool.submit(other.apply,'book','layout',{'score':99},'commit')
        first.result();second.result()
    final=book.read('book')
    assert final['inputs']['note']=='human' and final['generated']=={'score':99} and final['commit_id']=='commit'


def test_read_modify_write_hold_prevents_lost_updates_across_real_processes(book):
    with probe(book,'human') as child:
        assert line_from_child(child)=='READ'
        other=MockSheets(book.directory)
        started=threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            write=pool.submit(apply_with_signal,other,started)
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):write.result(timeout=0.2)
            child.stdin.write('continue\n');child.stdin.flush()
            write.result(timeout=5)
        assert child.wait(timeout=5)==0
    final=book.read('book')
    assert final['inputs']['note']=='child edit' and final['generated']=={'score':99}
    assert final['commit_id']=='parent_commit'


def test_parent_human_update_and_subprocess_publish_both_survive(book):
    path=book._path('book')
    with ExitStack() as cleanup:
        with workbook_file_lock(path):
            data=book.read('book')
            child=cleanup.enter_context(probe(book,'publish'))
            assert child.poll() is None
            data['inputs']['note']='parent human'
            book._save('book',data)
        assert child.wait(timeout=5)==0
    final=book.read('book')
    assert final['inputs']['note']=='parent human' and final['generated']=={'score':99}
    assert final['commit_id']=='child_commit'


def test_process_termination_releases_kernel_lock_without_deleting_sidecar(book):
    with probe(book,'hold') as child:
        assert line_from_child(child)=='LOCKED'
        child.terminate();child.wait(timeout=5)
    book.edit_inputs('book',{'note':'after owner exit','counter':0})
    assert book.read('book')['inputs']['note']=='after owner exit'
    assert any((book.directory/'.locks').iterdir())


def test_lock_timeout_never_partially_mutates_workbook(book):
    before=book.read('book')
    with probe(book,'hold') as child:
        assert line_from_child(child)=='LOCKED'
        with pytest.raises(TimeoutError,match='lock_timeout'):
            book.edit_inputs('book',{'note':'must not save'})
        assert book._path('book').read_text(encoding='utf-8')==json.dumps(before,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        child.stdin.write('continue\n');child.stdin.flush()
        assert child.wait(timeout=5)==0
    assert book.read('book')==before


def test_nested_lock_and_decorated_reads_are_reentrant(book):
    with workbook_file_lock(book._path('book')):
        with workbook_file_lock(book._path('book')):
            book.edit_inputs('book',{'note':'nested','counter':0})
            assert book.read('book')['inputs']['note']=='nested'
    book.apply('book','layout',{'score':1},'commit')
    assert book.read('book')['generated']=={'score':1}


def test_multiple_threads_increment_without_losing_read_modify_write_state(book):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment,[(book.directory,25)]*4))
    assert book.read('book')['inputs']['counter']==100


def test_cooperative_budget_can_cancel_lock_wait_without_mutation(book):
    before=book.read('book')
    with probe(book,'hold') as child:
        assert line_from_child(child)=='LOCKED'
        with execution_checks(ComputeBudget(max_checks=1).check):
            with pytest.raises(ComputeLimit):book.edit_inputs('book',{'note':'must not save'})
        child.stdin.write('continue\n');child.stdin.flush()
        assert child.wait(timeout=5)==0
    assert book.read('book')==before


def test_lock_sidecars_are_not_backed_up_and_restore_builds_fresh_locks(tmp_path):
    directory=tmp_path/'source';run_demo(directory)
    sheets=MockSheets(directory/'workbooks')
    assert sheets.read('output_demo')['generated']
    assert (directory/'workbooks'/'.locks').is_dir()
    archive=tmp_path/'backup.zip'
    backup=create_backup(directory,archive,'code')
    restored=tmp_path/'restored';restore_backup(archive,restored,backup['backup_id'],'code')
    assert not (restored/'workbooks'/'.locks').exists()
    assert MockSheets(restored/'workbooks').read('output_demo')==sheets.read('output_demo')
    assert (restored/'workbooks'/'.locks').is_dir() and (restored/'restore-lock.json').exists()


@pytest.mark.parametrize('timeout',[0,11,False,float('nan')])
def test_invalid_lock_wait_bounds_do_not_create_data_files(tmp_path,timeout):
    with pytest.raises(ValueError):
        with workbook_file_lock(tmp_path/'book.json',timeout):pass
    assert list(tmp_path.iterdir())==[]


def increment(args):
    directory,count=args
    sheets=MockSheets(directory)
    for _ in range(count):
        with workbook_file_lock(sheets._path('book')):
            data=sheets.read('book')
            data['inputs']['counter']+=1
            sheets._save('book',data)


@contextmanager
def probe(book,operation):
    env={key:os.environ[key] for key in ('SYSTEMROOT','WINDIR','TEMP','TMP') if key in os.environ}
    env['PYTHONIOENCODING']='utf-8'
    child=subprocess.Popen([sys.executable,'-m','local_tests.workbook_lock_probe',operation,str(book.directory)],
        cwd=Path(__file__).resolve().parents[1],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        child.stdin.close();child.stdout.close();child.stderr.close()


def line_from_child(child):
    pool=ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(child.stdout.readline).result(timeout=10).strip()
    except TimeoutError:
        child.terminate();child.wait(timeout=5)
        raise
    finally:
        pool.shutdown(wait=True)


def apply_with_signal(sheets,started):
    started.set()
    sheets.apply('book','layout',{'score':99},'parent_commit')


def test_two_human_cell_editors_preserve_each_others_columns(book):
    book.set_manual_tab('book','notes','人工區',[[0,0]])
    barrier=threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(edit_column,book.directory,0,50,barrier)
        second=pool.submit(edit_column,book.directory,1,100,barrier)
        first.result();second.result()
    cells=book.read('book')['manual_tabs']['notes']['cells'][0]
    assert cells[0]['value']=={'numberValue':50} and cells[1]['value']=={'numberValue':100}


def edit_column(directory,column,final,barrier):
    sheets=MockSheets(directory)
    barrier.wait(timeout=5)
    for value in range(final-20,final+1):
        sheets.edit_cell('book','notes',0,column,value)
