# Script om de pending_intersections.json te converteren naar het nieuwe gegroepeerde formaat

import json
from collections import defaultdict

# Lees oude pending data
with open('pending_intersections.json', 'r', encoding='utf-8') as f:
    old_pending = json.load(f)

# Groepeer op projectnummer
grouped = defaultdict(list)
for item in old_pending:
    project_num = item['permit_data']['projectnummer']
    grouped[project_num].append(item)

# Maak nieuwe gegroepeerde structuur
new_pending = []
for project_num, items in grouped.items():
    first = items[0]
    
    # Verzamel alle wegen
    road_data_list = [item['road_data'] for item in items]
    road_geom_wkts = [item['road_geom_wkt'] for item in items]
    
    new_pending.append({
        "municipality": first['municipality'],
        "permit_data": first['permit_data'],
        "road_data_list": road_data_list,
        "permit_geom_wkt": first['permit_geom_wkt'],
        "road_geom_wkts": road_geom_wkts,
        "discovered_at": first['discovered_at'],
        "num_roads": len(items)
    })

# Schrijf nieuwe structuur
with open('pending_intersections_grouped.json', 'w', encoding='utf-8') as f:
    json.dump(new_pending, f, indent=2, ensure_ascii=False)

print(f"Converted {len(old_pending)} individual intersections to {len(new_pending)} grouped projects")
for item in new_pending:
    if item['num_roads'] > 1:
        print(f"  Project {item['permit_data']['projectnummer']}: {item['num_roads']} roads")
