"""Small explicit-seed Wikimedia archive. Never collects evaluation gold."""
from __future__ import annotations
import hashlib, html, json, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ALLOWED = {'ko.wikipedia.org','commons.wikimedia.org','upload.wikimedia.org','creativecommons.org'}
LIMIT = 3 * 1024 * 1024

def now(): return datetime.now(timezone.utc).isoformat()
def digest(b): return hashlib.sha256(b).hexdigest()
def writej(path, value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def fetch(url, path):
    if urllib.parse.urlsplit(url).hostname not in ALLOWED: raise ValueError('Host outside collection scope')
    if path.exists() and path.with_suffix(path.suffix+'.receipt.json').exists():
        body=path.read_bytes(); receipt=json.loads(path.with_suffix(path.suffix+'.receipt.json').read_text(encoding='utf-8'))
        if digest(body)!=receipt['sha256'] or receipt['url']!=url:raise ValueError('Existing archive differs from recorded source')
        return body,receipt
    req=urllib.request.Request(url,headers={'User-Agent':'MemeResearchPilot/0.1 (bounded public Wikimedia archive)'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            if urllib.parse.urlsplit(r.url).hostname not in ALLOWED: raise ValueError('Redirect outside scope')
            body=r.read(LIMIT+1)
            if len(body)>LIMIT: raise ValueError('Response exceeds 3 MiB limit')
            path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists(): raise FileExistsError('Archive is immutable: '+str(path))
            path.write_bytes(body)
            item={'url':url,'final_url':r.url,'status':r.status,'content_type':r.headers.get('Content-Type'),'received_at':now(),'path':str(path.relative_to(BASE)),'sha256':digest(body),'bytes':len(body)}
            writej(path.with_suffix(path.suffix+'.receipt.json'),item)
            return body,item
    except Exception as exc:
        with (BASE/'fetch_failures.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps({'url':url,'at':now(),'error_type':type(exc).__name__,'error':str(exc)},ensure_ascii=False)+'\n')
        raise

def clean(s):
    s=re.sub(r'<sup\b[^>]*>.*?</sup>','',s,flags=re.S|re.I)
    return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>','',s))).strip()

def main():
    receipts=[]; records=[]
    articles=[('muyaho','무야호'),('doge','도지_(밈)')]
    for family,title in articles:
        url='https://ko.wikipedia.org/wiki/'+urllib.parse.quote(title)
        body,receipt=fetch(url,BASE/'raw'/(family+'_kowiki.html'));receipts.append(receipt)
        source=body.decode('utf-8')
        if 'https://creativecommons.org/licenses/by-sa/4.0' not in source: raise ValueError('Explicit text licence not found')
        paragraphs=[clean(x) for x in re.findall(r'<p\b[^>]*>(.*?)</p>',source,re.S|re.I)]
        paragraphs=[x for x in paragraphs if x and (x.startswith('무야호는') if family=='muyaho' else x.startswith('도지(') or x.startswith('도지 ('))]
        if not paragraphs: raise ValueError('Expected directly observed lead paragraph not found')
        target=BASE/'text'/(family+'_lead_ko.txt');target.parent.mkdir(exist_ok=True);target.write_text(paragraphs[0]+'\n',encoding='utf-8')
        rev=re.findall(r'(?:oldid=|"wgRevisionId":)(\d+)',source)
        records.append({'source_id':family+'_kowiki_lead','family_id':family,'title':title.replace('_',' '),'modality':'text','language':'ko','text':paragraphs[0],'source_url':url,'source_revision_candidates':list(dict.fromkeys(rev)),'source_role':'encyclopedic_description_not_original_meme_creation','verification_scope':'Downloaded article HTML, extracted first lead paragraph, checked explicit text licence link; linked broadcasts and cited reports not independently read','sampling':'known_seed_retrieval','provenance':'actual_collected','available_at':now(),'raw_received_at':receipt['received_at'],'source_group_id':family+'_kowiki_article','local_path':str(target.relative_to(BASE)),'sha256':digest(target.read_bytes()),'license':'CC-BY-SA-4.0','license_url':'https://creativecommons.org/licenses/by-sa/4.0/','attribution':title.replace('_',' ')+' — Korean Wikipedia contributors; article history linked from source page','changes':'Lead paragraph extracted; HTML/reference markers removed; whitespace normalized','human_reviewed':False,'purpose':'source_support_candidate','evaluation_gold':False})
    for aid,title in [('doge_taiki_original','Shiba_inu_taiki.jpg'),('doge_taiki_macro','Doge_meme.png')]:
        url='https://commons.wikimedia.org/wiki/File:'+title
        body,receipt=fetch(url,BASE/'raw'/(aid+'_commons.html'));receipts.append(receipt)
        source=body.decode('utf-8')
        link=re.search(r'class="fullImage(?:Link)?".*?<a href="([^"]+)"',source,re.S)
        if not link: raise ValueError('Original download URL not found on file page')
        image_url=html.unescape(link.group(1))
        if image_url.startswith('//'):image_url='https:'+image_url
        if aid.endswith('original') and 'release this work into the' not in source:raise ValueError('Public-domain declaration absent')
        if aid.endswith('macro') and 'https://creativecommons.org/licenses/by-sa/4.0' not in source:raise ValueError('CC licence absent')
        ext=Path(urllib.parse.urlsplit(image_url).path).suffix
        asset,ar=fetch(image_url,BASE/'images'/(aid+ext));receipts.append(ar)
        records.append({'source_id':aid,'family_id':'doge','title':title.replace('_',' '),'modality':'image','language':None,'source_url':url,'download_url':image_url,'source_role':'creator_uploaded_photo' if aid.endswith('original') else 'published_meme_variant','verification_scope':'Commons source/author/licence and original image bytes retrieved; identification is Taiki, not Kabosu; no claim of earliest Doge origin','sampling':'known_seed_retrieval','provenance':'actual_collected','available_at':ar['received_at'],'source_group_id':'taiki_roberto_vasarri_2008','parent_source_ids':[] if aid.endswith('original') else ['doge_taiki_original'],'local_path':ar['path'],'sha256':ar['sha256'],'license':'Public-domain author dedication' if aid.endswith('original') else 'CC-BY-SA-4.0','license_url':url+'#Licensing' if aid.endswith('original') else 'https://creativecommons.org/licenses/by-sa/4.0/','attribution':'Roberto Vasarri; Commons file page and its revision history; macro uploaded by Pangu, optimized by RokerHRO','changes':'None; original downloaded bytes preserved','human_reviewed':False,'purpose':'source_support_candidate','evaluation_gold':False})
    for aid,url in [('cc_by_sa_4_deed','https://creativecommons.org/licenses/by-sa/4.0/deed.ko')]:
        body,r=fetch(url,BASE/'raw'/(aid+'.html'));receipts.append(r)
    (BASE/'sources.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
    writej(BASE/'manifest.json',{'schema_version':'rights_documented_actual_source_candidates_v1','created_at':now(),'collection_code_sha256':digest(Path(__file__).read_bytes()),'sampling':'known_seed_retrieval','family_ids':sorted(set(x['family_id'] for x in records)),'sources':records,'http_receipts':receipts,'model_calls':0,'paid_cost_usd':0,'human_evaluations':0,'independent_gold_created':False,'scope':'Small support candidate bundle; text descriptions are Wikimedia editorial descriptions, not original broadcast evidence. Images only for Doge/Taiki. No video/audio collected. Current collected versions are available only now, not at historical meme dates. Attribution, licence links and changes must accompany redistributed CC derivatives.'})
    print(json.dumps({'sources':len(records),'families':len(set(x['family_id'] for x in records)),'http_requests':len(receipts),'manifest':str(BASE/'manifest.json')},ensure_ascii=False))

if __name__=='__main__':main()
