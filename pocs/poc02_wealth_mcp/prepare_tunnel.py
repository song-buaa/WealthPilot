"""Operator-only hidden credential entry. Never pass a key on the command line."""
import getpass
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replace-revoked-key', action='store_true',
                        help='Replace a key only after the operator revoked it in Platform.')
    args = parser.parse_args()
    folder = Path('/tmp/wealthpilot-poc02-private')
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    if not (folder / 'tunnel-id').exists():
        tunnel_id = input('Tunnel ID (not an API key): ').strip()
        if not tunnel_id.startswith('tunnel_'):
            raise SystemExit('Invalid Tunnel ID')
        descriptor = os.open(folder / 'tunnel-id', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            output.write(tunnel_id)
    key_path = folder / 'runtime-key'
    if key_path.exists() and not args.replace_revoked_key:
        raise SystemExit('Runtime key file already exists; retained without replacement.')
    secret = getpass.getpass('Tunnel runtime API Key (hidden; never paste into chat): ').strip()
    if not secret.startswith('sk-'):
        raise SystemExit('No valid key entered; nothing saved.')
    target = key_path.with_name('runtime-key.new') if args.replace_revoked_key else key_path
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        output.write(secret)
    if args.replace_revoked_key:
        os.replace(target, key_path)
    print('Runtime key saved outside Git with mode 0600. No key printed.')


if __name__ == '__main__':
    main()
