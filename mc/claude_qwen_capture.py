"""Offline Claude/Qwen record capture before lossy UI parsers.

Supported evidence: repository Claude transcript fixtures and Claude-shaped
Qwen stream-json fixtures in test_provider_runtimes. This is not a CLI-version
certification. Native message.id/record uuid and tool IDs are never invented.
Block addresses (``message-id/block/index``) are canonical structural addresses,
not claimed native block IDs. Prompt echoes are NOT original user messages.
No filesystem, subprocess, network, INIT config dumps, or runtime integration.

State is per source stream/attempt. The composition layer must persist source
identity and bound bookkeeping; this offline decoder keeps replay keys in RAM.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
from typing import Any

from mc.conversation_contract import validate_protocol_event


class CaptureSourceConflict(ValueError):
    pass


@dataclass(frozen=True)
class CaptureEvent:
    kind: str
    payload_json: str
    source_reference: str
    source_sequence: int
    event_index: int
    protocol_version: int = 1

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json)


def _text_id(value: Any) -> bool:
    return isinstance(value,str) and bool(value.strip())


def _unique_object(pairs: list[tuple[str,Any]]) -> dict:
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('duplicate JSON object key')
        result[key] = value
    return result


class ClaudeQwenCapture:
    def __init__(self, provider: str):
        if provider not in {'claude','qwen'}:
            raise ValueError('This decoder only covers Claude/Qwen fixtures')
        self.provider = provider
        self._sources: dict[str,tuple[int,str]] = {}
        self._last_sequence = -1
        self._message_id: str | None = None
        self._blocks: dict[int,dict] = {}
        self._terminal = False
        self._finished = False

    def feed(self, source_reference: str, sequence: int, frame_json: str) -> tuple[CaptureEvent,...]:
        if not _text_id(source_reference) or type(sequence) is not int or sequence < 0 or not isinstance(frame_json,str):
            raise ValueError('Explicit source identity, sequence and JSON text required')
        fingerprint = hashlib.sha256(frame_json.encode('utf-8')).hexdigest()
        if source_reference in self._sources:
            if self._sources[source_reference] == (sequence,fingerprint):
                return ()
            raise CaptureSourceConflict('Source reference reused for different record')
        if sequence <= self._last_sequence:
            raise CaptureSourceConflict('Unseen source sequence must increase')
        if self._finished:
            raise CaptureSourceConflict('Source already finished')
        # Per-attempt decoder is single-owner. Commit source acknowledgement only
        # after decoding/validation succeeds; unexpected failures can be retried.
        old_message,old_blocks,old_terminal = self._message_id,deepcopy(self._blocks),self._terminal
        try:
            result = self._decode_frame(source_reference,sequence,frame_json)
        except BaseException:
            self._message_id,self._blocks = old_message,old_blocks
            self._terminal = old_terminal
            raise
        self._sources[source_reference] = (sequence,fingerprint)
        self._last_sequence = sequence
        return result

    def _decode_frame(self, source_reference: str, sequence: int, frame_json: str) -> tuple[CaptureEvent,...]:
        result: list[CaptureEvent] = []
        def emit(kind: str, payload: dict) -> None:
            validate_protocol_event(kind,payload,version=1)
            encoded = json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
            result.append(CaptureEvent(kind,encoded,source_reference,sequence,len(result)))
        def gap(reason: str) -> None:
            emit('capture_gap',{'reason':reason,'source_reference':source_reference})
        if sequence != self._last_sequence + 1:
            gap('missing_source_sequence')
            # A missing native delta makes every active block incomplete.
            for block in self._blocks.values():
                block['incomplete'] = True
        try:
            record = json.loads(frame_json,object_pairs_hook=_unique_object,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
            # JSON exponent overflow (1e999) is not handled by parse_constant.
            json.dumps(record,allow_nan=False)
        except (ValueError,TypeError,RecursionError):
            gap('malformed_json')
            return tuple(result)
        if not isinstance(record,dict):
            gap('record_not_object')
            return tuple(result)
        kind = record.get('type')
        if kind in ('assistant','user'):
            self._terminal = False
            message = record.get('message')
            if not isinstance(message,dict):
                gap('missing_message_object')
                return tuple(result)
            contents = message.get('content')
            if kind == 'user' and isinstance(contents,str):
                # May be injected system context, resume wrappers or hook text.
                # Preserve its exact text with explicitly untrusted provenance.
                emit('provider_observation',{'name':'provider_prompt_echo','value':{'text':contents,'origin':'unknown'}})
                return tuple(result)
            if not isinstance(contents,list):
                gap('unsupported_message_content')
                return tuple(result)
            message_id = message.get('id') or record.get('uuid')
            offset = record.get('apiBlockIndex',0)
            if type(offset) is not int or offset < 0:
                gap('invalid_block_index')
                return tuple(result)
            for index,block in enumerate(contents):
                if not isinstance(block,dict):
                    gap('malformed_content_block')
                    continue
                block_kind = block.get('type')
                if block_kind == 'tool_use' and kind == 'assistant':
                    if not _text_id(block.get('id')) or not _text_id(block.get('name')) or 'input' not in block:
                        gap('tool_call_missing_identity_or_input')
                    else:
                        emit('tool_call',{'call_id':block['id'],'name':block['name'],'input':block['input']})
                elif block_kind == 'tool_result' and kind == 'user':
                    if not _text_id(block.get('tool_use_id')) or 'content' not in block:
                        gap('tool_result_missing_identity_or_content')
                    elif 'is_error' in block and type(block['is_error']) is not bool:
                        gap('tool_result_invalid_error_flag')
                    else:
                        # Claude/Qwen fixture format omits is_error on success.
                        emit('tool_result',{'call_id':block['tool_use_id'],'output':block['content'],'is_error':block.get('is_error',False)})
                elif block_kind in ('text','thinking'):
                    text = block.get('text' if block_kind == 'text' else 'thinking')
                    if not isinstance(text,str):
                        gap('message_block_missing_text')
                    elif kind == 'user':
                        emit('provider_observation',{'name':'provider_prompt_echo','value':{'text':text,'origin':'unknown'}})
                    elif not _text_id(message_id):
                        gap('assistant_missing_message_identity')
                    else:
                        emit('thinking' if block_kind == 'thinking' else 'assistant_message',
                             {'message_id':message_id,'block_id':f'{message_id}/block/{offset+index}',
                              'text':text,'completeness':'final'})
                else:
                    gap('unsupported_content_block')
        elif kind == 'stream_event':
            event = record.get('event')
            if not isinstance(event,dict):
                gap('missing_stream_event')
            else:
                self._stream(event,emit,gap)
        elif kind == 'system' and record.get('subtype') == 'init':
            # Narrow allowlist only; never forward raw tools/MCP/config/env.
            if _text_id(record.get('model')):
                emit('provider_observation',{'name':'observed_model','value':record['model']})
        elif kind == 'result':
            self._terminal = type(record.get('is_error')) is bool
            if 'result' in record and isinstance(record['result'],str):
                emit('provider_observation',{'name':'terminal_result_text','value':record['result']})
            if record.get('is_error'):
                gap('provider_reported_error')
            elif record.get('is_error') is False:
                emit('provider_observation',{'name':'turn_outcome','value':'reported_success'})
            else:
                gap('terminal_outcome_missing')
        else:
            gap('unsupported_record_type')
        return tuple(result)

    def finish(self, source_reference: str, sequence: int, exit_status: int | None) -> tuple[CaptureEvent,...]:
        """Explicit transport EOF, never inferred proof of native completion.

        No partial block is promoted/finalized here. Replay identity is distinct
        from native frame bytes. A new source/decoder is required after EOF.
        """
        if not _text_id(source_reference) or type(sequence) is not int or sequence < 0 or (exit_status is not None and type(exit_status) is not int):
            raise ValueError('Invalid EOF source or exit status')
        fingerprint = 'finish:' + str(exit_status)
        if source_reference in self._sources:
            if self._sources[source_reference] == (sequence,fingerprint):
                return ()
            raise CaptureSourceConflict('EOF source identity conflict')
        if sequence <= self._last_sequence or self._finished:
            raise CaptureSourceConflict('EOF sequence conflict or source already finished')
        events = []
        def emit(kind,payload):
            validate_protocol_event(kind,payload,version=1)
            events.append(CaptureEvent(kind,json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')),source_reference,sequence,len(events)))
        if sequence != self._last_sequence+1:
            emit('capture_gap',{'reason':'missing_source_sequence','source_reference':source_reference})
        emit('provider_observation',{'name':'transport_eof','value':{'exit_status':exit_status}})
        if not self._terminal:
            emit('capture_gap',{'reason':'missing_native_terminal_result','source_reference':source_reference})
        if self._blocks or self._message_id is not None:
            emit('capture_gap',{'reason':'unfinished_native_stream','source_reference':source_reference})
        self._sources[source_reference] = (sequence,fingerprint)
        self._last_sequence,self._finished = sequence,True
        return tuple(events)

    def _stream(self,event,emit,gap):
        kind = event.get('type')
        if kind == 'message_start':
            self._terminal = False
            message = event.get('message')
            native_id = message.get('id') if isinstance(message,dict) else None
            if self._message_id is not None:
                gap('unfinished_stream_message')
            self._blocks = {}
            self._message_id = native_id if _text_id(native_id) else None
            if self._message_id is None:
                gap('stream_missing_message_identity')
            return
        if kind == 'message_stop':
            if self._blocks:
                gap('stream_stopped_with_unfinished_blocks')
            self._message_id = None
            self._blocks = {}
            return
        if kind == 'message_delta':
            # Stop/usage metadata only; no opaque vendor config forwarding.
            delta = event.get('delta')
            if isinstance(delta,dict) and isinstance(delta.get('stop_reason'),str):
                emit('provider_observation',{'name':'stop_reason','value':delta['stop_reason']})
            else:
                gap('unsupported_message_delta')
            return
        index = event.get('index')
        if self._message_id is None or type(index) is not int or index < 0:
            gap('stream_block_missing_identity')
            return
        address = f'{self._message_id}/block/{index}'
        if kind == 'content_block_start':
            block = event.get('content_block')
            if index in self._blocks or not isinstance(block,dict) or block.get('type') not in ('text','thinking','tool_use'):
                gap('unsupported_or_duplicate_block_start')
                return
            block = dict(block)
            block['parts'] = []
            block['delta_index'] = 0
            block['incomplete'] = False
            self._blocks[index] = block
            text = block.get('text' if block['type']=='text' else 'thinking')
            if block['type'] in ('text','thinking') and not isinstance(text,str):
                block['incomplete'] = True
                gap('block_start_missing_text')
            if isinstance(text,str) and text:
                block['parts'].append(text)
                emit('thinking' if block['type']=='thinking' else 'assistant_message',{'message_id':self._message_id,'block_id':address,'text':text,'completeness':'partial'})
        elif kind == 'content_block_delta':
            block = self._blocks.get(index)
            delta = event.get('delta')
            if block is None or not isinstance(delta,dict):
                gap('delta_without_known_block')
                return
            delta_kind = delta.get('type')
            key = {'text_delta':'text','thinking_delta':'thinking','input_json_delta':'partial_json'}.get(delta_kind) if isinstance(delta_kind,str) else None
            if key is None or not isinstance(delta.get(key),str):
                block['incomplete'] = True
                gap('unsupported_or_malformed_delta')
                return
            expected = {'text':'text_delta','thinking':'thinking_delta','tool_use':'input_json_delta'}[block['type']]
            if delta_kind != expected:
                block['incomplete'] = True
                gap('delta_block_type_mismatch')
                return
            text = delta[key]
            block['parts'].append(text)
            if delta_kind == 'text_delta':
                emit('message_delta',{'message_id':self._message_id,'block_id':address,'delta_index':block['delta_index'],'text':text})
            elif delta_kind == 'thinking_delta':
                emit('thinking',{'message_id':self._message_id,'block_id':address,'text':text,'completeness':'partial'})
            elif _text_id(block.get('id')):
                emit('provider_observation',{'name':'tool_input_delta','value':{'call_id':block['id'],'partial_json':text,'delta_index':block['delta_index']}})
            else:
                gap('tool_delta_missing_call_identity')
            block['delta_index'] += 1
        elif kind == 'content_block_stop':
            block = self._blocks.pop(index,None)
            if block is None:
                gap('stop_without_known_block')
                return
            text = ''.join(block['parts'])
            if block['type'] in ('text','thinking'):
                emit('thinking' if block['type']=='thinking' else 'assistant_message',{'message_id':self._message_id,'block_id':address,'text':text,'completeness':'partial' if block['incomplete'] else 'final'})
            elif block['incomplete']:
                gap('incomplete_tool_input')
            elif not _text_id(block.get('id')) or not _text_id(block.get('name')):
                gap('tool_block_missing_identity')
            else:
                try:
                    value = json.loads(text) if block['parts'] else block['input']
                    emit('tool_call',{'call_id':block['id'],'name':block['name'],'input':value})
                except (ValueError,KeyError):
                    gap('incomplete_tool_input_json')
        else:
            gap('unsupported_stream_event')
