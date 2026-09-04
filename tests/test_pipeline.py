import math,threading,unittest,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]));from analysis import cosine;from collect import classify
from fastapi.testclient import TestClient
from api import app
class T(unittest.TestCase):
 def test_math(self):self.assertAlmostEqual(cosine([1,0],[1,0]),1);self.assertAlmostEqual(cosine([1,0],[0,1]),0);self.assertFalse(math.isnan(cosine([0],[0])))
 def test_filter(self):self.assertFalse(classify('Connect your ads to CRM for full attribution')[0]);self.assertTrue(classify('My ads attribution is wrong and my leads are low quality')[0])
 def test_tenant(self):c=TestClient(app);self.assertEqual(c.get('/api/health').status_code,200);self.assertEqual(c.get('/api/evidence',headers={'X-Demo-Tenant':'other'}).status_code,404)
 def test_loopback_fixture(self):
  class H(BaseHTTPRequestHandler):
   def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'owned')
   def log_message(self,*x):pass
  s=HTTPServer(('127.0.0.1',0),H);t=threading.Thread(target=s.serve_forever);t.start();import urllib.request;self.assertEqual(urllib.request.urlopen(f'http://127.0.0.1:{s.server_port}',timeout=2).read(),b'owned');s.shutdown();t.join()
if __name__=='__main__':unittest.main()
