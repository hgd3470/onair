"""Collector-owned credential/provider boundary; keep content-addressed logs."""
import json
import os
import re
import time
from pathlib import Path
import httpx
from dotenv import dotenv_values
from .providers import APIProvider, ProviderError, Completion
from . import batch_transport
from .core import digest, now, read, save

class CallFailure(RuntimeError):
    pass

def credentials(root):
    from .providers import credentials as load
    result=load(root=root)
    # Keep both credential types; the configured provider selects its own auth.
    # Runtime-only; never print values, prefixes, lengths, headers or exceptions.
    values=dotenv_values(root / '.env',interpolate=False)
    for key in ['naver_client_id','naver_client_secret','X-NCP-APIGW-API-KEY-ID','X-NCP-APIGW-API-KEY']:
        value=os.environ.get(key) or os.environ.get(key.upper()) or values.get(key) or values.get(key.upper())
        if value: result[key]=value
    return result

class Models:
    def __init__(self, root, config, keys, logs):
        self.root,self.config,self.keys,self.logs=root,config,keys,logs

    def batch_enabled(self, settings):
        mode=self.config['collection'].get('mode','standard')
        if mode not in {'standard','batch'}: raise CallFailure('invalid_collection_mode')
        enabled=mode=='batch' and settings['provider'] in {'gemini','vertex'}
        if enabled and settings['provider']!='gemini': raise CallFailure('batch_requires_gemini_provider')
        return enabled

    def batch_generate(self, body, settings, folder):
        cfg=self.config.get('batch',{})
        return batch_transport.generate(body,model=settings['model'],key=self.keys.get('gemini_api_key'),
            path=folder/'batch.json',timeout=self.config['collection']['model_timeout_sec'],
            poll_interval=cfg.get('poll_interval_sec',15),wait_timeout=cfg.get('wait_timeout_sec',86400))

    def call(self, stage, payload, schema):
        role='verifier' if stage in {'verify','rights_review'} else 'generator'
        settings=self.config['models'][role]
        use_batch=self.batch_enabled(settings)
        prompt=(self.root / 'prompts' / f'{stage}.txt').read_text()
        request={'stage':stage,'prompt':prompt,'payload':payload,'settings':settings,'schema':schema.model_json_schema()}
        if use_batch: request['execution_mode']='batch'
        folder=self.logs / (stage+'-'+digest(request)[:20])
        if (folder/'result.json').exists():
            return schema.model_validate(read(folder/'result.json')['value'])
        if not use_batch and (folder/'request.json').exists() and not (folder/'error.json').exists():
            raise CallFailure('ambiguous_previous_call_requires_new_run')
        if not use_batch and (folder/'error.json').exists():
            raise CallFailure('previous_call_failed_requires_new_run')
        save(folder/'request.json',request)
        try:
            if use_batch:
                started=time.monotonic()
                data=self.batch_generate(batch_transport.request_body(prompt,payload,settings,schema),settings,folder)
                try:
                    value=schema.model_validate_json(batch_transport.response_text(data))
                except ValueError:
                    raise batch_transport.BatchError('invalid_structured_output') from None
                out=Completion(value,data.get('usageMetadata',{}),time.monotonic()-started,data.get('modelVersion',settings['model']))
            else:
                out=APIProvider(settings,self.keys,self.config['collection']['model_timeout_sec'],root=self.root).complete(prompt,payload,schema)
        except batch_transport.BatchPending:
            raise
        except batch_transport.BatchError as exc:
            save(folder/'error.json',{'code':exc.code,'at':now()})
            # A failed/unknown batch is not a usable result or a fallback trigger.
            raise
        except ProviderError as exc:
            save(folder/'error.json',{'code':exc.code,'status':exc.status,'diagnostics':exc.diagnostics,'at':now()})
            raise CallFailure(exc.code) from None
        save(folder/'result.json',{'value':out.value.model_dump(),'model':out.response_model,'usage':out.usage,'elapsed_sec':out.elapsed_sec,'at':now()})
        return out.value

    def search(self, query, day):
        prompt=(f'한국 뉴스 출처 탐색. {day.isoformat()} 당일 실제 발생/공식 발표된 다음 사안의 기사와 공식 발표를 검색하라: {query}. '
                '독립된 국내 방송사·신문사 최소 4곳과 공식 기관을 찾는다. 미국 증시는 제외. '
                '제목, 실제 원문 URL, 게시일만 출력한다. 기억으로 URL을 만들지 마라. 근거 부족은 없다고 밝혀라. '
                '결과는 사실 검증이 아니라 후속 원문 다운로드 후보에만 사용된다.')
        settings=self.config['models']['generator']
        use_batch=self.batch_enabled(settings)
        request={'prompt':prompt,'settings':settings,'tool':'google_search'}
        if use_batch: request['execution_mode']='batch'
        folder=self.logs/('search-'+digest(request)[:20])
        if (folder/'result.json').exists(): return read(folder/'result.json')
        if not use_batch and (folder/'request.json').exists(): raise CallFailure('previous_search_incomplete_requires_new_run')
        save(folder/'request.json',request)
        model=settings['model']
        url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        headers={'x-goog-api-key':self.keys.get('gemini_api_key','')}
        body={'contents':[{'role':'user','parts':[{'text':prompt}]}],
              'tools':[{'google_search':{}}],
              'generationConfig':{'maxOutputTokens':12000,'thinkingConfig':{'thinkingLevel':settings['effort']}}}
        start=time.monotonic()
        try:
            if use_batch:
                data=self.batch_generate(body,settings,folder)
            elif settings['provider'] == 'vertex':
                api=APIProvider(settings,self.keys,self.config['collection']['model_timeout_sec'],root=self.root)
                try:
                    auth=api._get_auth_helper()
                    token=auth.get_token()
                    project=self.keys.get('gcp_project_id') or auth.project_id
                    if not project: raise CallFailure('vertex_search_missing_project')
                    location=self.keys.get('gcp_location') or settings.get('location') or 'us-central1'
                    endpoint=f'{location}-aiplatform.googleapis.com' if location!='global' else 'aiplatform.googleapis.com'
                    url=f'https://{endpoint}/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent'
                    headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'}
                    body['tools']=[{'googleSearch':{}}]
                except CallFailure:
                    raise
                except Exception:
                    raise CallFailure('vertex_search_auth_failed') from None
            elif settings['provider'] != 'gemini':
                raise CallFailure('unsupported_search_provider')
            elif not headers['x-goog-api-key']:
                raise CallFailure('gemini_search_missing_key')
            if not use_batch:
                r=httpx.post(url,headers=headers,json=body,timeout=self.config['collection']['model_timeout_sec'])
                if r.is_error: raise CallFailure('search_http_'+str(r.status_code))
                data=r.json()
            c=(data.get('candidates') or [{}])[0]
            if c.get('finishReason')!='STOP': raise CallFailure('search_incomplete')
            text=''.join(p.get('text','') for p in c.get('content',{}).get('parts',[]) if not p.get('thought'))
            metadata=c.get('groundingMetadata',{})
            # No grounding metadata => ungrounded model text is never a discovery source.
            chunks=metadata.get('groundingChunks',[])
            urls=[x['web']['uri'] for x in chunks if x.get('web',{}).get('uri')]
            if urls:
                urls+=re.findall(r'https?://[^\s<>\]\)"`]+',text)
            result={'text':text,'urls':list(dict.fromkeys(urls)),'grounding':metadata,
                    'usage':data.get('usageMetadata',{}),'model':data.get('modelVersion',model),
                    'elapsed_sec':round(time.monotonic()-start,3),'at':now()}
            save(folder/'result.json',result)
            return result
        except batch_transport.BatchPending:
            raise
        except Exception as exc:
            code=str(exc) if isinstance(exc,(CallFailure,batch_transport.BatchError)) else type(exc).__name__
            save(folder/'error.json',{'code':code,'at':now()})
            raise CallFailure(code) from None
