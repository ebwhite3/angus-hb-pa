#!/usr/bin/env python3
"""Extract Master Well List from the P&A NOI Progress Report workbook -> wells.json

Usage: python3 extract_wells.py <xlsx_path> <output_json> [current_rig_well]
current_rig_well: normalized well name (e.g. A-8I) the rig is currently on,
taken from the latest Daily Rig Report. If omitted, inferred as any well with an
ops start date but no completion date.
"""
import sys, json, re, datetime
import openpyxl

def norm_name(s):
    """Normalize well name: 'A-8 I' / 'A-8i' / 'A8i' -> 'A-8I'"""
    if not s: return ''
    s = str(s).strip().upper().replace(' ', '')
    m = re.match(r'^([AB])-?(\d+)(I?)$', s)
    if m:
        return f"{m.group(1)}-{m.group(2)}{m.group(3)}"
    return s

def fmt_date(v):
    if v is None or v == '': return None
    if isinstance(v, datetime.datetime):
        if v.year < 1990: return None  # formula artifacts like '00:00:00'
        return v.strftime('%Y-%m-%d')
    if isinstance(v, datetime.time): return None
    s = str(v).strip()
    if s.lower() in ('n/a', 'na', ''): return None
    return s

def main():
    xlsx, out = sys.argv[1], sys.argv[2]
    rig_well = norm_name(sys.argv[3]) if len(sys.argv) > 3 else None

    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb['Master Well List']
    # Iterate cells (not values_only) so we can also read the Permit # hyperlink.
    rows = list(ws.iter_rows())
    hdr = [str(c.value).strip().replace('\n', ' ') if c.value is not None else '' for c in rows[1]]

    def col(name_part):
        for i, h in enumerate(hdr):
            if name_part.lower() in h.lower():
                return i
        return None

    C = {
        'well': col('Well Name'), 'api': col('API 10'), 'parcel': col('Parcel'),
        'permit': col('Permit #'), 'submitted': col('Submission Date'),
        'resubmitted': col('Resubmitted'), 'approved': col('Approval Date'),
        'noi': col('NOI Status'), 'ops_start': col('Operations  Start'),
        'ops_end': col('Operations  Completion'), 'rig_days': col('Rig Days'),
        'fire': col('Fire Permit'), 'calgem': col('CalGEM Status'),
        'type': col('Type'), 'td': col('Original Hole'),
    }
    # fallbacks for header whitespace quirks
    if C['ops_start'] is None: C['ops_start'] = col('Start Date')
    if C['ops_end'] is None: C['ops_end'] = col('Completion Date')

    def val(r, idx):
        return r[idx].value if idx is not None else None

    wells = []
    for r in rows[2:]:
        if not val(r, C['well']): continue
        name_raw = str(val(r, C['well'])).strip()
        name = norm_name(name_raw)
        ops_start = fmt_date(val(r, C['ops_start'])) if C['ops_start'] is not None else None
        ops_end = fmt_date(val(r, C['ops_end'])) if C['ops_end'] is not None else None
        noi = str(val(r, C['noi']) or '').strip()
        rig_days = val(r, C['rig_days']) if C['rig_days'] is not None else None
        if isinstance(rig_days, (int, float)) and (rig_days < 0 or rig_days > 365):
            rig_days = None

        # API: zero-pad to 10 digits so the CalGEM leading zero is never dropped,
        # whether the spreadsheet stores it as text ('0405921420') or a number (405921419).
        api = str(val(r, C['api']) or '').strip()
        if api.endswith('.0'): api = api[:-2]
        if api.isdigit(): api = api.zfill(10)

        # Permit #: value plus any hyperlink set on the cell (link lives in the spreadsheet).
        permit_cell = r[C['permit']] if C['permit'] is not None else None
        permit_val = str(permit_cell.value).strip() if (permit_cell is not None and permit_cell.value is not None) else ''
        permit_url = permit_cell.hyperlink.target if (permit_cell is not None and permit_cell.hyperlink) else None

        # Derived status
        if ops_end:
            status = 'complete'           # P&A complete (rig work done)
        elif (rig_well and name == rig_well):
            status = 'rig'                # rig currently on this well
        elif ops_start and not ops_end:
            status = 'rig'
        elif noi.startswith('0'):
            status = 'ready'              # NOI approved, awaiting rig
        else:
            status = 'pending'            # NOI resubmitted / under CalGEM review

        td_val = val(r, C['td'])
        entry = {
            'well': name_raw, 'well_norm': name,
            'api': api,
            'type': str(val(r, C['type']) or '').strip(),
            'td': td_val if isinstance(td_val, (int, float)) else None,
            'permit': permit_val,
            'noi_status': noi,
            'submitted': fmt_date(val(r, C['submitted'])),
            'resubmitted': str(val(r, C['resubmitted']) or '').strip() or None,
            'approved': fmt_date(val(r, C['approved'])),
            'ops_start': ops_start, 'ops_end': ops_end,
            'rig_days': int(rig_days) if isinstance(rig_days, (int, float)) and rig_days > 0 else None,
            'fire_permit': str(val(r, C['fire']) or '').strip() or None,
            'calgem_status': str(val(r, C['calgem']) or '').strip() or None,
            'status': status,
        }
        if permit_url:
            entry['permit_url'] = permit_url
        wells.append(entry)

    meta = {
        'source_file': xlsx.split('/')[-1],
        'extracted_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'well_count': len(wells),
    }
    with open(out, 'w') as f:
        json.dump({'meta': meta, 'wells': wells}, f, indent=1)
    print(f"Wrote {len(wells)} wells -> {out}")
    from collections import Counter
    print(Counter(w['status'] for w in wells))

if __name__ == '__main__':
    main()
