"""MC-1074/1079: checking an agent-held profile leaves both existing tabs alone."""
from mc import browser_agent_tabs as tabs
from mc.desk_connect import browser_check as check
from mc.browser_hidden_read import HiddenTabReader
from tests.test_browser_agent_read_hidden import live_pane, _wait, _pane_href, _raw_page_targets  # noqa: F401


def test_check_with_agent_tab_uses_its_own_hidden_target(live_pane):
    br,pane,site=live_pane
    user_url=site.base+'/user-tab'
    _wait(lambda:_pane_href(br,pane)==user_url)
    handle=tabs.adopt(pane,site.base+'/agent-page','agent-project')['session_id']
    agent=tabs._tabs[handle]
    try:
        _wait(lambda:agent.evaluate('location.href')==(True,site.base+'/agent-page'))
        before={t['id'] for t in _raw_page_targets(br,pane)}
        active=pane['active_target_id'];visible=dict(pane['tabs'])
        assert br._session_using_profile('main') is pane
        reader=check.reader_for('main')
        assert isinstance(reader,HiddenTabReader)
        reader.close()
        # A local HTTP fixture deliberately cannot verify a reviewed HTTPS
        # service, but exercises the real probe's tab ownership and teardown.
        out=check.probe('main',{'hosts':['127.0.0.1']},{'url':site.base+'/check-page','selectors':['#v']})
        assert out.ok is False and out.kind=='signed_out'
        _wait(lambda:{t['id'] for t in _raw_page_targets(br,pane)}==before)
        assert _pane_href(br,pane)==user_url
        assert agent.evaluate('location.href')==(True,site.base+'/agent-page')
        assert pane['active_target_id']==active and dict(pane['tabs'])==visible
        assert pane['status']=='running' and agent.running()
    finally:
        agent.close()
