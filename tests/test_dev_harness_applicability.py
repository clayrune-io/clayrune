from mc.dev_harness_applicability import aggregate, select


def payload():
    return {'steps': [{'id': 'unit'}, {'id': 'lint'}], 'groups': [
        {'id': 'docs', 'include': ['docs/'], 'check_ids': ['lint'],
         'not_applicable_check_ids': ['unit'], 'reason_code': 'docs_change'},
        {'id': 'app', 'include': ['app.py', 'docs/run.py'], 'check_ids': ['unit'],
         'not_applicable_check_ids': ['lint'], 'reason_code': 'source_change'}]}


def test_overlap_rename_deletion_and_unmapped():
    source = {'kind': 'integration', 'integration_changes': [{'old_path': 'app.py', 'new_path': 'docs/run.py'}]}
    selected = select(payload(), source)
    assert selected['check_ids'] == ['unit', 'lint'] and selected['not_applicable'] == {}
    source['integration_changes'] = [{'old_path': 'docs/gone.md', 'new_path': 'docs/gone.md'}]
    selected = select(payload(), source)
    assert selected['check_ids'] == ['lint'] and selected['not_applicable'] == {'unit': ['docs_change']}
    source['integration_changes'].append({'old_path': 'unknown', 'new_path': 'unknown'})
    assert select(payload(), source)['code'] == 'unmapped_change'


def test_preview_untracked_clean_measurement_and_noop():
    source = {'kind': 'preview', 'changes': [], 'manifest': {'files': {'binary': {}}, 'index': {}}}
    assert select(payload(), source)['unmapped_paths'] == ['binary']
    source['manifest']['files'] = {}
    assert select(payload(), source)['check_ids'] == ['unit', 'lint']
    assert select(payload(), source, measurement=True)['check_ids'] == ['unit', 'lint']
    assert select(payload(), {'kind': 'integration', 'integration_changes': []})['code'] == 'nothing_to_merge'


def test_severity_missing_and_integrity():
    policy = {'native_checks': {'unit': {'severity': 'required'}, 'lint': {'severity': 'warning'}}}
    selected = {'check_ids': ['unit', 'lint'], 'code': None}
    result = {'unit': {'status': 'pass'}, 'lint': {'status': 'fail'}}
    assert not aggregate(policy, selected, result, mode='enforce')['standards_hold']
    for status in ['fail', 'review_required', 'error', 'unsupported', 'pending', 'cancelled']:
        result['unit']['status'] = status
        assert aggregate(policy, selected, result, mode='enforce')['standards_hold']
        assert not aggregate(policy, selected, result, mode='preview')['standards_hold']
    assert aggregate(policy, selected, {}, mode='enforce')['standards_hold']
    assert aggregate(policy, selected, {}, mode='enforce', integrity_code='inputs_changed')['status'] == 'error'
