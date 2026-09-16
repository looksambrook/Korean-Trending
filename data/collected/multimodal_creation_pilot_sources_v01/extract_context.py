"""Extract paragraphs from already archived HTML; no network or model call."""
import json,re
from pathlib import Path
from collect_sources import BASE,clean,now,digest

records=[]
for family in ('muyaho','doge'):
    source=BASE/'raw'/(family+'_kowiki.html')
    paragraphs=[clean(p) for p in re.findall(r'<p\b[^>]*>(.*?)</p>',source.read_text(encoding='utf-8'),re.S|re.I)]
    paragraphs=[p for p in paragraphs if p]
    for index,text in enumerate(paragraphs):
        records.append({'source_id':family+'_kowiki_paragraph_'+str(index),'family_id':family,'paragraph_index':index,'text':text,'source_html':str(source.relative_to(BASE)),'source_html_sha256':digest(source.read_bytes()),'available_at':now(),'provenance':'actual_collected','transformation':'HTML paragraph extraction; superscript references removed; whitespace normalized','source_role':'Wikipedia_editorial_text_not_independent_gold','license':'CC-BY-SA-4.0','source_group_id':family+'_kowiki_article','eligible_by_default':False,'note':'Full HTML paragraph candidates including page furniture; main study must select relevant source text, retain source attribution and not treat surrounding quotes as primary broadcast evidence.'})
target=BASE/'text'/'article_paragraph_candidates.jsonl'
if target.exists():raise FileExistsError(target)
target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
print(json.dumps({'path':str(target),'paragraphs':len(records)},ensure_ascii=False))
