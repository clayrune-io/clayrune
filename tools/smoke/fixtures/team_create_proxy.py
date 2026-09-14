"""Run the REAL POST /api/characters/team for tools/smoke/team-card.mjs.

The smoke drives the real card in a hermetic browser; this puts the real
endpoint behind its click, against a throwaway directory, so "3 characters
exist with the edited values" is checked on disk rather than on a mock.

  python team_create_proxy.py <workdir> <result.json>   (request body on stdin)

<workdir>/global is the global agents dir (the smoke seeds it), <workdir>/proj
is the one project's folder, and <workdir>/roster.json persists the project's
roster between calls.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

work = Path(sys.argv[1])
out = Path(sys.argv[2])
body = json.loads(sys.stdin.read() or '{}')

import server  # noqa: E402
from mc import characters as ch  # noqa: E402
from mc.blueprints import character_routes as cr  # noqa: E402
from mc.blueprints import local_auth as la  # noqa: E402
from mc.blueprints import project_routes as pr  # noqa: E402
from mc.blueprints import skills_routes as sr  # noqa: E402

la.LOCAL_AUTH_PATH = work / 'local_auth.json'
ch.GLOBAL_AGENTS_DIR = work / 'global'
proj_path = work / 'proj'
proj_path.mkdir(parents=True, exist_ok=True)
roster_file = work / 'roster.json'
proj = {'id': 'smoke_team', 'name': 'Smoke Team', 'project_path': str(proj_path),
        'roster': json.loads(roster_file.read_text(encoding='utf-8')) if roster_file.exists() else []}


def _load(pid):
    return proj if pid == 'smoke_team' else None


def _save(pid, data):
    roster_file.write_text(json.dumps(data.get('roster', [])), encoding='utf-8')


cr.load_project = _load
sr.load_project = _load
pr.save_project = _save

server.app.config['TESTING'] = True
resp = server.app.test_client().post(
    '/api/characters/team', json=body,
    environ_base={'HTTP_ORIGIN': 'http://localhost:5199'})
files = {p.relative_to(work).as_posix(): p.read_text(encoding='utf-8')
         for p in work.rglob('*.md')}
out.write_text(json.dumps({
    'status': resp.status_code, 'body': resp.get_json(), 'files': files,
    'roster': json.loads(roster_file.read_text(encoding='utf-8')) if roster_file.exists() else [],
}), encoding='utf-8')
