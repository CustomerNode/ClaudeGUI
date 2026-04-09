"""
Compose API – stub module.

The compose feature is not yet implemented.  This stub provides the
blueprint and helper functions that other modules import so the app
can start without errors.
"""

from flask import Blueprint, jsonify

bp = Blueprint('compose_api', __name__, url_prefix='/api/compose')


# ---- Routes referenced by JS (toolbar.js, live-panel.js) ----

@bp.route('/board')
def get_board():
    return jsonify(None)


@bp.route('/projects/<project_id>/directives/resolve', methods=['POST'])
def resolve_directives(project_id):
    return jsonify({'ok': False, 'error': 'compose not implemented'})


# ---- Functions imported by ws_events.py ----

def resolve_compose_system_prompt(compose_task_id):
    return {'ok': False, 'error': 'compose not implemented'}


def link_session_to_compose_task(compose_task_id, session_id):
    pass
