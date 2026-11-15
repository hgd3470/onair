import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import anthropic,httpx
from teum_refine.__main__ import main
from teum_refine.llm import Client
class Parallel(unittest.TestCase):
 def test_order_and_attribution(self):
  class Fake:
   def __init__(self,*a,**kw):self.calls=[]
  class Encoder:
   def encode(self,ds):return [[1.,0.] for _ in ds]
  def review(d,c,*a):
   time.sleep(.01 if d['content_id']=='a' else .001)
   c.calls.append(dict(owner=d['content_id'],cached=False,usage=dict(input_tokens=1,output_tokens=2),cost_usd=.01))
   return [],{},[]
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);inputs=root/'in';inputs.mkdir()
   for i,cid in enumerate(('a','b','a')):
    (inputs/f'{i}.json').write_text(json.dumps(dict(content_id=cid,title='제목',production_type='창작각색',script=['짧은 본문'],sources=[])))
   for n in (1,4):
    out=root/str(n)
    with patch('teum_refine.__main__.Client',Fake),patch('teum_refine.__main__.review',review),patch('teum_refine.dedup.Encoder',Encoder),patch('sys.argv',['refine','run',str(inputs),'--out',str(out),'--workers',str(n),'--skip-safety']):main()
   for f in (root/'1').glob('*'):
    if f.name!='dedup-index.jsonl' and f.suffix in ('.json','.csv','.jsonl'):self.assertEqual(f.read_bytes(),(root/'4'/f.name).read_bytes())
   for f in (root/'4').glob('*.refine.json'):
    r=json.loads(f.read_text());self.assertEqual([c['owner'] for c in r['calls']],[r['content_id']])
   self.assertTrue(any(x['stage']=='R6' for x in json.loads((root/'4/b.refine.json').read_text())['items']))
 def test_transient_retries(self):
  for status in (429,529):
   response=httpx.Response(status,request=httpx.Request('POST','https://example.com'))
   create=Mock(side_effect=[anthropic.APIStatusError('transient',response=response,body=None),'ok'])
   with patch('teum_refine.llm.time.sleep'):
    self.assertEqual(Client.__new__(Client).request(create),'ok')
   self.assertEqual(create.call_count,2)
