import json

with open('search_results_official.json', 'r') as f:
    data = json.load(f)

sessions = data[0] if isinstance(data[0], list) else data
races = [s for s in sessions if s.get('event_type_name', '').lower() == 'race']

print(f'Total sesiones: {len(sessions)}')
print(f'Carreras (races): {len(races)}')
print()
for r in races:
    finish = r.get('finish_position_in_class', r.get('finish_position', 0))
    start = r.get('starting_position_in_class', r.get('starting_position', 0))
    inc = r.get('incidents', 0)
    sof = r.get('event_strength_of_field', 0)
    series = r.get('series_short_name', 'N/A')
    print(f"  [{series[:20]:20}] Start: P{start:2}, Finish: P{finish:2}, Inc: {inc:2}x, SOF: {sof}")
