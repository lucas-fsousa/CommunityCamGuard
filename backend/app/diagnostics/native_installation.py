"""Host-only first handoff into the actual database; no HTTP or physical commands."""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..config import get_settings
from ..drivers.yoosee.native_install import prepare
from ..media.config_file import write_private_config
from ..media.server_owner import ServerOwner
from .trial_containers import TrialContainers, docker
from .trial_driver_offer import evidence_reference


async def bootstrap(camera_id: str, containers: TrialContainers, reference: str) -> None:
    from ..main import app
    async with app.router.lifespan_context(app):
        app.state.rec.pause()  # readiness only; avoid host writes into Docker-owned folders
        offer=await asyncio.to_thread(prepare,camera_id,app.state.rec.native_archive(camera_id),reference)
        retirement=app.state.owned_runtime.retirement
        receipt=await asyncio.to_thread(retirement.retire_legacy,camera_id,
            expected_revision=0,retire_ingress=containers.confirm_closed)
        await asyncio.to_thread(app.state.native_runtime.activate,retirement=retirement,receipt=receipt,
            offer=offer,apply_routes=lambda:app.state.media.write_config(),ready_timeout=30)
    if app.state.service_lifetime.owner.acquired:
        raise RuntimeError('native bootstrap cleanup unconfirmed')


def rollback(directory: Path) -> None:
    manifest=directory/'installation.json'
    if not manifest.exists() or (directory/'installed.json').exists(): return
    data=json.loads(manifest.read_text())
    for key in ('app','hub'):
        current=TrialContainers.inspect(data[key])
        if current['State']['Running']: docker('stop','--time','20',data[key])
        current=TrialContainers.inspect(data[key])
        if current['State']['Running'] or current['State']['Pid']!=0:
            raise RuntimeError('installation rollback owner still active')
    owner=ServerOwner(Path(data['database']))
    owner.acquire()
    try:
        if (directory/'backup-ready.json').exists():
            with closing(sqlite3.connect(directory/'original.db')) as source, closing(sqlite3.connect(data['database'])) as target:
                source.backup(target)
        write_private_config(Path(data['config']),(directory/'original-config.txt').read_text())
    finally:
        owner.close()
    docker('start',data['hub'])
    docker('start',data['app'])
    write_private_config(directory/'rolled-back.json','{"restored":true}')


def install(camera_id: str, directory: Path, evidence: Path) -> None:
    import os

    from ..config import Settings
    host=get_settings()
    database=host.db_path.resolve()
    config=host.go2rtc_config.resolve()
    containers=TrialContainers()
    environment=dict(value.split('=',1) for value in containers.app['Config']['Env'] if '=' in value)
    if environment.get('NATIVE_RESUME_ON_START','').lower()!='true':
        raise RuntimeError('container must enable NATIVE_RESUME_ON_START before installation')
    allowed={key.upper() for key in Settings.model_fields}
    os.environ.update({key:value for key,value in environment.items() if key in allowed})
    # Container paths are bind-mounted at these host paths for the CCG compose stack.
    os.environ.update(DB_PATH=str(database),RECORDINGS_DIR=str(host.recordings_dir.resolve()),
        GO2RTC_CONFIG=str(config),FRONTEND_DIR='frontend',
        AUTOSTART_SERVICES='false',CONTROL_SESSION_WARMUP='false',RECORDING_RETENTION_DAYS='0',
        PLAYBACK_PRETRANSCODE='false')
    get_settings.cache_clear()
    settings=get_settings()
    reference=evidence_reference(evidence)
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    write_private_config(directory/'original-config.txt',settings.go2rtc_config.read_text())
    write_private_config(directory/'installation.json',json.dumps({'app':containers.app['Id'],
        'hub':containers.hub['Id'],'database':str(settings.db_path),'config':str(settings.go2rtc_config)}))
    containers.stop_all()
    owner=ServerOwner(settings.db_path)
    owner.acquire()
    try:
        with closing(sqlite3.connect(settings.db_path)) as source, closing(sqlite3.connect(directory/'original.db')) as target:
            source.backup(target)
        (directory/'original.db').chmod(0o600)
        write_private_config(directory/'backup-ready.json','{"ready":true}')
    finally:
        owner.close()
    asyncio.run(bootstrap(camera_id,containers,reference))
    containers.start_hub()
    docker('start',containers.app['Id'])
    # Reuse the app's normal checkpoint startup, not a daemonized experiment.
    import time
    from urllib.error import URLError
    from urllib.request import HTTPCookieProcessor, Request, build_opener
    origin=f'http://localhost:{settings.port}'
    client=build_opener(HTTPCookieProcessor())
    client.addheaders=[('Origin',origin)]
    for _ in range(20):
        try:
            request=Request(origin+'/api/login',data=json.dumps({'key':settings.dashboard_secret_key}).encode(),
                            headers={'Content-Type':'application/json'})
            with client.open(request,timeout=5) as response: response.read()
            with client.open(origin+'/api/media/streams',timeout=5) as response:
                state=json.load(response)['source_states'][camera_id]
            if state['mode']=='native' and state['available']:
                write_private_config(directory/'installed.json',json.dumps({'camera_id':camera_id,'mode':'native'}))
                return
        except (URLError,KeyError,ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('native installation did not become available')
