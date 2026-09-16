"""Bind source hashes to a small native-image/selected-frame development experiment."""
import argparse
import json
import shutil
import sys
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from research_log import hash_file


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--frame-zero')
    parser.add_argument('--frame-five')
    args=parser.parse_args()
    output=Path(args.output).resolve()
    if output.exists(): raise FileExistsError(output)
    if not output.is_relative_to(ROOT): raise ValueError('project output required')
    path=ROOT/'data/research/creation_native_image_tasks_v01.json'
    tasks=json.loads(path.read_text(encoding='utf-8'))
    assets={'original':ROOT/'data/collected/multimodal_creation_pilot_sources_v01/images/doge_taiki_original.jpg',
            'macro':ROOT/'data/collected/multimodal_creation_pilot_sources_v01/images/doge_taiki_macro.png'}
    def images(asset):
        p=assets[asset]
        return [{'local_path':str(p),'sha256':hash_file(p),'kind':'image','source_group_id':'taiki_roberto_vasarri_2008'}]
    train=[{**e,'images':images(e['asset']),'provenance':'ai_synthetic','target_provenance':'ai_synthetic',
            'visual_source_provenance':'actual_collected'} for e in tasks['training_examples']]
    # This is family-conditioned retrieval from the same accessible training
    # pool. No target to these new evaluation requests is present in that pool.
    cases=[]
    for row in tasks['cases']:
        selected=train[:2] if row['asset']=='original' else train[2:4]
        context='Reference examples:\n'+'\n'.join('Request: '+e['prompt']+'\nCaption: '+e['target'] for e in selected)
        cases.append({**row,'images':images(row['asset']),'c0_prompt':row['brief'],
                      'cr_prompt':context+'\nNow fulfill this new request: '+row['brief'],'cf_prompt':row['brief'],
                      'retrieved_example_ids':[e['example_id'] for e in selected],
                      'retrieval_method':'fixed_family_and_asset_role_selection_from_shared_pool',
                      'requested_outputs':['text','image','video']})
    if bool(args.frame_zero)!=bool(args.frame_five): raise ValueError('both frame paths required')
    if args.frame_zero:
        frames=[{'local_path':str(Path(p).resolve()),'sha256':hash_file(p),'kind':'selected_video_frame',
                 'frame_at_seconds':t,'source_group_id':'mazatlan_2018_non_meme_control'}
                for p,t in ((args.frame_zero,0),(args.frame_five,5))]
        brief='Write a short factual caption for these two frames from a video. Do not use a meme, invent an event, or claim to hear audio.'
        cases.append({'case_id':'native_video_control','family_id':'no_meme','brief':brief,'images':frames,
                      'c0_prompt':brief,'cr_prompt':brief,'cf_prompt':brief,'retrieved_example_ids':[],
                      'retrieval_method':'no_matching_meme_support_for_non_meme_control',
                      'requested_outputs':['text','image','video']})
    now=datetime.now(timezone.utc).isoformat()
    spec={'model_path':str(ROOT/'data/models/smolvlm_256m_7e3e67e'),
          'revision':'7e3e67edbbed1bf9888184d9df282b700a323964','asset_root':str(ROOT),
          'cases':cases,'train_examples':train,'provenance':'mixed',
          'settings':{'device':'cpu','dtype':'float32','steps':12,'rank':4,'alpha':8,'seed':1730,
                      'learning_rate':0.001,'max_input_tokens':512,'max_sequence_tokens':512,
                      'max_target_tokens':96,'max_new_tokens':48,'max_wall_seconds':900,'max_rss_mb':4096,
                      'max_images':2 if args.frame_zero else 1,'image_longest_edge':512,'cpu_threads':2,
                      'gradient_checkpointing':True},
          'inputs_built_at':now,'source_cutoff':now,'study_role':'native-image-development-diagnostic',
          'image_provenance':'actual_collected','prompt_target_provenance':'ai_synthetic',
          'same_source_lineage_between_adaptation_and_new_development_requests':True,
          'independent_test_family_generalization_claim':False,'independent_human_gold':False,
          'native_audio_supported':False,'video_observation':'two_selected_frames_only' if args.frame_zero else 'none',
          'korean_performance_measured':False,'renderer_weights_trained':False,'main_study_complete':False}
    output.mkdir(parents=True)
    for name,value in [('experiment_spec.json',spec),('shared_adaptation_pool.json',train)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    for name in ['src/creation_vision_backend.py','src/creation_model_backend.py','scripts/run_creation_vision_pilot.py',
                 'scripts/build_creation_native_pilot.py','data/research/creation_native_image_tasks_v01.json']:
        target=output/'code'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,target)
    print(json.dumps({'spec':str(output/'experiment_spec.json'),'cases':len(cases),'training_examples':len(train),'planned_steps':12}))


if __name__=='__main__': main()
