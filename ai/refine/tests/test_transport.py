import hashlib,json,tempfile,unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as N
from unittest.mock import patch
import test_client_limits as helpers
from teum_refine.llm import usage_cost
from teum_refine.batches import BatchCollector

class Transport(unittest.TestCase):
 def test_hash_and_prompt_shape(self):
  hashes=[]
  for enabled in (True,False):
   with tempfile.TemporaryDirectory() as p:
    c=helpers.Limits().client(p,[helpers.Limits().response('end_turn')]);c.prompt_cache=enabled
    c.call('R4',{}, {},'test',system='fixed')
    hashes.append(c.calls[0]['input_hash'])
    self.assertEqual(c.client.messages.create.call_args.kwargs['system'],[dict(type='text',text='fixed',cache_control={'type':'ephemeral'})] if enabled else 'fixed')
  self.assertEqual(hashes,['52725e3338de1a1bd5e90e751e08572d73b4c27c3af1d56e284e25601b5b368a']*2)
 def test_cost(self):
  u=dict(input_tokens=100,output_tokens=200,cache_creation_input_tokens=300,cache_read_input_tokens=400)
  for rates in ((.1,.5),(2,10)):
   expected=(515*rates[0]+200*rates[1])/1e6
   self.assertAlmostEqual(usage_cost(u,rates),expected)
   self.assertAlmostEqual(usage_cost(u,rates,'batch'),expected/2)
 def test_batch_wave_retry_and_online_equivalence(self):
  class API:
   def __init__(self):self.waves=[]
   def create(self,requests):
    self.waves.append(requests);return N(id=str(len(self.waves)),processing_status='ended')
   def results(self,bid):
    for i,r in enumerate(reversed(self.waves[int(bid)-1])):
     result=N(type='errored',error=N(type='overloaded_error')) if bid=='1' and i==0 else N(type='succeeded',message=helpers.Limits().response('end_turn'))
     yield N(custom_id=r['custom_id'],result=result)
  api=API();collector=BatchCollector(api,quiet=.05,poll=0)
  with tempfile.TemporaryDirectory() as root:
   def run(i):
    from pathlib import Path
    p=Path(root)/str(i);p.mkdir();c=helpers.Limits().client(p,[]);c.api_mode='batch';c.collector=collector
    return json.dumps(c.call('R4',{'i':i},{},'test'),sort_keys=True)
   with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
   self.assertEqual([len(w) for w in api.waves],[4,1])
   p=__import__('pathlib').Path(root)/'online';p.mkdir();c=helpers.Limits().client(p,[helpers.Limits().response('end_turn')])
   self.assertEqual(results,[json.dumps(c.call('R4',{}, {},'test'),sort_keys=True)]*4)
