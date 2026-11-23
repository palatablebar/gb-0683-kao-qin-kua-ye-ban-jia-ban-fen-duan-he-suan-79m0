import io,csv

HEADER=['计划','批次','规则版本','安排状态','项目','员工','超时班次','实际下班','休息截止',
    '接替岗位','岗位开始','岗位结束','缺口分钟','工作分钟','确认人','项目状态','候选人']

def bundle(plan):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(HEADER)
    base=[plan['plan_id'],plan['run_id'],plan['rule_version'],plan['status']]
    items=plan.get('items') or [None]
    for item in items:
        if item is None:
            writer.writerow(base+['']*(len(HEADER)-4));continue
        writer.writerow(base+[item['id'],item['employee'],item['overtime_shift'],item['actual_out'],
            item['rest_deadline'],item['position_shift'],item['position_start'],item['position_end'],
            item['shortfall_minutes'],item['worked_minutes'],item.get('confirmed') or '',
            item['status'],'、'.join(item['candidates'])])
    return '﻿'+out.getvalue()
