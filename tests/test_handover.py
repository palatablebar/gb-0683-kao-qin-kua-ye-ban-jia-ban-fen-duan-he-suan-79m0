import pytest

CSV=(
'id,employee,shift_day,version,punch,at,shift_start,shift_end,break_start,break_end,hourly_rate,paid\n'
'0001,E01,2026-10-01,1,IN,2026-10-01T22:00:00+08:00,22:00,06:00,02:00,03:00,20,0\n'
'0002,E01,2026-10-01,1,OUT,2026-10-02T11:00:00+08:00,22:00,06:00,02:00,03:00,20,0\n'
'0003,E01,2026-10-02,1,IN,2026-10-02T22:00:00+08:00,22:00,06:00,02:00,03:00,20,0\n'
'0004,E01,2026-10-02,1,OUT,2026-10-03T06:00:00+08:00,22:00,06:00,02:00,03:00,20,0\n'
'0005,E02,2026-10-01,1,IN,2026-10-01T22:00:00+08:00,22:00,06:00,02:00,03:00,20,0\n'
'0006,E02,2026-10-01,1,OUT,2026-10-02T06:00:00+08:00,22:00,06:00,02:00,03:00,20,0\n'
'0007,E02,2026-10-02,1,IN,2026-10-02T22:00:00+08:00,22:00,06:00,02:00,03:00,20,0\n'
'0008,E02,2026-10-02,1,OUT,2026-10-03T06:00:00+08:00,22:00,06:00,02:00,03:00,20,0\n'
'0009,E03,2026-10-01,1,IN,2026-10-01T08:00:00+08:00,08:00,17:00,12:00,13:00,20,0\n'
'0010,E03,2026-10-01,1,OUT,2026-10-01T17:00:00+08:00,08:00,17:00,12:00,13:00,20,0\n'
'0011,E04,2026-10-01,1,IN,2026-10-01T08:00:00+08:00,08:00,17:00,12:00,13:00,20,0\n'
'0012,E04,2026-10-01,1,OUT,2026-10-01T17:00:00+08:00,08:00,17:00,12:00,13:00,20,0\n')
CSV2=CSV+(
'0013,E05,2026-10-01,1,IN,2026-10-01T08:00:00+08:00,08:00,17:00,12:00,13:00,20,0\n'
'0014,E05,2026-10-01,1,OUT,2026-10-01T17:00:00+08:00,08:00,17:00,12:00,13:00,20,0\n')

def make_run(client,csv=CSV):
    r=client.post('/api/runs',json={'csv':csv});assert r.status_code==200
    return r.get_json()['run_id']

def current(client):
    r=client.get('/api/m8_handover');assert r.status_code==200
    return r.get_json()

def confirm(client,plan_id,item_id,employee,version):
    return client.post('/api/m8_handover/'+plan_id+'/confirm',
        json={'item_id':item_id,'employee':employee,'version':version})

def test_generate_maps_items(client):
    make_run(client)
    plan=current(client)
    assert plan['status']=='草稿' and len(plan['items'])==1
    item=plan['items'][0]
    assert item['employee']=='E01' and item['overtime_shift']=='E01/2026-10-01'
    assert item['actual_out']=='2026-10-02T11:00:00+08:00'
    assert item['rest_deadline']=='2026-10-02T23:00:00+08:00'
    assert item['position_shift']=='E01/2026-10-02' and item['shortfall_minutes']==60
    assert item['worked_minutes']==720 and item['overtime_minutes']==300
    assert item['candidates']==['E03','E04'] and item['status']=='待确认'

def test_regenerate_is_idempotent(client):
    make_run(client)
    first=current(client);item=first['items'][0]['id']
    assert confirm(client,first['plan_id'],item,'E03',0).status_code==200
    again=client.post('/api/m8_handover',json={}).get_json()
    assert again['plan_id']==first['plan_id']
    assert len(again['confirmations'])==1 and again['items'][0]['status']=='已确认'

def test_confirm_eligibility_and_version(client):
    make_run(client)
    plan=current(client);item=plan['items'][0]['id']
    bad=confirm(client,plan['plan_id'],item,'E02',0)
    assert bad.status_code==422 and bad.get_json()['code']=='CONSTRAINT'
    assert confirm(client,plan['plan_id'],item,'E03',0).status_code==200
    assert confirm(client,plan['plan_id'],item,'E03',0).status_code==200
    stale=confirm(client,plan['plan_id'],item,'E04',0)
    assert stale.status_code==409 and stale.get_json()['code']=='STALE'
    assert confirm(client,plan['plan_id'],item,'E04',1).status_code==200
    plan=current(client)
    assert len(plan['confirmations'])==1 and plan['confirmations'][0]['employee']=='E04'
    assert plan['confirmations'][0]['version']==2

def test_publish_requires_reconcile(client):
    make_run(client)
    plan=current(client);item=plan['items'][0]['id']
    early=client.post('/api/m8_handover/'+plan['plan_id']+'/publish',json={'version':1})
    assert early.status_code==422 and early.get_json()['code']=='UNCONFIRMED'
    confirm(client,plan['plan_id'],item,'E03',0)
    stale=client.post('/api/m8_handover/'+plan['plan_id']+'/publish',json={'version':9})
    assert stale.status_code==409
    done=client.post('/api/m8_handover/'+plan['plan_id']+'/publish',json={'version':1})
    assert done.status_code==200 and done.get_json()['status']=='已发布'
    again=client.post('/api/m8_handover/'+plan['plan_id']+'/publish',json={'version':1})
    assert again.status_code==200 and again.get_json()['status']=='已发布'

def test_rule_update_invalidates_and_keeps_records(client,monkeypatch):
    run_id=make_run(client)
    plan=current(client);item=plan['items'][0]['id']
    confirm(client,plan['plan_id'],item,'E03',0)
    monkeypatch.setattr('app.manifest.RULE_VERSION','2099.01.01-test')
    assert make_run(client)!=run_id
    fresh=current(client)
    assert fresh['plan_id']!=plan['plan_id'] and fresh['status']=='草稿'
    assert fresh['items'][0]['status']=='待确认' and not fresh['confirmations']
    old=client.get('/api/m8_handover/'+plan['plan_id']).get_json()
    assert old['status']=='已失效' and len(old['confirmations'])==1
    assert client.get('/api/runs/'+run_id).status_code==200
    gone=confirm(client,plan['plan_id'],item,'E04',1)
    assert gone.status_code==409

def test_generation_failure_keeps_current(client,monkeypatch):
    make_run(client)
    plan=current(client)
    def boom(state):
        from app.errors import DomainError
        raise DomainError('RANGE','测试故障')
    monkeypatch.setattr('app.modules.m8_handover.service.propose',boom)
    make_run(client,CSV2)
    r=client.get('/api/m8_handover');assert r.status_code==200
    body=r.get_json()
    assert body['plan_id']==plan['plan_id'] and 'warning' in body
    old=client.get('/api/m8_handover/'+plan['plan_id']).get_json()
    assert old['status']=='草稿'
    assert client.post('/api/m8_handover',json={}).status_code==422

def test_page_and_export_share_one_plan(client):
    make_run(client)
    plan=current(client)
    page=client.get('/m8_handover')
    assert page.status_code==200 and plan['plan_id'] in page.data.decode()
    export=client.get('/api/m8_handover/export.csv')
    assert export.status_code==200
    text=export.data.decode('utf-8-sig')
    assert plan['plan_id'] in text and plan['items'][0]['id'] in text
