import atexit, importlib.abc, json, os, sys
forbidden=('app', 'backend', 'decision_engine', 'intent_engine', 'research_v2', 'services', 'sqlalchemy', 'sqlite3', 'ib_async', 'tigeropen', 'futu', 'openai')
violations=[]
def save():
 with open('isolation.json', 'w') as handle:
  json.dump({'violations':violations, 'business_modules_loaded':
   [n for n in sys.modules if n.split('.')[0] in forbidden]}, handle)
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in forbidden:
   violations.append('business_import:' + fullname)
   save()
   raise RuntimeError('Business import forbidden in synthetic PoC')
sys.meta_path.insert(0, Guard())
def audit(event, args):
 if event == 'socket.connect':
  violations.append('outbound_connect')
  save()
  raise RuntimeError('Outbound connection forbidden in synthetic PoC')
 if event == 'open' and isinstance(args[0], (str, bytes)):
  name=os.fsdecode(args[0]); base=os.path.basename(name)
  protected=os.path.realpath(name).startswith('/Users/songbin/Documents/GitHub/WealthPilot/data' + os.sep)
  if protected or base.startswith('.env') or base.endswith(('.db', '.pem')):
   violations.append('protected_file_access')
   save()
   raise RuntimeError('DB/env/credential access forbidden in synthetic PoC')
sys.addaudithook(audit)
save()
atexit.register(save)

# Temporary test observer; no change to the MCP server or business code.
import threading, time
def observe_fixture(frame, event, arg):
 if event == 'return' and frame.f_code.co_name == 'get_synthetic_wealth_summary' and frame.f_code.co_filename == '/Users/songbin/Documents/GitHub/WealthPilot/pocs/poc01_private_mcp/server.py':
  if frame.f_locals.get('currency') == 'CNY' and arg is not None:
   record={'observed_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'tool':'get_synthetic_wealth_summary', 'arguments':{'currency':'CNY'}, 'result':arg.model_dump(), 'source':'Python profile return event in isolated server'}
   with open('fixture-calls.jsonl','a') as handle:
    handle.write(json.dumps(record,ensure_ascii=False)+'\n')
sys.setprofile(observe_fixture)
threading.setprofile(observe_fixture)
