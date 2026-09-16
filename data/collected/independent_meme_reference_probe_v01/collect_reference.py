"""Archive a small CC-BY-4.0 human-reference probe from its authors' repository."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,urllib.request,urllib.parse

ROOT=Path(__file__).resolve().parent
CAP=5000000
HOSTS={'api.github.com','raw.githubusercontent.com','aclanthology.org'}
def now():return datetime.now(timezone.utc).isoformat()
def sha(b):return hashlib.sha256(b).hexdigest()
def savej(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def fetch(url,name,receipts):
    path=ROOT/name;path.parent.mkdir(parents=True,exist_ok=True)
    if urllib.parse.urlsplit(url).hostname not in HOSTS:raise ValueError('Host outside probe')
    remaining=CAP-sum(x['bytes'] for x in receipts)
    if path.exists():
        data=path.read_bytes();receipt=json.loads(path.with_suffix(path.suffix+'.receipt.json').read_text(encoding='utf-8'))
        if sha(data)!=receipt['sha256'] or receipt['url']!=url:raise ValueError('Archive changed')
    else:
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'MemeReferenceProbe/0.1 (bounded research archive)'}),timeout=25) as r:
                data=r.read(remaining+1)
                if len(data)>remaining:raise ValueError('Total response bytes exceed 5 MB')
                path.write_bytes(data)
                receipt={'url':url,'final_url':r.url,'received_at':now(),'path':name,'bytes':len(data),'sha256':sha(data),'content_type':r.headers.get('Content-Type'),'status':r.status}
                savej(path.with_suffix(path.suffix+'.receipt.json'),receipt)
        except Exception as exc:
            with (ROOT/'failures.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'at':now(),'url':url,'error':str(exc)})+'\n')
            raise
    receipts.append(receipt);return data

def main():
    receipts=[]
    commit=json.loads(fetch('https://api.github.com/repos/npnkhoi/MemeInterpret/commits/master','raw/commit.json',receipts))['sha']
    base='https://raw.githubusercontent.com/npnkhoi/MemeInterpret/'+commit+'/'
    readme=fetch(base+'README.md','raw/README.upstream.md',receipts).decode('utf-8')
    if 'Annotations in this repository' not in readme.replace('**','') or 'CC BY 4.0' not in readme:raise ValueError('Annotation license declaration absent')
    raw=fetch(base+'data/memeinterpret/test_data.json','raw/test_data.upstream.json',receipts)
    data=json.loads(raw)
    entries=list(data.values()) if isinstance(data,dict) else data
    if not isinstance(entries,list):raise ValueError('Unexpected annotation root')
    sample=entries[:3]
    savej(ROOT/'protected_reference_sample.json',sample)
    schema={key:sorted({type(row.get(key)).__name__ for row in entries}) for key in entries[0]}
    # The PDF was read through the primary web tool. A bounded attempted
    # local copy exceeded the remaining byte cap; do not retry the transfer.
    savej(ROOT/'manifest.json',{'run_id':'703814ca7d6b46dd8b000d50aae54517','collected_at':now(),'dataset':'MemeInterpret','source_repository':'https://github.com/npnkhoi/MemeInterpret','source_commit':commit,'code_sha256':sha(Path(__file__).read_bytes()),'annotations_license':'CC-BY-4.0','annotations_license_source':base+'README.md','code_license':'not_confirmed_in_inspected_README; no upstream code imported','image_license':'separate Facebook Hateful Memes conditions; images not acquired','upstream_split':'test','upstream_annotation_rows':len(entries),'sample_rows':len(sample),'schema_observed':schema,'sample_selection':'first_3_rows_in_upstream_order_for_schema_probe_only','probe_exposed_image_ids':[r.get('img_path') for r in sample],'human_annotation_fields':['image_caption','surface_message','background_knowledge_list','meme_caption_list'],'machine_generated_field':'auto_image_caption','inherited_fields':['text','label','img_path'],'raw_annotations_provenance':'externally_human_authored_per_authors_paper_with_explicit_separate_machine_field','protected_reference_use':'External caption/understanding targets only; never insert targets or derived explanations into corresponding model input/training; this probe has not run an evaluation','historical_availability':'Acquired current versions only; human-annotation per-record creation times unknown; no retrospective T availability claim','eligible_creation_quality_gold':False,'new_human_evaluation_completed':False,'images_acquired':0,'upstream_model_overlap_unknown':True,'http_receipts':receipts,'retained_successful_response_bytes':sum(x['bytes'] for x in receipts),'adaptation_allowed':False,'access_role':'evaluation_reference_only','pdf_copy_retained':False,'paid_cost_usd':0,'model_calls':0,'human_review_minutes':0,'paper_read_scope':'Read sections 3.1-3.3, ethics/terms paragraph and Appendix A via primary ACL text. Did not reproduce or validate paper experimental scores.'})
    print(json.dumps({'manifest':str(ROOT/'manifest.json'),'rows':len(entries),'sample':len(sample),'schema':schema,'bytes':sum(x['bytes'] for x in receipts)},ensure_ascii=False))
if __name__=='__main__':main()
