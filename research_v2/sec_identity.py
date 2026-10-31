"""Narrow SEC operator identity helpers. No dotenv, network or global injection."""
import json
import os
from pathlib import Path
import re
import stat

SEC_IDENTITY_KEYS = {'WEALTHPILOT_SEC_ORGANIZATION', 'WEALTHPILOT_SEC_CONTACT_EMAIL'}
DEFAULT_PATH = Path.home() / '.config/wealthpilot/sec-identity.json'


def identity_string(values):
    org=values.get('WEALTHPILOT_SEC_ORGANIZATION','')
    email=values.get('WEALTHPILOT_SEC_CONTACT_EMAIL','')
    if not isinstance(org,str) or not isinstance(email,str):return None
    org,email=org.strip(),email.strip()
    if (not 2<=len(org)<=120 or any(ord(c)<32 or ord(c)>126 for c in org)
            or not re.fullmatch(r'[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+',email)
            or len(email)>254 or email.lower().endswith(('.invalid','.example','.test','@example.com','@example.org','@example.net'))
            or org.lower() in {'example','sample','test','your organization','your company'}):
        return None
    return org+' '+email


def private_identity(path=DEFAULT_PATH):
    """Return status and two fields; never disclose values in an exception."""
    path=Path(path).expanduser()
    try:
        if not path.exists() and not path.is_symlink():return 'MISSING',None
        if path.is_symlink() or path.parent.is_symlink():return 'INVALID',None
        file=path.stat();folder=path.parent.stat()
        if (not stat.S_ISREG(file.st_mode) or file.st_uid!=os.getuid() or folder.st_uid!=os.getuid()
                or stat.S_IMODE(file.st_mode)!=0o600 or stat.S_IMODE(folder.st_mode)!=0o700
                or file.st_size>8192):return 'INVALID',None
        values=json.loads(path.read_text())
        if not isinstance(values,dict) or set(values)!=SEC_IDENTITY_KEYS or not identity_string(values):return 'INVALID',None
        return 'PRESENT',{k:values[k].strip() for k in SEC_IDENTITY_KEYS}
    except (OSError,ValueError,UnicodeError):return 'INVALID',None


def child_environment(path=DEFAULT_PATH, parent=None):
    status,values=private_identity(path)
    if status!='PRESENT':raise ValueError('SEC_DEV_IDENTITY_'+status)
    environment=dict(os.environ if parent is None else parent)
    for key in SEC_IDENTITY_KEYS:environment.pop(key,None)
    environment.update(values)
    environment['PYTHON_DOTENV_DISABLED']='1'
    return environment
