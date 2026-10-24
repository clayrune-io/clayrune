"""Idempotently accept one campaign suggestion into the existing piece store.

Original suggestions remain untouched. A stable source fingerprint associates
the saved piece with its proposal without trusting a client-invented body/id.
"""
from __future__ import annotations
import hashlib
import json
from mc import desk, desk_pieces as pieces
from mc.core import now_iso


def key(suggestion: dict) -> str:
    return hashlib.sha256(json.dumps(suggestion, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def projected(store: dict) -> list[dict]:
    out = []
    for campaign in (store.get('campaigns') or {}).values():
        accepted = set((campaign.get('suggestion_pieces') or {}).keys())
        accepted.update({(p.get('source') or {}).get('ref') for p in pieces._pieces(store).values()
                         if p.get('campaign_id') == campaign['id']
                         and (p.get('source') or {}).get('kind') == 'suggestion'})
        for suggestion in (campaign.get('suggestions') or {}).get('what') or []:
            fingerprint = key(suggestion)
            if fingerprint in accepted:
                continue
            accepted.add(fingerprint)
            out.append({'id': 'suggest-' + fingerprint[:32], 'campaignId': campaign['id'],
                        'projectId': campaign.get('project_id'), 'kind': suggestion.get('kind') or 'post',
                        'title': suggestion['title'], 'body': '', 'assets': [], 'versions': [],
                        'suggestion': suggestion, 'archivedAt': None})
    return out


def materialize(campaign_id: str, title: str, channel_id=None) -> dict:
    with desk._store_lock:
        store = desk._read_store()
        campaign = pieces._campaign_of(store, campaign_id)
        candidates = [s for s in (campaign.get('suggestions') or {}).get('what') or []
                      if s.get('title') == title and (channel_id is None or s.get('channelId') == channel_id)]
        if not candidates:
            raise pieces.PieceError('that suggestion is no longer available; refresh the campaign', 409)
        suggestion = candidates[0]
        fingerprint = key(suggestion)
        source = {'kind': 'suggestion', 'ref': fingerprint}
        refs = campaign.setdefault('suggestion_pieces', {})
        if fingerprint in refs:
            existing = pieces._pieces(store).get(refs[fingerprint])
            if existing is None:
                raise pieces.PieceError('this suggestion was already used; restore its deleted post instead', 409)
            return pieces.v1_piece(existing)
        for piece in pieces._pieces(store).values():
            if piece.get('campaign_id') == campaign_id and piece.get('source') == source:
                refs[fingerprint] = piece['id']
                desk._write_store(store)
                return pieces.v1_piece(piece)
        now = now_iso()
        piece = {'id': desk._new_id('piece'), 'campaign_id': campaign_id,
                 'project_id': campaign.get('project_id'), 'kind': suggestion.get('kind') or 'post',
                 'title': suggestion['title'], 'body': '', 'word_count': None,
                 'source': source, 'because': suggestion.get('because') or [],
                 'assets': [], 'claims': [], 'versions': [], 'draft_status': None,
                 'created_at': now, 'updated_at': now}
        pieces._pieces(store)[piece['id']] = piece
        refs[fingerprint] = piece['id']
        desk._write_store(store)
        return pieces.v1_piece(piece)
