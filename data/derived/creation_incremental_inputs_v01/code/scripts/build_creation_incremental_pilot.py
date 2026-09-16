"""Bind a later actual source acquisition to a preserved earlier adapter."""
import argparse
import json
import shutil
import sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))
from research_log import hash_file
from build_creation_local_pilot import FAMILIES,prompt,retrieve


def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    output=Path(args.output).resolve()
    if output.exists(): raise FileExistsError(output)
    if not output.is_relative_to(ROOT): raise ValueError('project output required')
    old_spec_path=ROOT/'data/derived/creation_local_pilot_cpu_inputs_v03/experiment_spec.json'
    old_summary_path=ROOT/'data/derived/creation_local_pilot_cpu_run_v03/run_summary.json'
    previous=read(old_spec_path)
    prior=read(old_summary_path)
    if prior['status']!='completed' or not prior['adapter_weights_changed']: raise ValueError('prior actual training required')
    tasks_path=ROOT/'data/research/creation_incremental_tasks_v01.json'
    tasks=read(tasks_path)
    source_manifest_path=ROOT/'data/collected/multimodal_creation_media_sources_v02/manifest.json'
    source_manifest=read(source_manifest_path)
    new_source=next(r for r in source_manifest['records'] if r['source_id']=='monkeys_spinning_monkeys')
    old_cutoff=datetime.fromisoformat(previous['source_cutoff'])
    if datetime.fromisoformat(new_source['available_at'])<=old_cutoff:
        raise ValueError('new source must have become available after initial cutoff')
    family=tasks['new_family_id']
    FAMILIES[family]=tasks['new_family_label']
    new_examples=[{**e,'family_id':family,'prompt':prompt(family,e['brief']),
                   'provenance':'ai_synthetic','prompt_provenance':'ai_synthetic',
                   'target_provenance':'ai_synthetic','source_ids':['monkeys_spinning_monkeys']}
                  for e in tasks['new_training_examples']]
    new_examples.append({'example_id':'msm_shared_source_context','family_id':family,
                         'prompt':'Monkeys Spinning Monkeys의 확인된 출처 설명을 알려 줘.',
                         'target':tasks['new_source_context'],'provenance':'ai_synthetic',
                         'prompt_provenance':'ai_synthetic','target_provenance':'ai_synthetic',
                         'purpose':'source_description_adaptation','source_ids':['monkeys_spinning_monkeys']})
    pool=previous['train_examples']+new_examples
    cases=[]
    for row in tasks['new_cases']:
        base=prompt(family,row['brief'])
        selected=retrieve(row['brief'],pool,family)
        context=[tasks['new_source_context']]
        context.extend('참고 요청: '+e['brief']+'\n참고 답: '+e['target'] for e in selected)
        cases.append({**row,'c0_prompt':base,'cf_prompt':base,'cr_prompt':'\n'.join(context)+'\n\n'+base,
                      'retrieved_example_ids':[e['example_id'] for e in selected],
                      'requested_outputs':['text','image','video'],'evaluation_role':'newly_added_source_family'})
    for c in previous['cases']:
        if c['case_id'] in tasks['retained_case_ids']:
            cases.append({**c,'evaluation_role':'retained_original_request_after_update'})
    now=datetime.now(timezone.utc).isoformat()
    spec={**previous,'cases':cases,'train_examples':pool,'source_cutoff':now,'inputs_built_at':now,
          'initial_adapter_path':str(ROOT/'data/derived/creation_local_pilot_cpu_run_v03/worker/adapter.safetensors'),
          'prior_run_id':prior['run_id'],'prior_model_run_id':prior['run_id'],'prior_run_summary_path':str(old_summary_path),
          'initial_adapter_sha256':hash_file(ROOT/'data/derived/creation_local_pilot_cpu_run_v03/worker/adapter.safetensors'),
          'optimizer_reset_at_update':True,'prior_source_cutoff':previous['source_cutoff'],
          'new_actual_source_available_at':new_source['available_at'],'new_source_manifest':str(source_manifest_path),
          'new_source_manifest_sha256':hash_file(source_manifest_path),'update_task_file_sha256':hash_file(tasks_path),
          'settings':{**previous['settings'],'steps':len(pool)},
          'study_role':'controlled_later_corpus_addition_with_real_adapter_continuation',
          'real_world_meme_change_claim':False,'temporal_change_effect_estimated':False,
          'native_audio_understanding_test':False,'new_source_context_provenance':'ai_synthetic_derived_from_source_metadata',
          'old_evaluation_requests_retained_for_development_observation_not_new_independent_samples':True}
    output.mkdir(parents=True)
    (output/'experiment_spec.json').write_text(json.dumps(spec,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'shared_adaptation_pool.json').write_text(json.dumps(pool,ensure_ascii=False,indent=2),encoding='utf-8')
    for name in ['src/creation_incremental_backend.py','scripts/run_creation_incremental_pilot.py',
                 'scripts/build_creation_incremental_pilot.py','scripts/build_creation_local_pilot.py',
                 'data/research/creation_incremental_tasks_v01.json']:
        target=output/'code'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
    print(json.dumps({'spec':str(output/'experiment_spec.json'),'cases':len(cases),'steps':len(pool),
                      'initial_adapter_from_run':prior['run_id'],'source_available_after_previous_cutoff':True}))


if __name__=='__main__': main()
