import json
import sys
import tomllib
from pathlib import Path
from .models import Probe
from .providers import APIProvider, ProviderError, credentials
from . import batch_transport

def run(root: Path, env_file: Path | None):
    config = tomllib.loads((root / 'config.toml').read_text())
    keys = credentials(env_file, root=root)
    report = []
    for role, settings in config['models'].items():
        try:
            if config.get('pipeline',{}).get('mode')=='batch' and settings['provider'] in {'gemini','vertex'}:
                if settings['provider']!='gemini':
                    raise batch_transport.BatchError('batch_requires_gemini_provider')
                cfg=config.get('batch',{})
                data=batch_transport.generate(batch_transport.request_body('Return JSON with ok=true.', {'probe':True},settings,Probe),
                    model=settings['model'],key=keys.get('gemini_api_key'),path=root/'evidence'/f'preflight-{role}-batch.json',
                    timeout=45,poll_interval=cfg.get('poll_interval_sec',15),wait_timeout=cfg.get('wait_timeout_sec',86400))
                from .providers import Completion
                result=Completion(Probe.model_validate_json(batch_transport.response_text(data)),data.get('usageMetadata',{}),0,data.get('modelVersion',settings['model']))
            else:
                result = APIProvider(settings, keys, 45, root=root).complete('Return JSON with ok=true.', {'probe': True}, Probe)
            report.append({'role': role, 'model': settings['model'], 'effort': settings['effort'], 'status': 'ok', 'usage': result.usage, 'response_model': result.response_model})
        except ProviderError as exc:
            report.append({'role': role, 'model': settings['model'], 'effort': settings['effort'], 'status': 'failed', 'code': exc.code, 'http_status': exc.status, 'diagnostics': exc.diagnostics})
        except batch_transport.BatchError as exc:
            report.append({'role':role,'model':settings['model'],'status':'pending' if isinstance(exc,batch_transport.BatchPending) else 'failed','code':exc.code})
    (root / 'evidence').mkdir(exist_ok=True)
    (root / 'evidence/preflight.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))
    return all(r['status'] == 'ok' for r in report)

if __name__ == '__main__':
    sys.exit(0 if run(Path.cwd(), Path(sys.argv[1]) if len(sys.argv) > 1 else None) else 2)
