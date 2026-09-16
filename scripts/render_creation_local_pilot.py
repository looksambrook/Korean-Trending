"""Render every planned model response through one fixed compositor and report failures."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from research_log import ResearchLog, hash_file
from creation_artifact_renderer import render_plan


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--spec',required=True)
    parser.add_argument('--run-summary',required=True)
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    source=read(args.spec)
    model=read(args.run_summary)
    output=Path(args.output_dir).resolve()
    if output.exists(): raise FileExistsError(output)
    if not output.is_relative_to(ROOT): raise ValueError('project output required')
    image_path=ROOT/'data/collected/multimodal_creation_pilot_sources_v01/images/doge_taiki_original.jpg'
    registry=ResearchLog(ROOT/'logs/research.sqlite3')
    spec={'objective':'Create and decode actual text/image/static-video artifacts from all planned C0 CR CF responses',
          'stage':'offline_validation','parameters':{'width':960,'height':540,'fps':12,'seconds':3,
                    'styles':['caption_card','image_caption'],'source_model_run_id':model['run_id'],
                    'all_planned_cases_retained':True,'renderer_weights_trained':False},
          'data_snapshots':[{'id':'model_outputs','sha256':hash_file(args.run_summary)},
                            {'id':'input_spec','sha256':hash_file(args.spec)},
                            {'id':'taiki_source_image','sha256':hash_file(image_path)}],
          'code_hashes':{'renderer':hash_file(ROOT/'src/creation_artifact_renderer.py'),'wrapper':hash_file(__file__)},
          'prompt_hashes':{},'config_hashes':{},'modality':['text','image','video'],'provenance':'mixed'}
    run=registry.begin(spec)
    output.mkdir(parents=True)
    records=[]
    actual={(r['case_id'],r['condition']):r for r in model.get('responses',[])}
    try:
        for case in source['cases']:
            for condition in ('C0','CR','CF'):
                response=actual.get((case['case_id'],condition))
                response_id=case['case_id']+'_'+condition
                row={'response_id':response_id,'case_id':case['case_id'],'family_id':case['family_id'],
                     'condition':condition,'body':None,'image_path':None,'video_path':None,
                     'status':'generation_not_completed','model_run_id':model['run_id'],
                     'quality_evaluated':False,'human_reviewed':False}
                if not response or response.get('status')!='completed':
                    row['generation_status']=response.get('status') if response else 'not_run'
                    records.append(row)
                    continue
                body=response.get('output','')
                row.update(body=body,generation_status='completed',
                           output_reached_token_limit=response.get('output_reached_token_limit'),
                           input_truncated=response.get('input_truncated'),
                           generation_wall_seconds=response.get('wall_seconds'))
                if not body.strip():
                    row['status']='empty_generation'
                    records.append(row)
                    continue
                plan={'schema_version':'creation-plan-v1','plan_id':response_id,
                      'provenance':'ai_synthetic','plan_origin':'model_generated','model_run_id':model['run_id'],
                      'text':body,'visual':{'style':'image_caption' if case['family_id']=='doge' else 'caption_card',
                       'width':960,'height':540,'caption':body},'video':{'duration_seconds':3,'fps':12}}
                if case['family_id']=='doge': plan['source_image_path']=str(image_path)
                destination=output/'artifacts'/response_id
                manifest=render_plan(plan,destination)
                row.update(status=manifest['status'],renderer_manifest=str(destination/'manifest.json'),
                           plan_construction='Fixed code/layout plus verbatim model body; no model-generated layout claim',
                           artifact_errors=manifest.get('error'),
                           completed_formats=list(manifest['artifacts']))
                for kind in ('text','image','video'):
                    if kind in manifest['artifacts']:
                        row[kind+'_path']=str(destination/manifest['artifacts'][kind]['path'])
                records.append(row)
        summary={'schema_version':'creation-render-batch-v1','run_id':run['run_id'],
                 'created_at':datetime.now(timezone.utc).isoformat(),'model_run_id':model['run_id'],
                 'outputs':records,'planned_responses':len(source['cases'])*3,
                 'render_succeeded':sum(r['status']=='succeeded' for r in records),
                 'completed_formats':{kind:sum(bool(r.get(kind+'_path')) for r in records) for kind in ('text','image','video')},
                 'evaluation_scores':None,'human_ratings':None,'full_study_complete':False,
                 'scope':'Development text adaptation with fixed caption/image cards and 3-second silent static-card video',
                 'native_audio_input_verified':False,'native_video_input_verified':False,
                 'image_video_generator_weights_trained':False,'model_quality_inferred_from_file_decoding':False}
        (output/'render_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        credits={'image':{'author':'Roberto Vasarri','subject':'Taiki, not Kabosu',
                          'source_url':'https://commons.wikimedia.org/wiki/File:Shiba_inu_taiki.jpg',
                          'license_or_use_basis':'Public-domain dedication on creator source page',
                          'changes':'Resized and placed in a fixed panel with verbatim generated text'},
                 'text_support':{'source_manifest':'data/collected/multimodal_creation_pilot_sources_v01/manifest.json',
                                 'authors':'Korean Wikipedia contributors','license':'CC-BY-SA-4.0',
                                 'license_url':'https://creativecommons.org/licenses/by-sa/4.0/'},
                 'generated_text_origin':'Qwen base or actual LoRA model outputs, not human gold'}
        (output/'attributions.json').write_text(json.dumps(credits,ensure_ascii=False,indent=2),encoding='utf-8')
        # Import after actual artifacts exist; report failure cannot replace results.
        from creation_pilot_report import build_report
        build_report(source,model,summary,output/'report')
        registry.finish(run['run_id'],status='succeeded',notes=f"Rendered {summary['render_succeeded']} of {summary['planned_responses']} planned outputs; failures retained. No independent quality ratings, native audio/video understanding or renderer training claimed.",
                        artifacts=[p for p in output.rglob('*') if p.is_file()],
                        actual_cost={'amount':None,'currency':None,'api_units':0,'human_minutes':0})
        print(json.dumps({'run_id':run['run_id'],'model_run_id':model['run_id'],
                          'render_succeeded':summary['render_succeeded'],'planned':summary['planned_responses'],
                          'report':str(output/'report/gallery.html')}))
    except Exception as exc:
        failure={'error_type':type(exc).__name__,'message':str(exc),'completed_records':records}
        (output/'failure.json').write_text(json.dumps(failure,ensure_ascii=False,indent=2),encoding='utf-8')
        registry.finish(run['run_id'],status='failed',notes='Rendering/reporting failed; partial artifacts retained.',
                        artifacts=[p for p in output.rglob('*') if p.is_file()],
                        actual_cost={'amount':None,'currency':None,'api_units':0,'human_minutes':0})
        raise


if __name__=='__main__': main()
