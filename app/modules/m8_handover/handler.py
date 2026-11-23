import base64,hashlib
from datetime import datetime,timezone
from flask import Blueprint,jsonify,request,render_template,Response
from app.config import TITLE,MODULES,SHORT
from app.errors import DomainError
from app.workflow import run as pipeline,load
from app import store
from app.modules.m8_handover.export_out import bundle

bp=Blueprint('m8_handover',__name__)

def plan_id_for(run_id):
    return hashlib.sha256(('handover/'+run_id).encode()).hexdigest()[:20]

def latest_run():
    history=store.history()
    if history:return store.get(history[0]['id'])
    state=pipeline(load('dirty.csv'),{})
    return store.save(state['run_id'],state)

def rebuild(run_id):
    record=store.get(run_id)
    source=base64.b64decode(record['source_b64']) if record.get('source_b64') else load('dirty.csv')
    return pipeline(source,record.get('manifest',{}).get('parameters',{}),8)

def build_plan(state):
    handover=state['handover']
    return {'plan_id':plan_id_for(state['run_id']),'run_id':state['run_id'],
        'rule_version':state['manifest']['rule_version'],'min_rest_hours':handover['min_rest_hours'],
        'generated_at':datetime.now(timezone.utc).isoformat(),'items':handover['items']}

def ensure(run_id,make_current):
    found=store.find_handover(plan_id_for(run_id))
    if found:return found
    return store.save_handover(build_plan(rebuild(run_id)),make_current)

def resolve():
    record=latest_run()
    try:return ensure(record['run_id'],True),record,None
    except Exception as error:
        plan=store.current_handover()
        if not plan:raise
        return plan,record,getattr(error,'message',str(error))

def reconcile(plan,state):
    proposal=state['handover']
    if proposal['min_rest_hours']!=plan['min_rest_hours']:raise DomainError('STALE','休息规则已变化，请重新生成',409)
    current={item['id']:item for item in proposal['items']}
    if set(current)!={item['id'] for item in plan['items']}:raise DomainError('STALE','班次或岗位已变化，请重新生成',409)
    roster={shift['employee'] for shift in state['shifts']}
    for item in plan['items']:
        fresh=current[item['id']]
        for key in ('employee','overtime_shift','rest_deadline','position_shift','position_start','position_end'):
            if fresh[key]!=item[key]:raise DomainError('STALE','班次或岗位已变化，请重新生成',409)
        if item['confirmed']:
            if item['confirmed'] not in roster or item['confirmed'] not in fresh['candidates']:
                raise DomainError('CONSTRAINT','确认人员未与班次和人员对平')
        elif fresh['candidates']:
            raise DomainError('UNCONFIRMED','存在待确认岗位')

@bp.get('/m8_handover')
def screen():
    plan,record,warning=resolve()
    try:data=rebuild(record['run_id'])
    except Exception:data=record
    stale=plan['rule_version']!=record.get('manifest',{}).get('rule_version')
    return render_template('m8_handover.html',title=TITLE,short=SHORT,modules=MODULES,current=8,
        data=data,run=record,history=store.history(),plan=plan,stale=stale,warning=warning)

@bp.route('/api/m8_handover',methods=['GET','POST'])
def api():
    if request.method=='POST':
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
        history=store.history()
        run_id=data.get('run_id') or (history[0]['id'] if history else None)
        if not run_id:raise DomainError('NOT_FOUND','没有可用批次',404)
        store.get(run_id)
        return jsonify(ensure(run_id,bool(history) and run_id==history[0]['id']))
    plan,_,warning=resolve()
    if warning:plan=dict(plan,warning=warning)
    return jsonify(plan)

@bp.get('/api/m8_handover/<plan_id>')
def show(plan_id):
    return jsonify(store.get_handover(plan_id))

@bp.post('/api/m8_handover/<plan_id>/confirm')
def confirm(plan_id):
    data=request.get_json(silent=True) or {}
    employee,item_id=data.get('employee'),data.get('item_id')
    if not employee or not item_id:raise DomainError('PARAMETERS','请提交接替岗位与确认人员')
    plan=store.get_handover(plan_id)
    if plan['status']=='已失效':raise DomainError('STALE','安排已失效，请按新安排确认',409)
    if not any(item['id']==item_id for item in plan['items']):raise DomainError('NOT_FOUND','接替项目不存在',404)
    state=rebuild(plan['run_id'])
    fresh=next((item for item in state['handover']['items'] if item['id']==item_id),None)
    if not fresh:raise DomainError('STALE','班次已变化，请重新生成安排',409)
    if employee not in fresh['candidates']:raise DomainError('CONSTRAINT','确认人员未通过休息对平')
    version=data.get('version',0)
    if not isinstance(version,int):raise DomainError('VERSION','确认版本无效')
    return jsonify(store.confirm_handover(plan_id,item_id,employee,version))

@bp.post('/api/m8_handover/<plan_id>/publish')
def publish(plan_id):
    data=request.get_json(silent=True) or {}
    if not isinstance(data.get('version'),int):raise DomainError('VERSION','请提交安排版本')
    plan=store.get_handover(plan_id)
    if plan['status']=='已发布':return jsonify(plan)
    if plan['status']=='已失效':raise DomainError('STALE','安排已失效，请重新生成核对',409)
    state=rebuild(plan['run_id'])
    if plan['rule_version']!=state['manifest']['rule_version']:raise DomainError('STALE','规则已更新，请重新生成核对',409)
    reconcile(plan,state)
    return jsonify(store.publish_handover(plan_id,data['version']))

@bp.get('/api/m8_handover/export.csv')
def export():
    plan,_,_=resolve()
    return Response(bundle(plan),mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition':'attachment; filename=handover.csv'})
