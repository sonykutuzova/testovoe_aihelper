import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from aihelper.domain import AppError, Product, parse_json
from aihelper.provider import OpenAIProvider
from aihelper.service import generate_card, validate_draft
from aihelper.api import make_server


EXAMPLES = Path(__file__).resolve().parent.parent / 'examples'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'


class FixtureProvider:
    model = 'test'

    def generate(self, product, feedback=None):
        for stem in ('manometer', 'thermometer_demo', 'incomplete'):
            if product.name == source(stem)['name']:
                return draft(stem)
        raise AssertionError('Unknown test fixture')


def source(stem='manometer'):
    return json.loads((EXAMPLES / f'{stem}.json').read_text(encoding='utf-8'))


def draft(stem='manometer'):
    return json.loads((FIXTURES / f'{stem}.json').read_text(encoding='utf-8'))


class InputTests(unittest.TestCase):
    def test_invalid_json(self):
        for raw in ['{', '{"a":1,"a":2}', '{"x":NaN}', b'\xff']:
            with self.subTest(raw=raw), self.assertRaises(AppError):
                parse_json(raw)

    def test_input_size(self):
        with self.assertRaises(AppError) as cm:
            parse_json(' ' * 65537)
        self.assertEqual(cm.exception.status, 413)

    def test_empty_name_and_wrong_types(self):
        for data in [[], None, {}, {'name':' '}, {'name':1}, {'name':'x','specs':[]},
                     {'name':'x','specs':{'v':24}}, {'name':'x','extra':1}]:
            with self.subTest(data=data), self.assertRaises(AppError):
                Product.from_dict(data)

    def test_null_empty_missing(self):
        p = Product.from_dict(source('incomplete'))
        self.assertEqual(p.manufacturer, '')
        self.assertIn('питание', p.missing_fields())
        self.assertTrue(all(row['value'] == 'данных нет' for row in p.facts()))

    def test_preserve_exact_values(self):
        p = Product.from_dict(source())
        rows = {row['name']:row['value'] for row in p.facts()}
        for key,value in p.specs.items():
            self.assertEqual(rows[key], value)
        self.assertNotIn('точность', p.missing_fields())
        self.assertIn('корпус', p.missing_fields())  # Diameter is not material/type.

    def test_normalized_duplicate_spec(self):
        with self.assertRaises(AppError):
            Product.from_dict({'name':'x','specs':{'Питание':'1',' питание ':'2'}})


class ServiceTests(unittest.TestCase):
    def test_sample_cards(self):
        for stem in ('manometer','thermometer_demo','incomplete'):
            with self.subTest(stem=stem):
                card = generate_card(source(stem), FixtureProvider())
                self.assertLessEqual(len(card['seo_title']),70)
                self.assertTrue(800 <= len(card['seo_description']) <= 1200)
                self.assertTrue(3 <= len(card['search_queries']) <= 5)
                self.assertEqual(card['meta']['model'],'test')

    def test_reject_unsupported_numbers_and_codes(self):
        for phrase in [' Питание 220 В.', ' Степень защиты IP65.', ' Диапазон 0–16 МПа.']:
            d = draft(); d['seo_description'] += phrase
            with self.subTest(phrase=phrase):
                self.assertTrue(validate_draft(d,Product.from_dict(source())))

    def test_changed_sign_is_rejected(self):
        data = source('thermometer_demo')
        d = draft('thermometer_demo')
        d['seo_description'] = d['seo_description'].replace('−50…+150', '50…+150')
        self.assertTrue(validate_draft(d, Product.from_dict(data)))

    def test_title_description_queries_limits(self):
        for field,value in [('seo_title','x'*71),('seo_description','x'*799),('seo_description','x'*1201),
                            ('search_queries',['a','a','b']),('commercial',{}),('clarifications',[1])]:
            d = draft();d[field]=value
            with self.subTest(field=field):
                self.assertTrue(validate_draft(d,Product.from_dict(source())))

    def test_single_repair(self):
        class RepairProvider:
            mode='test';model='fake'
            def __init__(self):self.calls=0
            def generate(self,product,feedback=None):
                self.calls+=1
                d=draft()
                if self.calls == 1:d['seo_title']='x'*71
                else:assert feedback
                return d
        p=RepairProvider();card=generate_card(source(),p)
        self.assertEqual(p.calls,2)
        self.assertEqual(card['meta']['generation_attempts'],2)

    def test_fail_after_repair(self):
        class BadProvider:
            mode='test';model='fake'
            def generate(self,*args,**kwargs):return {}
        with self.assertRaises(AppError) as cm:
            generate_card(source(),BadProvider())
        self.assertEqual(cm.exception.code,'invalid_generation')

    def test_source_values_are_preserved(self):
        card = generate_card(source(), FixtureProvider())
        facts = {row['name']: row['value'] for row in card['characteristics']}
        for key, value in source()['specs'].items():
            self.assertEqual(facts[key], value)



class ProviderTests(unittest.TestCase):
    def test_missing_key(self):
        with self.assertRaises(AppError) as cm:
            OpenAIProvider(api_key='').generate(Product.from_dict(source()))
        self.assertEqual(cm.exception.code,'missing_api_key')

    def test_request_contract_and_response(self):
        def opener(request,timeout):
            self.assertEqual(request.full_url,'https://api.openai.com/v1/responses')
            body=json.loads(request.data)
            self.assertFalse(body['store'])
            self.assertTrue(body['text']['format']['strict'])
            self.assertIn('PRODUCT_DATA',body['input'][0]['content'])
            return io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[
                {'type':'output_text','text':json.dumps(draft())}]}]}).encode())
        result=OpenAIProvider(api_key='test-only',opener=opener).generate(Product.from_dict(source()))
        self.assertEqual(result,draft())

    def test_http_errors_and_retry_count(self):
        for status,calls,code in [(401,1,'api_auth'),(429,3,'api_rate_limit'),(500,3,'api_error'),(400,1,'api_error')]:
            count=[]
            def opener(*args,**kwargs):
                count.append(1)
                raise urllib.error.HTTPError('https://example.invalid',status,'secret body',{},io.BytesIO(b'secret'))
            with self.subTest(status=status),self.assertRaises(AppError) as cm:
                OpenAIProvider(api_key='test',opener=opener,sleep=lambda _:None).generate(Product.from_dict(source()))
            self.assertEqual(len(count),calls);self.assertEqual(cm.exception.code,code)
            self.assertNotIn('secret',str(cm.exception))

    def test_network_error(self):
        def opener(*args,**kwargs):raise urllib.error.URLError('private details')
        with self.assertRaises(AppError) as cm:
            OpenAIProvider(api_key='test',opener=opener).generate(Product.from_dict(source()))
        self.assertEqual(cm.exception.status,504)

    def test_malformed_incomplete_refusal(self):
        for raw,code in [(b'no json','provider_response'),(b'[]','provider_response'),
                         (b'{"status":"incomplete"}','incomplete_response'),
                         (b'{"status":"completed","output":[{"type":"message","content":[{"type":"refusal"}]}]}','model_refusal')]:
            with self.subTest(raw=raw),self.assertRaises(AppError) as cm:
                OpenAIProvider._extract(raw)
            self.assertEqual(cm.exception.code,code)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(FixtureProvider(),0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_health(self):
        with urllib.request.urlopen(self.url + '/health') as response:
            self.assertEqual(json.load(response), {'status': 'ok'})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url + "/unknown")
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()

    def test_ui_assets(self):
        for path, mime in [('/', 'text/html'), ('/app.js', 'text/javascript'), ('/style.css', 'text/css')]:
            with self.subTest(path=path), urllib.request.urlopen(self.url + path) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertTrue(response.read())

    def test_generate(self):
        req = urllib.request.Request(self.url + '/generate', data=json.dumps(source()).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as response:
            card = json.load(response)
        self.assertEqual(card['meta']['model'], 'test')
        self.assertEqual(card['seo_title'], draft()['seo_title'])

    def test_bad_json_and_origin(self):
        for body,headers,status in [(b'{',{'Content-Type':'application/json'},400),
                                    (b'{}',{'Content-Type':'text/plain'},415),
                                    (b'{}',{'Content-Type':'application/json','Origin':'https://evil.invalid'},403)]:
            req=urllib.request.Request(self.url+'/generate',data=body,headers=headers)
            with self.subTest(status=status),self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code,status)
            cm.exception.close()

if __name__ == '__main__':unittest.main()
