import contextvars
import json
import logging

request_id_var = contextvars.ContextVar("request_id", default="-")


class _Fmt(logging.Formatter):
    def __init__(self, as_json):
        super().__init__()
        self.as_json = as_json

    def format(self, r):
        base = {"ts": self.formatTime(r, "%Y-%m-%dT%H:%M:%S"), "level": r.levelname, "logger": r.name,
                "request_id": request_id_var.get(), "msg": r.getMessage()}
        if r.exc_info:
            base["exc"] = self.formatException(r.exc_info)
        if self.as_json:
            return json.dumps(base)
        line = f"{base['ts']} {base['level']:<7} [{base['request_id']}] {base['logger']}: {base['msg']}"
        return line + ("\n" + base["exc"] if "exc" in base else "")


def configure_logging(level="INFO", as_json=False):
    h = logging.StreamHandler()
    h.setFormatter(_Fmt(as_json))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level.upper())
