import hashlib
from collections import defaultdict
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from app.errors import DomainError
from app.modules.m3_versions.rules import schedule,ZONE

def _out(shift):
    return datetime.fromisoformat(shift['out']['at']).astimezone(ZONE)

def _window(shift):
    row=shift['in'] or shift['out'] or shift['sources'][0]
    start,end,_,_=schedule(row)
    return start,end

def _candidates(reliever,start,end,by_employee,rest):
    picked=[]
    for employee in sorted(by_employee):
        if employee==reliever:continue
        rested=True
        for shift in by_employee[employee]:
            ws,we=_window(shift)
            if ws<end and start<we:rested=False;break
            if shift['out']:
                out_at=_out(shift)
                if out_at<=start and out_at+rest>start:rested=False;break
        if rested:picked.append(employee)
    return picked

def propose(state):
    raw=state['parameters'].get('min_rest_hours',12)
    try:rest_hours=Decimal(str(raw))
    except (InvalidOperation,ValueError):raise DomainError('PARAMETERS','最低休息时长无效')
    if not 0<rest_hours<=24:raise DomainError('PARAMETERS','最低休息时长须在0到24小时之间')
    rest=timedelta(hours=float(rest_hours))
    worked=defaultdict(int)
    for segment in state.get('segments',[]):worked[segment['id']]+=segment['minutes']
    by_employee=defaultdict(list)
    for shift in state['shifts']:
        if shift['in'] and shift['out']:by_employee[shift['employee']].append(shift)
    for shifts in by_employee.values():shifts.sort(key=lambda s:s['day'])
    items=[]
    for employee in sorted(by_employee):
        shifts=by_employee[employee]
        for current,following in zip(shifts,shifts[1:]):
            out_at=_out(current);deadline=out_at+rest
            start,end=_window(following)
            if start>=deadline:continue
            _,planned_end=_window(current)
            item_id=hashlib.sha256((following['id']+'|'+employee).encode()).hexdigest()[:16]
            items.append({'id':item_id,'employee':employee,
                'overtime_shift':current['id'],'actual_out':out_at.isoformat(),
                'planned_end':planned_end.isoformat(),
                'overtime_minutes':int((out_at-planned_end).total_seconds()//60),
                'worked_minutes':worked.get(current['id'],0),
                'rest_deadline':deadline.isoformat(),
                'position_shift':following['id'],
                'position_start':start.isoformat(),'position_end':end.isoformat(),
                'shortfall_minutes':int((deadline-start).total_seconds()//60),
                'candidates':_candidates(employee,start,end,by_employee,rest)})
    return {'min_rest_hours':str(rest_hours),'items':items}
