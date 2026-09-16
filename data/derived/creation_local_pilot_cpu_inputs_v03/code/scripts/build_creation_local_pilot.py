"""Create a real-source plus labelled authored-example local development experiment."""
import argparse
import collections
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from research_log import hash_file


FAMILIES = {'muyaho':'무야호', 'doge':'Doge', 'no_meme':'밈 사용 안 함'}
SYSTEM = '너는 짧은 게시글과 이미지 문구를 쓰는 작가다. 요청에 맞는 완성 문구만 출력해라. 설명이나 분석을 덧붙이지 마라. 밈이 상황에 부적절하면 억지로 사용하지 마라.'


def prompt(family, brief):
    return f'{SYSTEM}\n대상: {FAMILIES[family]}\n요청: {brief}'


def grams(text):
    value = re.sub(r'\s+', '', text.lower())
    return collections.Counter(value[i:i+n] for n in (2,3) for i in range(max(0,len(value)-n+1)))


def retrieve(query, pool, family, top_k=2):
    # Family conditioned development task, not unseeded meme discovery.
    docs = [p for p in pool if p['family_id']==family and p.get('purpose')!='source_description_adaptation']
    if not docs:
        return []
    counts = [grams(d['prompt']+' '+d['target']) for d in docs]
    q = grams(query)
    df = collections.Counter(term for bag in counts for term in bag)
    idf = {t: math.log((1+len(docs))/(1+v))+1 for t,v in df.items()}
    def vector(bag): return {t:(1+math.log(c))*idf[t] for t,c in bag.items() if t in idf}
    qv=vector(q)
    qnorm=math.sqrt(sum(v*v for v in qv.values()))
    scores=[]
    for d, bag in zip(docs,counts):
        dv=vector(bag)
        denom=qnorm*math.sqrt(sum(v*v for v in dv.values()))
        score=sum(v*dv.get(t,0) for t,v in qv.items())/denom if denom else 0.0
        scores.append((score,d))
    return [{'example_id':d['example_id'],'score':score,'prompt':d['prompt'],'brief':d.get('brief',d['prompt']),'target':d['target'],'provenance':d['provenance']} for score,d in sorted(scores,key=lambda p:(-p[0],p[1]['example_id']))[:top_k]]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--steps',type=int,default=24)
    parser.add_argument('--case-limit',type=int,default=6)
    parser.add_argument('--device',choices=['cpu','cuda'],default='cpu')
    args=parser.parse_args()
    output=Path(args.output).resolve()
    if output.exists(): raise FileExistsError(output)
    if not output.is_relative_to(ROOT): raise ValueError('project output required')
    tasks_path=ROOT/'data/research/creation_local_pilot_tasks_v01.json'
    tasks=json.loads(tasks_path.read_text(encoding='utf-8'))
    model_path=ROOT/'data/models/qwen2_5_0_5b_instruct_pilot_v01'
    acquisition=json.loads((model_path/'acquisition_manifest.json').read_text(encoding='utf-8'))
    if acquisition['status']!='succeeded': raise ValueError('model acquisition incomplete')
    source_root=ROOT/'data/collected/multimodal_creation_pilot_sources_v01/text'
    knowledge={}
    sources=[]
    for family in ('muyaho','doge'):
        p=source_root/f'{family}_lead_ko.txt'
        knowledge[family]=p.read_text(encoding='utf-8').strip()
        sources.append({'family_id':family,'path':str(p),'sha256':hash_file(p),'provenance':'actual_collected',
                        'source_type':'Wikipedia_edited_description_not_original_performance',
                        'acquisition_mode':'known_seed_retrieval_not_discovery'})
    examples=[{**e,'prompt':prompt(e['family_id'],e['brief']),'provenance':'ai_synthetic',
               'prompt_provenance':'ai_synthetic','target_provenance':'ai_synthetic'} for e in tasks['authored_training_examples']]
    for family,description in knowledge.items():
        # Split on Korean sentence endings, keeping every original character
        # except inter-sentence whitespace. No future explanation is added.
        chunks = re.split(r'(?<=다\.)\s+', description)
        for index, chunk in enumerate(chunks):
            examples.append({'example_id':f'{family}_source_description_{index+1}','family_id':family,
                             'prompt':f'{FAMILIES[family]} 밈의 설명 중 {index+1}번째 사실을 알려 줘.','target':chunk,
                             'provenance':'mixed','prompt_provenance':'ai_synthetic','target_provenance':'actual_collected',
                             'purpose':'source_description_adaptation'})
    cases=[]
    for c in tasks['evaluation_requests'][:args.case_limit]:
        base=prompt(c['family_id'],c['brief'])
        matched=retrieve(c['brief'],examples,c['family_id'])
        context=[]
        if c['family_id'] in knowledge:
            context.append('밈 자료: '+knowledge[c['family_id']])
        for e in matched:
            # The same supervision targets are accessible to RAG and actual FT.
            context.append('참고 요청: '+e['brief']+'\n참고 답: '+e['target'])
        cr='\n'.join(context)+'\n\n'+base if context else base
        cases.append({**c,'c0_prompt':base,'cr_prompt':cr,'cf_prompt':base,
                      'retrieved_example_ids':[e['example_id'] for e in matched],
                      'retrieval_results':matched,'provenance':'ai_synthetic_development_request'})
    current=datetime.now(timezone.utc).isoformat()
    spec={'model_path':str(model_path),'revision':acquisition['revision'],
          'cases':cases,'train_examples':examples,
          'settings':{'device':args.device,'dtype':'float32' if args.device=='cpu' else 'float16','steps':args.steps,'rank':4,'alpha':8,
                      'learning_rate':0.001,'seed':1729,'max_input_tokens':512,'max_target_tokens':128,
                      'max_sequence_tokens':384,'max_new_tokens':64,'max_wall_seconds':900,
                      'max_rss_mb':4096,'max_cuda_allocated_mb':1800,'cpu_threads':2,
                      'gradient_checkpointing':True},
          'study_role':'development_text_proxy_with_fixed_renderer_not_full_multimodal_comparison',
          'source_cutoff':current,'inputs_built_at':current,
          'sources':sources,'provenance':'mixed','task_file':str(tasks_path),'task_file_sha256':hash_file(tasks_path),
          'source_pool_equal_for_rag_ft':True,'evaluation_targets_in_adaptation':False,
          'independent_human_gold':False,'temporal_change_effect_estimated':False,
          'renderer_weights_trained':False,'main_study_complete':False}
    output.mkdir(parents=True)
    for name,value in [('experiment_spec.json',spec),('shared_adaptation_pool.json',examples),('evaluation_requests.json',tasks['evaluation_requests'][:args.case_limit])]:
        with (output/name).open('x',encoding='utf-8') as handle: json.dump(value,handle,ensure_ascii=False,indent=2)
    print(json.dumps({'path':str(output/'experiment_spec.json'),'train_examples':len(examples),'cases':len(cases),'arms':3,'actual_sources':len(sources)}))


if __name__=='__main__': main()
