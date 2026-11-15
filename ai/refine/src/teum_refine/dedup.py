"""R6: CPU KURE cosine matching against archive and earlier batch members."""
import json,math,os
from datetime import datetime,timezone
from pathlib import Path
MODEL='nlpai-lab/KURE-v1'
THRESHOLD=0.77 # In-sample: 37 distinct scripts + dupset (2026-11-08).
THRESHOLD_HI=0.95
class Encoder:
    def __init__(self):self.model=None
    def encode(self,documents):
        if self.model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            torch.set_num_threads(min(4,os.cpu_count() or 1))
            self.model=SentenceTransformer(MODEL,device='cpu')
        texts=[d['title']+'\n'+'\n'.join(d['script']) for d in documents]
        for text in texts:
            if len(self.model.tokenizer.encode(text))>self.model.max_seq_length:raise ValueError('R6 input exceeds model context; not truncated')
        return self.model.encode(texts,batch_size=4,normalize_embeddings=True,show_progress_bar=False).tolist()
def cosine(a,b):
    if len(a)!=len(b):raise ValueError('R6 index embedding dimension mismatch')
    norm=math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
    if not norm:raise ValueError('R6 zero embedding')
    return max(-1.,min(1.,sum(x*y for x,y in zip(a,b))/norm))
def duplicate_item(other,score,threshold=THRESHOLD,threshold_hi=THRESHOLD_HI):
    if not -1<=threshold<=threshold_hi<=1:raise ValueError('R6 thresholds')
    if score<threshold:return []
    from .rules import issue
    return [issue('R6','human_review' if score>=threshold_hi else 'pass_with_flags',f'중복 의심: {other} (유사도 {score:.2f})',fix='기존 대본과 같은 내용인지 확인하고 새 내용으로 바꾸기')]
class Archive:
    def __init__(self,path,encoder=None,threshold=THRESHOLD,threshold_hi=THRESHOLD_HI):
        self.path=Path(path);self.encoder=encoder or Encoder();self.threshold=threshold;self.threshold_hi=threshold_hi
        duplicate_item('',0,threshold,threshold_hi)
        self.rows=[json.loads(l) for l in self.path.read_text().splitlines() if l.strip()] if self.path.exists() else []
        self.batch=[];self.accepted=[]
    def consider(self,d):
        vector=self.encoder.encode([d])[0]
        row=dict(content_id=d['content_id'],title=d['title'],created_at=d.get('created_at') or datetime.now(timezone.utc).isoformat(),version=d.get('version') if d.get('version') is not None else d.get('generator',{}).get('revision',0),embedding=vector)
        # Reruns and revisions of the same content ID are never duplicates.
        candidates=[(cosine(vector,r['embedding']),r['content_id']) for r in self.rows+self.batch if r['content_id']!=row['content_id']]
        if not candidates:return [],row
        score,other=max(candidates)
        return duplicate_item(other,score,self.threshold,self.threshold_hi),row
    def accept(self,row,decision):
        if decision in ('pass','pass_with_flags'):
            self.batch.append(row)
            self.accepted.append(row)
    def commit(self):
        import fcntl
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a+',encoding='utf-8') as f:
            fcntl.flock(f,fcntl.LOCK_EX);f.seek(0)
            known={(r['content_id'],json.dumps(r.get('version'),sort_keys=True)) for r in (json.loads(l) for l in f if l.strip())}
            for row in self.accepted:
                key=(row['content_id'],json.dumps(row['version'],sort_keys=True))
                if key not in known:f.write(json.dumps(row,ensure_ascii=False)+'\n');known.add(key)
            f.flush();os.fsync(f.fileno())
        self.accepted=[]
