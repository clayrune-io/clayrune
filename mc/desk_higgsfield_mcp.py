"""Higgsfield MCP adapter; picture preparation is exclusive to human Render."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mc import desk_engines as eng
    from mc.desk_engines import ModelDescriptor, GenerationRequest, _Creds


class HiggsfieldMcpAdapter:
    """Higgsfield over MCP, on the user's own plan credits. Same four methods as
    every adapter. Proven against the live server 2026-10-02 (spike section 8):
    `get_cost: true` quotes credits and submits nothing, `use_unlim` is pinned
    false so only credits are ever spent, a job is polled with
    `job_status {jobId, sync: true}`, and the result URL is signed for ~4 hours."""

    _TOOLS = {'image': 'generate_image', 'video': 'generate_video'}

    def _call(self, creds: _Creds, tool: str, arguments: dict) -> dict:
        from mc import desk_engines as eng
        eng._mcp_post(creds.secret, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': eng._MCP_PROTOCOL, 'capabilities': {},
            'clientInfo': {'name': 'Clayrune', 'version': '1'}}}, expect_id=1)
        eng._mcp_post(creds.secret, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, expect_id=None)
        res = eng._mcp_post(creds.secret, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                       'params': {'name': tool, 'arguments': arguments}}, expect_id=2)
        res = res or {}
        if res.get('isError'):
            raise eng._classify_http(400, eng._mcp_text(res).encode('utf-8'))
        return res

    @staticmethod
    def _params(model: ModelDescriptor, req: GenerationRequest, **extra) -> dict:
        from mc import desk_engines as eng
        if (req.first_frame or req.last_frame or req.reference_images) and not model.inputs.get('picture_ready'):
            from mc.desk_mcp_picture_schema import PENDING
            raise eng.EngineError('invalid_input', model.inputs.get('picture_refusal', PENDING))
        p: dict = {'model': model.model_id, 'prompt': req.prompt, 'count': req.count,
                   'aspect_ratio': req.aspect_ratio, 'use_unlim': False}     # credits only, never the free allowance
        if req.kind == 'video' and req.duration_sec:
            p['duration'] = req.duration_sec
        p.update(extra)
        if p.get('medias') and model.inputs.get('picture_mode'):
            p['mode'] = model.inputs['picture_mode']
        return {'params': p}

    def estimate(self, model, req, creds) -> eng.Estimate:
        from mc import desk_engines as eng
        args = self._params(model, req, get_cost=True)
        picture = bool(req.first_frame or req.last_frame or req.reference_images)
        if picture:
            from mc import desk_engine_schemas
            from mc.desk_higgsfield_media_upload import checked_call
            doc = desk_engine_schemas.read(req.engine_id)
            out = checked_call(doc or {}, lambda n, a: self._call(creds, n, a), self._TOOLS[model.kind], args)
        else:
            out = eng._mcp_data(self._call(creds, self._TOOLS[model.kind], args))
        return self._quote(out, note='Text-only price; picture priced at render' if picture else None)

    @staticmethod
    def _quote(out, note=None) -> eng.Estimate:
        from mc import desk_engines as eng
        from mc.desk_mcp_schema_validation import finite_number
        cost = out.get('cost')
        val = (cost.get('credits_exact', cost.get('credits')) if isinstance(cost, dict) else None)
        if not isinstance(val, (int, float)) or not finite_number(val) or val < 0:
            raise eng.EngineError('engine', 'Higgsfield returned no usable credit quote, so nothing was sent')
        adj = out.get('adjustments') if isinstance(out.get('adjustments'), dict) and out.get('adjustments') else None
        return eng.Estimate(usd=0.0, credits=float(val), basis='engine', read=eng.now_iso(), adjustments=adj,
                            note=note, picture_pending=bool(note))

    def prepare(self, model, req, creds):
        """Upload/confirm and quote actual picture inputs, only inside an approved render."""
        from mc import desk_engine_schemas
        from mc.desk_generation_preflight import Prepared
        from mc.desk_higgsfield_media_upload import checked_call, upload
        from mc.desk_higgsfield_picture_contract import bindings
        if not (req.first_frame or req.last_frame or req.reference_images):
            return None
        doc = desk_engine_schemas.read(req.engine_id) or {}
        call = lambda n, a: self._call(creds, n, a)
        # Read every local picture before allocating any remote media.
        from mc import desk_engines as eng
        rows = bindings(model, req)
        for ref, _ in rows:
            eng._read_asset(ref)
        medias = [{'value': upload(doc, call, ref), 'role': role} for ref, role in rows]
        args = self._params(model, req, medias=medias)
        quote_args = {'params': {**args['params'], 'get_cost': True}}
        quote = checked_call(doc, call, self._TOOLS[model.kind], quote_args)
        return Prepared(args, self._quote(quote), doc)

    def submit(self, model, req, creds, *, prepared=None) -> dict:
        from mc import desk_engines as eng
        if req.first_frame or req.last_frame or req.reference_images:
            from mc.desk_higgsfield_media_upload import checked_call
            if prepared is None:
                raise eng.EngineError('engine', 'The picture was not prepared for this approved render')
            out = checked_call(prepared.snapshot, lambda n, a: self._call(creds, n, a),
                               self._TOOLS[model.kind], prepared.arguments, submitted=True)
        else:
            out = eng._mcp_data(self._call(creds, self._TOOLS[model.kind], self._params(model, req)))
        ids = [r.get('id') for r in (out.get('results') or []) if isinstance(r, dict)]
        if not ids or not all(isinstance(i, str) and eng._REF_OK.match(i) for i in ids):
            raise eng.EngineError('engine', 'Higgsfield accepted the job but returned no job id', definitive=False)
        adj = out.get('adjustments') if isinstance(out.get('adjustments'), dict) and out.get('adjustments') else None
        return {'ref': {'job_ids': ids}, 'adjustments': adj}

    def poll(self, model, ref, creds) -> dict:
        from mc import desk_engines as eng
        ids = ref.get('job_ids') or []
        if not ids or not all(isinstance(i, str) and eng._REF_OK.match(i) for i in ids):
            raise eng.EngineError('engine', 'stored job id is malformed')
        urls, state = [], 'ready'
        for jid in ids:
            gen = eng._mcp_data(self._call(creds, 'job_status', {'jobId': jid, 'sync': True})).get('generation')
            gen = gen if isinstance(gen, dict) else {}
            st = str(gen.get('status') or '').lower()
            if st in ('failed', 'error'):
                return {'state': 'failed', 'kind': 'engine', 'free': False,
                        'message': eng._safe(gen.get('error') or 'Higgsfield reported a failure')}
            if st == 'nsfw':
                return {'state': 'failed', 'kind': 'moderation', 'free': True,
                        'message': 'the engine moderation filter rejected the content'}
            if st in ('canceled', 'cancelled'):
                return {'state': 'failed', 'kind': 'canceled', 'message': 'the job was canceled', 'free': True}
            if st in ('completed', 'complete', 'succeeded'):
                url = (gen.get('results') or {}).get('rawUrl') if isinstance(gen.get('results'), dict) else None
                if not isinstance(url, str) or not url:
                    return {'state': 'failed', 'kind': 'engine', 'free': False,
                            'message': 'completed with no output URL'}
                urls.append(url)
            elif st in ('queued', 'pending'):
                state = 'queued' if state == 'ready' else state
            else:
                state = 'rendering'
        if state == 'ready':
            return {'state': 'ready', 'urls': urls}
        return {'state': state}

    def fetch(self, model, item, creds) -> list:
        from mc import desk_engines as eng
        return [eng._download(u) for u in item['urls']]     # signed CDN links: no Higgsfield token is sent
