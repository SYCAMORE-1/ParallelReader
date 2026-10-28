import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw, ImageChops
from reader.image_regions import qr_regions, preserve_codes
from reader.image_render import render_image
import test_image_translation as image_tests
from test_image_translation import sample
from test_reader import until


def code_fixture():
    image=Image.new('RGB',(600,600),'white'); draw=ImageDraw.Draw(image)
    for x,y in [(300,300),(410,300),(300,410)]:
        draw.rectangle((x,y,x+34,y+34),fill='black')
        draw.rectangle((x+5,y+5,x+29,y+29),fill='white')
        draw.rectangle((x+10,y+10,x+24,y+24),fill='black')
    draw.text((350,360),'OUTWIT',fill='black')
    draw.text((50,40),'Diagram title',fill='black')
    stream=io.BytesIO(); image.save(stream,format='PNG')
    return image,stream.getvalue()


class CodeProtectionTests(unittest.TestCase):
    def test_three_finder_patterns_protect_code_but_not_nearby_prose(self):
        image,data=code_fixture()
        boxes=qr_regions(data); self.assertEqual(len(boxes),1)
        value={'has_text':True,'blocks':[
            {'source':'Diagram title','translation':'图示标题','bbox':[75,60,250,100]},
            {'source':'OUTWIT','translation':'以智取胜','bbox':[580,590,690,620]},
            {'source':'Small brand mark','translation':'小标识','bbox':[580,590,690,620]},
            {'source':'Important caption','translation':'重要图注','bbox':[75,220,300,290]}], 'warnings':[]}
        filtered=preserve_codes(data,value)
        self.assertEqual([b['source'] for b in filtered['blocks']],['Diagram title','Important caption'])
        with tempfile.TemporaryDirectory() as directory:
            output=render_image(data,value,directory)
            actual=Image.open(Path(directory)/output['file']).convert('RGB')
            self.assertIsNone(ImageChops.difference(image.crop((295,295,450,450)),actual.crop((295,295,450,450))).getbbox())
        # An isolated square or a word with the same name is not grounds to discard text.
        plain=Image.new('RGB',(600,600),'white'); stream=io.BytesIO(); plain.save(stream,format='PNG')
        self.assertEqual(preserve_codes(stream.getvalue(),value)['blocks'],value['blocks'])

    def test_retained_label_does_not_need_to_fit_a_translation_box(self):
        image,data=code_fixture()
        result={'blocks':[{'source':'ABC123','translation':'ABC123','bbox':[10,10,11,11]}]}
        with tempfile.TemporaryDirectory() as directory:
            output=render_image(data,result,directory)
            actual=Image.open(Path(directory)/output['file']).convert('RGB')
            self.assertEqual(actual.tobytes(),image.tobytes())


class RenderRecoveryTests(unittest.TestCase):
    setUp = image_tests.ImageTranslationTests.setUp
    tearDown = image_tests.ImageTranslationTests.tearDown
    worker = image_tests.ImageTranslationTests.worker
    def test_review_survives_render_failure_restart_and_new_review_round(self):
        calls=[]
        worker=self.worker(lambda *args:(calls.append('review' if len(args)==6 else 'initial') or sample()))
        worker.render=lambda *args:(_ for _ in ()).throw(RuntimeError('测试排版失败'))
        worker.request(self.book,self.asset)
        until(lambda:worker.status(self.book,self.asset)['state']=='error')
        self.assertEqual(calls,['initial','review'])
        restored=self.worker(lambda *args:(calls.append('review' if len(args)==6 else 'initial') or sample()))
        self.assertEqual(restored.status(self.book,self.asset)['state'],'needs_render')
        restored.request(self.book,self.asset)
        until(lambda:restored.status(self.book,self.asset)['state']=='ready')
        self.assertEqual(calls,['initial','review'])
        restored.request(self.book,self.asset,review_only=True)
        until(lambda:restored.status(self.book,self.asset)['state']=='ready')
        self.assertEqual(calls,['initial','review','review'])
