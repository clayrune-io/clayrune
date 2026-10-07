"""Read-only browser sign-in probes; states/records belong to verification.py.

Selectors come only from reviewed profile data. No page text is returned and
no scripts supplied by a caller run. Only the hidden reader's tab is used when
the profile is open; a closed profile uses the existing owned reader.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from mc.blueprints import browser_routes as br
from mc.browser_hidden_read import HiddenTabReader
from mc.core import _log
from mc.desk_connect import registry, url_check, verification
from mc.desk_connect.providers.base import Probe

# Fixed read-only expression, selectors inserted as JSON values. Visible controls
# are positive evidence; no text, hidden node, cookie or storage value is read.
EVIDENCE_JS = """(() => {
 const visible = el => {
  if (!el.getClientRects().length || !el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return false;
  const r=el.getBoundingClientRect();
  if(r.bottom<=0 || r.right<=0 || r.top>=innerHeight || r.left>=innerWidth)return false;
  return !el.closest('[hidden],[aria-hidden="true"]');
 };
 return {href:location.href, matches:SELECTORS.map(s=>Array.from(document.querySelectorAll(s)).some(visible))};
})()"""


class CheckError(ValueError):
    def __init__(self,message,status=400,code='invalid'):
        super().__init__(message)
        self.status,self.code=status,code


def clean(raw, own_hosts=()) -> dict:
    if not isinstance(raw,dict) or set(raw)-{'url','browser_profile','confirmed','check'} or not {'url','browser_profile'} <= set(raw):
        raise CheckError('url and browser_profile are required; checks come from the service profile')
    try:
        got=url_check.check_url(raw['url'],own_hosts=own_hosts)
    except url_check.UrlError as e:
        raise CheckError(str(e)) from e
    name=raw['browser_profile']
    if not isinstance(name,str) or br._profile_dir(name) is None:
        raise CheckError('that browser profile name is not valid')
    name=name.strip().lower()
    if type(raw.get('confirmed',False)) is not bool or type(raw.get('check',True)) is not bool:
        raise CheckError('confirmed and check must be booleans')
    return {**got,'browser_profile':name,'confirmed':raw.get('confirmed',False),'check':raw.get('check',True)}


def profile_stamp(name: str) -> tuple:
    """Cookie database metadata only; deletion/replacement invalidates a check.

    No cookie values are opened. Records are process-local and bounded by the
    existing verification store, so a restart requires another successful probe.
    """
    directory=br._profile_dir(name)
    if directory is None:
        return (name,None)
    path=Path(directory)
    stamps=[]
    for part in ('','Default/Network/Cookies','Default/Cookies'):
        target=path / part
        try:
            s=target.stat();stamps.append((s.st_mtime_ns,s.st_size))
        except FileNotFoundError:
            stamps.append(None)
    return (name,*stamps)


class BrowserProvider:
    def __init__(self, target: dict):
        self.target=target
        svc=registry.lookup(target['host'])
        self.profile=registry.profile(svc['id']) if svc else None
        self.spec=(self.profile or {}).get('signin_check')
        self.service_id=svc['id'] if svc else target['host']
        self.key=hashlib.sha256(json.dumps({'service':self.service_id,'address':None if svc else target['url'],
                                          'profile':target['browser_profile']},sort_keys=True).encode()).hexdigest()

    def credential_state(self,_method,_account=None) -> dict:
        stamp=(profile_stamp(self.target['browser_profile']),json.dumps(self.spec,sort_keys=True), (self.profile or {}).get('revision'))
        return {'state':'probe_ready' if br.named_profile_exists(self.target['browser_profile']) else 'needs_signin',
                'entry':f'browser:{self.key}','stamp':stamp}

    def verify(self,_method,_account=None) -> Probe:
        if not self.spec:
            return Probe(None,'Clayrune could not check automatically.',capability='Browser sign-in',
                         kind='confirmed' if self.target['confirmed'] else '')
        assert self.profile is not None
        return probe(self.target['browser_profile'],self.profile,self.spec)

    def probe_stamp(self, before) -> tuple:
        # The owned reader closes gracefully and can flush its cookies. Bind the
        # evidence to that resulting snapshot; vault providers retain BEFORE stamps.
        return self.credential_state('browser_signin')['stamp']


def reader_for(name: str):
    pane = br._session_using_profile(name)
    reader = HiddenTabReader if pane else br.ProfilePageReader
    # Connections are global: use the live pane's project when it has one,
    # otherwise leave the owned reader unscoped. No installation-specific ID.
    return reader(pane.get('project_id') if pane else None,name)


def probe(name: str, profile: dict, spec: dict) -> Probe:
    reader=reader_for(name)
    try:
        body=reader.read(spec['url'])
        if not body.get('ok'):
            return Probe(False,'The connection check could not run. Try again.',kind='unavailable')
        final=body.get('final_url') or ''
        if urlsplit(final).hostname not in profile['hosts'] or urlsplit(final).scheme != 'https':
            return Probe(False,'Sign in to the service, then check again.',kind='signed_out')
        ok,evidence=reader._evaluate(EVIDENCE_JS.replace('SELECTORS',json.dumps(spec['selectors'])),5,15)
        if not ok or not isinstance(evidence,dict):
            return Probe(False,'The connection check could not run. Try again.',kind='unavailable')
        actual=urlsplit(evidence.get('href') or '')
        # Revalidate at the instant evidence is collected, including the page path:
        # a redirect/race cannot make an unrelated login page count as a pass.
        expected=urlsplit(spec['url'])
        passed=actual.scheme=='https' and actual.hostname==expected.hostname and actual.path.rstrip('/')==expected.path.rstrip('/') and evidence.get('matches')==[True]*len(spec['selectors'])
        return Probe(passed,'Browser sign-in verified.' if passed else 'Sign in to the service, then check again.',
                     identity=name,capability='Browser sign-in',kind='' if passed else 'signed_out')
    except Exception as e:
        _log(f'[desk_connect] browser sign-in check failed: {type(e).__name__}',flush=True)
        return Probe(False,'The connection check could not run. Try again.',kind='unavailable')
    finally:
        reader.close()


def run(cleaned: dict) -> dict:
    provider=BrowserProvider(cleaned)
    action=verification.verify if cleaned['check'] else verification.status
    out=action(provider.service_id,'browser_signin',provider.key,provider=provider)
    # A failed probe clears verification. Only actual signed-out evidence opens
    # sign-in; a transport failure is reported without guessing another reader.
    needs=out['state']=='sign_in_required' or out.get('failure')=='signed_out'
    route=next((r for r in (provider.profile or {}).get('routes',[]) if r['transport']=='browser' and r.get('signin')),None)
    out.update(verifiable=bool(provider.spec),needs_signin=needs,
               signin_url=route['signin']['url'] if route else (provider.profile or {}).get('home_url') or cleaned['url'],
               browser_profile=cleaned['browser_profile'])
    return out


def status_for(url: str, name: str) -> dict:
    """Best-effort saved-state projection; a bad reference cannot break a list."""
    try:
        return run(clean({'url':url,'browser_profile':name,'check':False}))
    except Exception as e:
        return unavailable(e)


def unavailable(error: Exception) -> dict:
    _log(f'[desk_connect] browser status lookup failed: {type(error).__name__}',flush=True)
    return {'state':'unknown','label':'Check unavailable','failure':'unavailable',
            'message':'The connection status is unavailable. Check the saved connection details.',
            'verifiable':False,'needs_signin':False}


def account_status(service: str, name: str) -> dict:
    """Keep registry and status failures outside the account-list boundary."""
    try:
        profile=registry.profile(service)
        if profile is None:
            raise CheckError('the stored service profile is unavailable')
        url=profile['home_url']
        return {'url':url,'status':status_for(url,name)}
    except Exception as e:
        return {'status':unavailable(e)}


def refresh_result(result: dict) -> dict:
    """An idempotent Save replays facts, never an old verification verdict."""
    return {**result,'status':status_for(result['url'],result['browser_profile'])}
