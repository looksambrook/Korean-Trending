"""Derive counts and literal-copy diagnostics from actual immutable experiment outputs."""
import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from research_log import ResearchLog,hash_file

STUDIES=[
 ('text_T0','data/derived/creation_local_pilot_cpu_inputs_v03/experiment_spec.json','data/derived/creation_local_pilot_cpu_run_v03/run_summary.json','data/derived/creation_local_pilot_media_v03/render_summary.json'),
 ('native_image','data/derived/creation_native_pilot_inputs_v01/experiment_spec.json','data/derived/creation_native_pilot_run_v01/run_summary.json','data/derived/creation_native_pilot_media_v01/render_summary.json'),
 ('text_T1','data/derived/creation_incremental_inputs_v01/experiment_spec.json','data/derived/creation_incremental_run_v01/run_summary.json','data/derived/creation_incremental_media_v01/render_summary.json')]


def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC',value).casefold().strip(' \t\n\r\"\'“”‘’').split())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    output=Path(args.output_dir).resolve()
    if output.exists(): raise FileExistsError(output)
    if not output.is_relative_to(ROOT): raise ValueError('project output required')
    loaded=[(name,read(ROOT/s),read(ROOT/r),read(ROOT/m)) for name,s,r,m in STUDIES]
    snapshots=[{'id':name+'/'+kind,'sha256':hash_file(ROOT/path)} for name,s,r,m in STUDIES for kind,path in [('spec',s),('model_result',r),('render_result',m)]]
    registry=ResearchLog(ROOT/'logs/research.sqlite3')
    run=registry.begin({'objective':'Summarize actual creation experiments without inventing semantic or human scores',
                        'stage':'offline_validation','parameters':{'normalization':'Unicode NFKC + casefold + outer quotes/whitespace removal + whitespace collapse',
                        'copy_match_is_semantic_score':False,'study_names':[s[0] for s in STUDIES]},
                        'data_snapshots':snapshots,'code_hashes':{'summarizer':hash_file(__file__)},
                        'prompt_hashes':{},'config_hashes':{},'modality':['text','image','video'],'provenance':'mixed'})
    output.mkdir(parents=True)
    cases=[]
    studies=[]
    for name,source,result,media in loaded:
        responses={(r['case_id'],r['condition']):r for r in result.get('responses',[])}
        targets={}
        for example in source['train_examples']:
            targets.setdefault(normalized(example['target']),[]).append(example['example_id'])
        records=[]
        for case in source['cases']:
            for condition in ('C0','CR','CF'):
                response=responses.get((case['case_id'],condition),{})
                body=response.get('output','')
                matches=targets.get(normalized(body),[]) if body.strip() else []
                row={'study':name,'run_id':result['run_id'],'case_id':case['case_id'],'condition':condition,
                     'family_id':case['family_id'],'status':response.get('status','not_run'),
                     'output_tokens':response.get('output_tokens'),
                     'output_token_limit_reached':response.get('output_reached_token_limit'),
                     'input_truncated':response.get('input_truncated',False),
                     'normalized_training_target_match':bool(matches),'matching_example_ids':'|'.join(matches),
                     'body':body,'semantic_score':None,'human_rating':None}
                cases.append(row)
                records.append(row)
        before=result.get('base_hash_before')
        after=result.get('base_hash_after')
        studies.append({'study':name,'run_id':result['run_id'],'status':result['status'],
                        'planned_response_count':len(source['cases'])*3,'completed_responses':sum(r['status']=='completed' for r in records),
                        'optimizer_steps':result.get('optimizer_steps',0),'generation_attempts':result.get('generation_calls',0),
                        'base_hashes_measured_equal':before==after if before is not None and after is not None else None,
                        'adapter_changed':result.get('adapter_weights_changed'),
                        'wall_seconds':result.get('wall_seconds'),'sampled_peak_rss_bytes':result.get('peak_rss_bytes'),
                        'rendered_formats':media['completed_formats'],
                        'normalized_training_target_matches_by_arm':{arm:sum(r['condition']==arm and r['normalized_training_target_match'] for r in records) for arm in ('C0','CR','CF')},
                        'token_limit_counts_by_arm':{arm:sum(r['condition']==arm and bool(r['output_token_limit_reached']) for r in records) for arm in ('C0','CR','CF')},
                        'semantic_quality_not_evaluated':True})
    prior=loaded[0][2]
    incremental=loaded[2][2]
    old={(r['case_id'],r['condition']):r for r in prior['responses']}
    repeated=[]
    for r in incremental['responses']:
        earlier=old.get((r['case_id'],r['condition']))
        if earlier:
            repeated.append({'case_id':r['case_id'],'condition':r['condition'],
                             'output_text_unchanged':earlier.get('output')==r.get('output'),
                             'interpretation':'literal repeat diagnostic, not retained-meaning accuracy'})
    summary={'run_id':run['run_id'],'studies':studies,'main_experiment_generations':sum(s['completed_responses'] for s in studies),
             'main_experiment_optimizer_steps':sum(s['optimizer_steps'] for s in studies),
             'earlier_CPU_smoke_excluded_from_main_counts':{'run_id':'22e0491200f84f0e8640b139abb9c4b3','optimizer_steps':2,'completed_generations':3},
             'failed_GPU_attempt_excluded_from_success_counts':{'run_id':'f8e084e3d1ff467f8a1874fb37d7907d','completed_generations':0,'attempts':1,'optimizer_steps':0},
             'retained_request_output_changes':repeated,'human_ratings_count':0,'semantic_scores':None,
             'statistical_superiority_test_performed':False,'native_audio_model_evaluation_performed':False,
             'real_world_meme_trend_change_evaluated':False,'independent_family_generalization_evaluated':False,
             'native_video_stream_understanding_evaluated':False,'image_video_generator_weights_trained':False,
             'local_compute_cost_measured':False,'paid_GPU_or_model_API_charges_incurred':False,'paper_level_goal_completed':False}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'case_outputs.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'case_outputs.csv').open('x',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(cases[0]))
        writer.writeheader()
        writer.writerows(cases)
    registry.finish(run['run_id'],status='succeeded',notes='Counts, literal output changes and normalized training-target matches derived from actual runs. No semantic/human score or publication-readiness claim.',
                    artifacts=list(output.iterdir()),actual_cost={'amount':None,'currency':None,'api_units':0,'human_minutes':0})
    print(json.dumps({'summary':str(output/'summary.json'),'main_outputs':summary['main_experiment_generations'],
                      'main_optimizer_steps':summary['main_experiment_optimizer_steps'],'human_ratings':0}))


if __name__=='__main__': main()
