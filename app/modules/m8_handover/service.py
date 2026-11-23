from app.modules.m8_handover.rules import propose

def execute(state):
    state['handover']=propose(state)
    return state
