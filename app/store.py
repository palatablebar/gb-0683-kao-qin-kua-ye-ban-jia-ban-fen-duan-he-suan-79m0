import sqlite3,json,os
from pathlib import Path
from datetime import datetime,timezone
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS handover_plans (plan_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, rule_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT "草稿", body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS handover_confirmations (plan_id TEXT NOT NULL, item_id TEXT NOT NULL, employee TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, PRIMARY KEY (plan_id,item_id))')
    return db

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)

def get_handover(plan_id):
    with connection() as db:
        row=db.execute('SELECT * FROM handover_plans WHERE plan_id=?',(plan_id,)).fetchone()
        if not row:raise DomainError('NOT_FOUND','接替安排不存在',404)
        confirmations=db.execute('SELECT * FROM handover_confirmations WHERE plan_id=? ORDER BY item_id',(plan_id,)).fetchall()
    plan=json.loads(row['body'])
    plan.update(plan_id=row['plan_id'],run_id=row['run_id'],rule_version=row['rule_version'],status=row['status'],version=row['version'])
    by_item={c['item_id']:c for c in confirmations}
    for item in plan['items']:
        confirmed=by_item.get(item['id'])
        item['confirmed']=confirmed['employee'] if confirmed else None
        item['confirmation_version']=confirmed['version'] if confirmed else 0
        item['status']='已确认' if confirmed else ('空岗' if not item['candidates'] else '待确认')
    plan['confirmations']=[dict(c) for c in confirmations]
    return plan

def find_handover(plan_id):
    try:return get_handover(plan_id)
    except DomainError as error:
        if error.code=='NOT_FOUND':return None
        raise

def current_handover():
    with connection() as db:row=db.execute("SELECT value FROM meta WHERE key='handover_current'").fetchone()
    return get_handover(row['value']) if row else None

def save_handover(plan, make_current):
    with connection() as db:
        cur=db.execute('INSERT OR IGNORE INTO handover_plans(plan_id,run_id,rule_version,status,body,created_at) VALUES (?,?,?,?,?,?)',
            (plan['plan_id'],plan['run_id'],plan['rule_version'],'草稿',json.dumps(plan,ensure_ascii=False),plan['generated_at']))
        if cur.rowcount==1 and make_current:
            db.execute("UPDATE handover_plans SET status='已失效',version=version+1 WHERE plan_id!=? AND status!='已失效'",(plan['plan_id'],))
            db.execute("INSERT INTO meta(key,value) VALUES ('handover_current',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(plan['plan_id'],))
    return get_handover(plan['plan_id'])

def confirm_handover(plan_id, item_id, employee, version):
    with connection() as db:
        if not db.execute('SELECT 1 FROM handover_plans WHERE plan_id=?',(plan_id,)).fetchone():raise DomainError('NOT_FOUND','接替安排不存在',404)
        row=db.execute('SELECT * FROM handover_confirmations WHERE plan_id=? AND item_id=?',(plan_id,item_id)).fetchone()
        now=datetime.now(timezone.utc).isoformat()
        if row:
            if row['employee']!=employee:
                if version!=row['version']:raise DomainError('STALE','确认版本已更新，请刷新',409)
                db.execute('UPDATE handover_confirmations SET employee=?,version=version+1,created_at=? WHERE plan_id=? AND item_id=?',(employee,now,plan_id,item_id))
        else:
            db.execute('INSERT INTO handover_confirmations(plan_id,item_id,employee,version,created_at) VALUES (?,?,?,1,?)',(plan_id,item_id,employee,now))
    return get_handover(plan_id)

def publish_handover(plan_id, version):
    with connection() as db:
        cur=db.execute("UPDATE handover_plans SET status='已发布',version=version+1 WHERE plan_id=? AND version=? AND status='草稿'",(plan_id,version))
        if cur.rowcount!=1:
            row=db.execute('SELECT * FROM handover_plans WHERE plan_id=?',(plan_id,)).fetchone()
            if not row:raise DomainError('NOT_FOUND','接替安排不存在',404)
            if row['status']=='已发布':return get_handover(plan_id)
            raise DomainError('STALE','安排版本已更新，请刷新',409)
    return get_handover(plan_id)
