"""复用个人禁网LEAN runner；无HTTP、Key、生产或公司依赖。"""
import json,os,shutil,subprocess,uuid,hashlib
from pathlib import Path
from datetime import datetime,timezone
from .contracts import write_new
IMAGE='quantconnect/lean@sha256:31c6e80802110e666a14c74f6cad65ca2698cae4129d04401d7c43417d68ceca'

def docker_command():
    binary=shutil.which('docker')
    if not binary:raise ValueError('LOCAL_DOCKER_UNAVAILABLE')
    local=Path.home()/'.docker/run/docker.sock'
    socket=local if local.exists() else Path('/var/run/docker.sock')
    return [binary,'--host','unix://'+str(socket)]

def base_config():
    return {'parameters': {},
     'environment': 'backtesting',
     'live-mode': False,
     'algorithm-type-name': 'AVHistoryBenchmark',
     'algorithm-language': 'Python',
     'algorithm-location': '/algorithm/main.py',
     'algorithm-id': 'wp-lean-research',
     'data-folder': '/data',
     'results-destination-folder': '/results',
     'object-store-root': '/results/storage',
     'log-file-name': '/results/engine.log',
     'close-automatically': True,
     'benchmark-symbol': 'SPY',
     'debugging': False,
     'show-missing-data-logs': True,
     'job-user-id': '0',
     'job-project-id': 0,
     'job-organization-id': '',
     'api-access-token': '',
     'log-handler': 'QuantConnect.Logging.CompositeLogHandler',
     'messaging-handler': 'QuantConnect.Messaging.Messaging',
     'job-queue-handler': 'QuantConnect.Queues.JobQueue',
     'api-handler': 'QuantConnect.Api.Api',
     'map-file-provider': 'QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider',
     'factor-file-provider': 'QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider',
     'data-provider': 'QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider',
     'data-channel-provider': 'DataChannelProvider',
     'object-store': 'QuantConnect.Lean.Engine.Storage.LocalObjectStore',
     'data-aggregator': 'QuantConnect.Lean.Engine.DataFeeds.AggregationManager',
     'setup-handler': 'QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler',
     'result-handler': 'QuantConnect.Lean.Engine.Results.BacktestingResultHandler',
     'data-feed-handler': 'QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed',
     'real-time-handler': 'QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler',
     'history-provider': ['QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider'],
     'transaction-handler': 'QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler',
     'python-additional-paths': ['/algorithm'],
     'maximum-data-points-per-chart-series': 1000,
     'data-permission-manager': 'QuantConnect.Lean.Engine.DataFeeds.DataPermissionManager'}

def run(window: Path, *, algorithm_type: str = 'AVHistoryBenchmark',
        observation_file: str = 'history-observation.json', timeout: int = 240,
        simulation_orders: bool = False) -> dict:
    run_id='wp-lean-'+window.name.lower()+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]
    record=dict(run_id=run_id,image=IMAGE,platform='linux/arm64',network='none',orders_authorized=simulation_orders,
        started_at=datetime.now(timezone.utc).isoformat(),commands=[],source_head=subprocess.check_output(
            ['git','rev-parse','HEAD'],text=True,cwd=Path(__file__).parent,
            env={'PATH':'/usr/local/bin:/usr/bin:/bin','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'}).strip())
    output=window/'results';output.mkdir()
    config=base_config()
    config.update(parameters={},**{'algorithm-type-name':algorithm_type,'algorithm-id':run_id,'benchmark-symbol':window.name,
        'algorithm-location':'/algorithm/main.py','data-folder':'/data','results-destination-folder':'/results'})
    write_new(window/'config.json',config)
    record['input_hashes']={p.relative_to(window).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in window.rglob('*') if p.is_file()}
    docker=docker_command()
    argv=docker+['create','--name',run_id,'--pull','never','--platform','linux/arm64','--network','none','--read-only',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=512m','--tmpfs','/home/lean:rw,nosuid,nodev,size=128m',
        '--memory','3g','--memory-swap','3g','--cpus','2','--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--env','HOME=/home/lean','--env','DOTNET_CLI_HOME=/home/lean','--env','DOTNET_CLI_TELEMETRY_OPTOUT=1',
        '--env','PYTHONDONTWRITEBYTECODE=1','--env','OPENBLAS_NUM_THREADS=1',
        '--mount',f'type=bind,source={window}/data,target=/data,readonly',
        '--mount',f'type=bind,source={window}/algorithm,target=/algorithm,readonly',
        '--mount',f'type=bind,source={window}/config.json,target=/config/config.json,readonly',
        '--mount',f'type=bind,source={output},target=/results',IMAGE,'--config','/config/config.json']
    env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(window/'isolated-home'),'DOCKER_CONFIG':str(window/'isolated-docker-config')}
    def execute(args,name,timeout=45):
        record['commands'].append(args)
        with (window/name).open('xb') as stream:
            value=subprocess.run(args,cwd=window,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
        return value.returncode
    container=None
    try:
        if execute(argv,'create.log'):raise ValueError('CONTAINER_CREATE_FAILED')
        container=(window/'create.log').read_text().strip()
        execute(docker+['inspect',container],'inspect-before.json')
        record['attach_exit_code']=execute(docker+['start','--attach',container],'stdout.log',timeout)
        execute(docker+['inspect',container],'inspect-after.json')
        state=json.loads((window/'inspect-after.json').read_text())[0]['State'];record['exit_code']=state['ExitCode']
        record['has_native_output']=(output/observation_file).is_file()
        if record['has_native_output']:
            record['observation_sha256']=hashlib.sha256((output/observation_file).read_bytes()).hexdigest()
        record['status']='COMPLETED_REQUIRES_RECONCILIATION' if not record['attach_exit_code'] and not record['exit_code'] and record['has_native_output'] else 'FAILED'
    except Exception as error:
        record.update(status='FAILED',error_class=type(error).__name__)
        if container:execute(docker+['stop','--time','5',container],'stop.log')
    finally:
        if container:execute(docker+['rm',container],'remove.log')
        record['ended_at']=datetime.now(timezone.utc).isoformat()
        write_new(window/'run-manifest.json',record)
    return record
