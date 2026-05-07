# -*- coding: utf-8 -*-
import json
import time
import threading
from typing import Callable, Optional

from flask import session, request, jsonify, Response, stream_with_context


class SSEManager:
    def __init__(self, max_connections: int = 10, max_idle: int = 600, max_duration: int = 1800):
        self._connections = 0
        self._lock = threading.Lock()
        self._max_connections = max_connections
        self._max_idle = max_idle
        self._max_duration = max_duration
        self._invalidated_sessions = set()
        self._invalidated_lock = threading.Lock()

    def invalidate_session(self, session_id: str):
        with self._invalidated_lock:
            self._invalidated_sessions.add(session_id)

    def _consume_invalidation(self, session_id: str) -> bool:
        with self._invalidated_lock:
            if session_id in self._invalidated_sessions:
                self._invalidated_sessions.discard(session_id)
                return True
            return False

    def create_stream(self, data_getter: Callable[[], Optional[dict]], session_id: Optional[str] = None):
        if not session.get("logged_in"):
            return jsonify({"error": "未登录"}), 401

        with self._lock:
            if self._connections >= self._max_connections:
                return jsonify({"error": "连接数已达上限"}), 503
            self._connections += 1

        if session_id is None:
            session_id = session.get("csrf_token", "")

        def generate():
            last_ts = ""
            start_time = time.time()
            idle_count = 0
            try:
                while True:
                    if time.time() - start_time > self._max_duration:
                        break
                    if self._consume_invalidation(session_id):
                        yield f"data: {json.dumps({'type': 'auth_required'})}\n\n"
                        break
                    try:
                        data = data_getter()
                        if data and data.get("timestamp", "") != last_ts:
                            last_ts = data.get("timestamp", "")
                            idle_count = 0
                            yield f"data: {json.dumps(data)}\n\n"
                        else:
                            idle_count += 1
                            if idle_count >= self._max_idle:
                                break
                            yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    except Exception:
                        yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                    time.sleep(1)
            except GeneratorExit:
                pass
            except Exception:
                pass
            finally:
                with self._lock:
                    self._connections -= 1

        resp = Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        resp.headers["Connection"] = "keep-alive"
        return resp
