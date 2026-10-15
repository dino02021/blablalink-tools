"""Owned subprocess helper for mock workbook concurrency tests; no external I/O."""

import argparse
import json
from pathlib import Path
import sys

from adapters.mock_sheets import MockSheets
from adapters.mock_workbook_lock import workbook_file_lock


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=('hold','human','publish'))
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    sheets=MockSheets(args.directory)
    path=sheets._path('book')
    if args.operation=='hold':
        with workbook_file_lock(path):
            print('LOCKED',flush=True)
            sys.stdin.readline()
    elif args.operation=='human':
        with workbook_file_lock(path):
            book=sheets.read('book')
            print('READ',flush=True)
            sys.stdin.readline()
            book['inputs']['note']='child edit'
            sheets._save('book',book)
    else:
        sheets.apply('book','layout',{'score':99},'child_commit')
    print(json.dumps({'done':True}),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
