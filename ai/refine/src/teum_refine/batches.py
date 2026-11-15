"""Shared quiet-window collector; callers retain their budget reservation."""
import threading,time,uuid
from concurrent.futures import Future

class BatchCollector:
 def __init__(self,api,quiet=2,poll=30,cap=1000):
  self.api=api;self.quiet=quiet;self.poll=poll;self.cap=cap
  self.condition=threading.Condition();self.pending=[];self.running=False
 def call(self,params):
  future=Future()
  with self.condition:
   self.pending.append((uuid.uuid4().hex,params,future));self.last=time.monotonic()
   if not self.running:
    self.running=True;threading.Thread(target=self.run,daemon=True).start()
   self.condition.notify_all()
  return future.result()
 def run(self):
  while True:
   with self.condition:
    if not self.pending:self.running=False;return
    while len(self.pending)<self.cap:
     remaining=self.quiet-(time.monotonic()-self.last)
     if remaining<=0:break
     self.condition.wait(remaining)
    wave=self.pending[:self.cap];del self.pending[:self.cap]
   try:self.execute(wave)
   except Exception as exc:
    for _,_,future in wave:
     if not future.done():future.set_exception(exc)
 def execute(self,wave):
  remaining={cid:(params,future) for cid,params,future in wave}
  for attempt in range(3):
   batch=self.api.create(requests=[dict(custom_id=cid,params=p) for cid,(p,_) in remaining.items()])
   while batch.processing_status!='ended':
    time.sleep(self.poll)
    try:batch=self.api.retrieve(batch.id)
    except Exception as exc:  # transient poll failure: keep tracking the submitted batch
     print(f'batch poll retry {batch.id}: {type(exc).__name__}',file=__import__('sys').stderr,flush=True)
   retry={};seen=set()
   for entry in self.api.results(batch.id):
    cid=entry.custom_id
    if cid not in remaining or cid in seen:raise RuntimeError('Invalid batch result custom_id')
    seen.add(cid);params,future=remaining[cid];result=entry.result
    if result.type=='succeeded':future.set_result((result.message,batch.id));continue
    error=getattr(result,'error',None);error=getattr(error,'error',error)
    kind=getattr(error,'type',None)
    retryable=result.type in ('expired','canceled') or (result.type=='errored' and kind in ('rate_limit_error','api_error','overloaded_error'))
    if retryable and attempt<2:retry[cid]=(params,future)
    else:future.set_exception(RuntimeError(f'Batch result {result.type}: {kind}'))
   if seen!=set(remaining):raise RuntimeError('Missing batch results')
   remaining=retry
   if not remaining:return
