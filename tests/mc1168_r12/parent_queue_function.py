def _auto_dispatch_followup_locked(session, message, p, pp, cmd, _sp_path):
    """Second phase of `_auto_dispatch_followup`; the caller holds `resuming`."""
    if session.get('status') == 'running':
        # Another resume won while the prompt was being built: a second process in
        # this worktree is worse than a delayed message. Queue it behind that turn.
        session.setdefault('pending_followups', []).insert(0, message)
        session['log_lines'].append('[follow-up queued: the session resumed while it was being prepared]')
        if _sp_path:
            try:
                os.unlink(_sp_path)
            except OSError:
                pass
        return
    try:
        _cwd = _session_cwd(session, pp, message)
        cmd = _launch_marker.launch_cmd(cmd, session.get('trigger_type'), _cwd)
        proc = spawn_engine_proc(
            cmd, session_id=session.get('session_id'), engine='claude',
            cwd=_cwd,
            env=_launch_marker.launch_env(session.get('trigger_type'), session=session),
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO)
    except Exception as e:
        session['log_lines'].append(f'[follow-up failed: {e}]')
        if _sp_path:
            try:
                os.unlink(_sp_path)
            except OSError:
                pass
        return
    _sysprompt_cleanup(_sp_path, proc)

    threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
    old_proc = session.get('proc')
    if old_proc:
        _unregister_process(old_proc.pid)
    session['proc'] = proc
    # Guardian/queue replay of an already-received message, not a fresh
    # caller decision -- never re-arms (see _advance_delegation_turn). The
    # queue does not record who sent the message, so the owner is kept.
    _advance_delegation_turn(session, owner=None)
    # Backlog 4668eafc calibration fix, defect 2 (WAKE TURNS WITH NO
    # turn_start): this dispatches a fresh turn exactly like agent_followup's
    # own `_start_new_turn` does, but historically skipped the matching
    # checkpoint write -- so a queued follow-up drained through here left
    # `_session_turns` nothing to pair against but the session's LAST
    # completion, folding the queueing delay into the turn and leaving no
    # 'running' session for the sampler to tick (measured: session c11ea2b1,
    # the scheduled backlog runner, had 5 completions a minute apart with no
    # turn_start and no ticks between any of them).
    _write_usage_breakdown_turn_start_checkpoint(session)
    session['status'] = 'running'
    session['last_status_change_time'] = _time.time()
    session['last_output_time'] = _time.time()
    session['pending_recovery_message'] = None
    _register_process(proc, 'Agent followup (A)', 'agent',
                      session['session_id'], session['project_id'], message[:80])
    user_label = state.CONFIG.get('user_name') or 'User'
    session['log_lines'].append(f"> {user_label}: {message}")

    t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
    t.start()