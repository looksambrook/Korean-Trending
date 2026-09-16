"""Bounded actual media acquisition. At most one audio and one control video."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,html,json,re,urllib.request,urllib.parse

ROOT=Path(__file__).resolve().parent
ALLOWED={'commons.wikimedia.org','upload.wikimedia.org','incompetech.com','newsroom.tiktok.com','creativecommons.org'}
def now():return datetime.now(timezone.utc).isoformat()
def sha(b):return hashlib.sha256(b).hexdigest()
def savej(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def get(url,name,limit=20000000):
    p=ROOT/name;p.parent.mkdir(parents=True,exist_ok=True)
    if urllib.parse.urlsplit(url).hostname not in ALLOWED:raise ValueError('Host not allowed')
    if p.exists():
        r=json.loads(p.with_suffix(p.suffix+'.receipt.json').read_text(encoding='utf-8'));b=p.read_bytes()
        if sha(b)!=r['sha256'] or r['url']!=url:raise ValueError('Existing raw source changed')
        return b,r
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'MemeResearchPilot/0.2 (bounded rights-documented media archive)'})
        with urllib.request.urlopen(req,timeout=40) as r:
            if urllib.parse.urlsplit(r.url).hostname not in ALLOWED:raise ValueError('Redirect outside scope')
            b=r.read(limit+1)
            if len(b)>limit:raise ValueError('File exceeds 20 MB cap')
            p.write_bytes(b);v={'url':url,'final_url':r.url,'received_at':now(),'status':r.status,'content_type':r.headers.get('Content-Type'),'path':name,'bytes':len(b),'sha256':sha(b)}
            savej(p.with_suffix(p.suffix+'.receipt.json'),v);return b,v
    except Exception as e:
        with (ROOT/'failures.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'url':url,'at':now(),'error':str(e)},ensure_ascii=False)+'\n')
        raise

def main():
    receipts=[];records=[]
    pages=[('composer','https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1400011'),('audio_commons','https://commons.wikimedia.org/wiki/File:Kevin_MacLeod_~_Monkeys_Spinning_Monkeys.ogg'),('video_commons','https://commons.wikimedia.org/wiki/File:Video_en_el_Centro_de_Mazatl%C3%A1n,_2018.webm'),('tiktok_usage','https://newsroom.tiktok.com/trending-on-tiktok-endless-inspirationalquotes?lang=en'),('cc_by4','https://creativecommons.org/licenses/by/4.0/deed.en')]
    bodies={}
    for key,url in pages:
        body,r=get(url,'raw/'+key+'.html');receipts.append(r);bodies[key]=body.decode('utf-8',errors='replace')
    for sid,page,ext,license_name,role in [('monkeys_spinning_monkeys','audio_commons','ogg','CC-BY-4.0','documented_reused_comedic_soundtrack_candidate'),('mazatlan_2018','video_commons','webm','CC0-1.0','nonmeme_control_candidate')]:
        body=bodies[page]
        marker='creativecommons.org/licenses/by/4.0' if ext=='ogg' else 'creativecommons.org/publicdomain/zero/1.0'
        if marker not in body:raise ValueError('Expected licence not directly observed')
        pat=r'https://upload\.wikimedia\.org/wikipedia/commons/[0-9a-f]/[0-9a-f]{2}/[^"\s<>]+\.'+ext+r'(?:\?[^"\s<>]*)?'
        urls=re.findall(pat,body)
        if not urls:raise ValueError('No original media URL in directly read page')
        url=html.unescape(urls[0]);b,r=get(url,'media/'+sid+'.'+ext);receipts.append(r)
        records.append({'source_id':sid,'modality':'audio' if ext=='ogg' else 'video','role':role,'provenance':'actual_collected','sampling':'known_seed_retrieval' if ext=='ogg' else 'nonmeme_control_convenience_selection','local_path':r['path'],'download_url':url,'source_url':dict(pages)[page],'available_at':r['received_at'],'sha256':r['sha256'],'bytes':len(b),'author':'Kevin MacLeod (incompetech.com)' if ext=='ogg' else 'El Nuevo Doge','license':license_name,'license_url':'https://creativecommons.org/licenses/by/4.0/' if ext=='ogg' else 'https://creativecommons.org/publicdomain/zero/1.0/','changes':'Original retrieved bytes unchanged','source_group_id':'kevin_macleod_monkeys_spinning_monkeys_USUAN1400011' if ext=='ogg' else 'el_nuevo_doge_mazatlan_2018','published_or_created_date_as_source_states':'2014-02-03' if ext=='ogg' else '2018-07; uploaded 2022-08-08','content_read_scope':'Author/description/licence page and original bytes retrieved; full audio/video not semantically reviewed by this collection step','human_reviewed':False,'independent_gold':False,'usage_evidence_url':dict(pages)['tiktok_usage'] if ext=='ogg' else None,'usage_evidence_scope':'Official TikTok editorial page describes repeated use for comedic videos; platform videos themselves not downloaded or independently examined' if ext=='ogg' else 'Ordinary street video per uploader description; nonmeme role is a control candidate assignment, not independently annotated gold','training_supervision':False})
    savej(ROOT/'manifest.json',{'created_at':now(),'run_id':'80372646d6d641508450889dfe1009d5','code_sha256':sha(Path(__file__).read_bytes()),'records':records,'http_receipts':receipts,'audio_files':1,'video_files':1,'actual_meme_video_files':0,'control_video_files':1,'paid_cost_usd':0,'research_model_calls':0,'human_evaluations':0,'scope':'Audio is an actually published soundtrack with documented repeated comedic reuse, not a synthetic sound effect or proof every use is a meme. Video is an explicitly separate nonmeme control candidate. No claim of current popularity or independent original variations.'})
    print(json.dumps({'manifest':str(ROOT/'manifest.json'),'media':[(r['source_id'],r['bytes']) for r in records]},ensure_ascii=False))
if __name__=='__main__':main()
