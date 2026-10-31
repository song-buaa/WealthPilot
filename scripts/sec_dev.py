"""Explicit MacBook SEC identity initialization/check/child-process wrapper."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research_v2.sec_identity import DEFAULT_PATH, SEC_IDENTITY_KEYS, child_environment, identity_string, private_identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=DEFAULT_PATH)
    commands=parser.add_subparsers(dest='action',required=True)
    commands.add_parser('status')
    commands.add_parser('configure')
    run=commands.add_parser('run');run.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args();path=args.config.expanduser()
    if args.action=='configure':
        if not sys.stdin.isatty():
            print(json.dumps(dict(status='INVALID',reason='LOCAL_TERMINAL_REQUIRED')));return 2
        if path.exists() or path.is_symlink():
            print(json.dumps(dict(status='INVALID',path=str(path),reason='EXISTING_CONFIG_NOT_OVERWRITTEN')));return 2
        values={'WEALTHPILOT_SEC_ORGANIZATION':getpass.getpass('Real SEC organization (hidden): '),
                'WEALTHPILOT_SEC_CONTACT_EMAIL':getpass.getpass('Real SEC contact email (hidden): ')}
        if not identity_string(values):
            print(json.dumps(dict(status='INVALID',path=str(path),reason='SEC_IDENTITY_FORMAT_INVALID')));return 2
        if path.parent.is_symlink():raise ValueError('SEC_CONFIG_DIRECTORY_SYMLINK_REJECTED')
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.parent.chmod(0o700)
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w') as output:json.dump(values,output)
    status,_=private_identity(path)
    if args.action!='run':
        print(json.dumps(dict(status=status,path=str(path))));return 0 if status=='PRESENT' else 2
    if status!='PRESENT':
        print(json.dumps(dict(status=status,path=str(path),repair='Run scripts/sec_dev.py configure in your local Terminal.')));return 2
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command:parser.error('run requires an explicitly authorized child command after --')
    return subprocess.run(command,env=child_environment(path)).returncode


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError):
        print(json.dumps(dict(status='INVALID',reason='SEC_PRIVATE_CONFIGURATION_OR_CHILD_REJECTED')));raise SystemExit(2)
