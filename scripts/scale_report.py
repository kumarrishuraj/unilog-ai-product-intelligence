"""Summarise a completed run's products.json into headline scale metrics."""
import json, sys, collections, statistics
from pathlib import Path
p = Path(sys.argv[1] if len(sys.argv)>1 else 'data/processed/sample_1000_input_products.json')
d = json.loads(p.read_text(encoding='utf-8'))
n = len(d)
conf = [x['confidence'] for x in d]
attr_fill = [sum(1 for a in x['attributes'] if a['value'])/max(1,len(x['attributes'])) for x in d]
lov_tot = lov_ok = ev_tot = ev_ok = 0
desc_counts = collections.Counter()
for x in d:
    for a in x['attributes']:
        if a['value'] and a['lov_compliant'] is not None:
            lov_tot += 1; lov_ok += bool(a['lov_compliant'])
        if a['value']:
            ev_tot += 1; ev_ok += bool(a['evidence'])
    for k,v in x['descriptions'].items():
        if v['value']: desc_counts[k]+=1
print(f'{"rows":26s}{n}')
print(f'{"status":26s}{dict(collections.Counter(x["status"] for x in d))}')
print(f'{"confidence bands":26s}{dict(collections.Counter(x["confidence_band"] for x in d))}')
print(f'{"mean confidence":26s}{statistics.mean(conf):.3f}   median {statistics.median(conf):.3f}')
print(f'{"classified (non-generic)":26s}{sum(1 for x in d if x["leaf_id"] and x["leaf_id"]!="generic_product")}  ({sum(1 for x in d if x["leaf_id"] and x["leaf_id"]!="generic_product")/n:.1%})')
print(f'{"brand resolved":26s}{sum(1 for x in d if x["brand"]["value"])}  ({sum(1 for x in d if x["brand"]["value"])/n:.1%})')
print(f'{"manufacturer resolved":26s}{sum(1 for x in d if x["manufacturer"]["value"])}  ({sum(1 for x in d if x["manufacturer"]["value"])/n:.1%})')
print(f'{"mean attribute fill":26s}{statistics.mean(attr_fill):.1%}')
print(f'{"LOV compliance":26s}{lov_ok}/{lov_tot}  ({lov_ok/max(1,lov_tot):.1%})')
print(f'{"attribute evidence cov.":26s}{ev_ok}/{ev_tot}  ({ev_ok/max(1,ev_tot):.1%})')
print(f'{"validation errors":26s}{sum(len([i for i in x["issues"] if i["severity"]=="error"]) for x in d)}')
print(f'{"rows w/ 0 errors":26s}{sum(1 for x in d if not [i for i in x["issues"] if i["severity"]=="error"])/n:.1%}')
print('\ndescription field population:')
for k,v in desc_counts.most_common(): print(f'   {k:24s}{v:5d}  {v/n:6.1%}')
print('\ntop review reasons:')
fl = collections.Counter(f['reason'] for x in d for f in x['review_flags'])
for k,v in fl.most_common(8): print(f'   {v:5d}  {k}')
print('\nbrand resolution methods:')
for k,v in collections.Counter(x['brand']['method'] for x in d).most_common(): print(f'   {v:5d}  {k}')
