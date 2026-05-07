# -*- coding: utf-8 -*-
import time
import threading
import logging
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)


class ThreadSafeCache:
    def __init__(self, ttl: Optional[float] = None):
        self._data = None
        self._lock = threading.Lock()
        self._set_time = 0
        self._ttl = ttl

    def get(self) -> Optional[Any]:
        with self._lock:
            if self._data is None:
                return None
            if self._ttl and time.time() - self._set_time > self._ttl:
                self._data = None
                return None
            return self._data

    def set(self, data: Any):
        with self._lock:
            self._data = data
            self._set_time = time.time()

    def clear(self):
        with self._lock:
            self._data = None
            self._set_time = 0

    def get_or_refresh(self, refresh_fn: Callable[[], Optional[Any]], fallback_fn: Optional[Callable[[], Optional[Any]]] = None) -> Optional[Any]:
        data = self.get()
        if data is not None:
            return data
        try:
            data = refresh_fn()
            if data is not None:
                self.set(data)
                return data
        except Exception:
            pass
        if fallback_fn:
            try:
                data = fallback_fn()
                if data is not None:
                    self.set(data)
                    return data
            except Exception:
                pass
        return data


class BackgroundRefreshLoop:
    def __init__(self, name: str = "bg-refresh"):
        self._name = name
        self._tasks = []
        self._started = False
        self._lock = threading.Lock()

    def add_task(self, fn: Callable[[], None], name: Optional[str] = None):
        self._tasks.append({"fn": fn, "name": name or fn.__name__})

    def start(self, interval_getter: Optional[Callable[[], float]] = None, default_interval: float = 300.0):
        with self._lock:
            if self._started:
                return
            self._started = True
        t = threading.Thread(target=self._loop, args=(interval_getter, default_interval), daemon=True)
        t.start()

    def _loop(self, interval_getter, default_interval):
        for task in self._tasks:
            try:
                task["fn"]()
            except Exception:
                logger.debug("初始执行 %s 失败", task["name"], exc_info=True)
        while True:
            try:
                interval = default_interval
                if interval_getter:
                    try:
                        interval = interval_getter()
                    except Exception:
                        pass
                time.sleep(interval)
            except Exception:
                time.sleep(default_interval)
            for task in self._tasks:
                try:
                    task["fn"]()
                except Exception:
                    logger.debug("执行 %s 失败", task["name"], exc_info=True)
