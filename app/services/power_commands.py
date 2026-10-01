"""Bounded, process-local command receipts; never an offline command queue."""
import asyncio
import secrets
import time


class PowerCommands:
    def __init__(self):
        self.records = {}

    def prune(self):
        now = time.monotonic()
        self.records = {key: value for key, value in self.records.items()
                        if now - value["created"] < 600}

    def start(self, device_id, command, socket):
        self.prune()
        if command not in {"restart", "shutdown"}:
            raise ValueError("Unsupported power command")
        if any(r["device_id"] == device_id and r["socket"] is socket
               and r["status"] != "failed" for r in self.records.values()):
            return None
        record = dict(command_id=secrets.token_urlsafe(16), device_id=device_id,
                      command=command, status="pending", detail=None,
                      created=time.monotonic(), socket=socket, event=asyncio.Event())
        self.records[record["command_id"]] = record
        return record

    def report(self, device_id, socket, message):
        record = self.records.get(message.get("command_id"))
        status = message.get("status")
        transitions = {
            "pending": {"acknowledged", "executing", "completed", "failed"},
            "sent": {"acknowledged", "executing", "completed", "failed"},
            "acknowledged": {"executing", "completed", "failed"},
            "executing": {"completed", "failed"},
        }
        if (not record or record["device_id"] != device_id
                or record["socket"] is not socket
                or record["command"] != message.get("command")
                or status not in transitions.get(record["status"], set())):
            return False
        record["status"] = status
        detail = message.get("detail")
        record["detail"] = detail[:500] if isinstance(detail, str) else None
        record["event"].set()
        return True

    @staticmethod
    def public(record):
        return {key: record[key] for key in
                ("command_id", "device_id", "command", "status", "detail")}


power_commands = PowerCommands()
