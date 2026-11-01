"""Chat history storage.

DynamoDB layout (single table, partitioned per user):
    pk = USER#<user id>
    sk = CHAT#<chat id>                       -> title, createdAt, updatedAt
    sk = MSG#<chat id>#<epoch ms>#<0|1>       -> role, text, data (JSON), feedback
    sk = DOC#<chat id>#<epoch ms>             -> facts extracted from an uploaded document (JSON)

Each message is its own item, so long chats never hit DynamoDB's 400 KB item limit.
A message id is "<epoch ms>-<0|1>" (the tail of its sort key).
"""

import json
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


def _now_ms() -> int:
    return int(time.time() * 1000)


def history_lines(messages: list[dict[str, Any]]) -> list[str]:
    """Format messages the way the agent prompt expects."""
    return [f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['text']}" for m in messages]


def _message_id(ts: int, index: int) -> str:
    return f"{ts:013d}-{index}"


def _sort_key(message_id: str) -> tuple[int, int]:
    """'1793520000000-1' -> (1793520000000, 1); message ids sort by time, then position."""
    ts, _, index = message_id.partition("-")
    return int(ts), int(index or 0)


def _exchange(messages: list[dict[str, Any]], message_id: str) -> tuple[str, str]:
    """The answer with this id and the user question just before it."""
    for i, message in enumerate(messages):
        if message.get("id") == message_id:
            question = next((m["text"] for m in reversed(messages[:i]) if m["role"] == "user"), "")
            return question, message.get("text", "")
    return "", ""


class DynamoChatStore:
    def __init__(self, table_name: str, region: str | None = None):
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    @staticmethod
    def _pk(user_id: str) -> str:
        return f"USER#{user_id}"

    def _query_all(self, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            response = self.table.query(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return items
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def _prefix(self, user_id: str, prefix: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self._query_all(
            KeyConditionExpression=Key("pk").eq(self._pk(user_id)) & Key("sk").begins_with(prefix), **kwargs
        )

    @staticmethod
    def _meta(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["sk"].removeprefix("CHAT#"),
            "title": item.get("title", "New chat"),
            "createdAt": int(item.get("createdAt", 0)),
            "updatedAt": int(item.get("updatedAt", 0)),
        }

    @staticmethod
    def _message(item: dict[str, Any]) -> dict[str, Any]:
        _, _, ts, index = item["sk"].split("#")
        message: dict[str, Any] = {
            "id": f"{ts}-{index}",
            "role": item["role"],
            "text": item.get("text", ""),
            "createdAt": int(ts),
        }
        if item.get("data"):
            message["data"] = json.loads(item["data"])
        if item.get("feedback"):
            message["feedback"] = item["feedback"]
        if item.get("feedbackComment"):
            message["feedbackComment"] = item["feedbackComment"]
        return message

    # ------------------------------------------------------------------ chats

    def list_chats(self, user_id: str) -> list[dict[str, Any]]:
        items = self._prefix(user_id, "CHAT#")
        return sorted((self._meta(i) for i in items), key=lambda c: c["updatedAt"], reverse=True)

    def get_chat(self, user_id: str, chat_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": self._pk(user_id), "sk": f"CHAT#{chat_id}"}).get("Item")
        if not item:
            return None
        messages = self._prefix(user_id, f"MSG#{chat_id}#")
        return {**self._meta(item), "messages": [self._message(m) for m in messages]}

    def recent_history(self, user_id: str, chat_id: str, limit: int = 6) -> list[str]:
        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(self._pk(user_id)) & Key("sk").begins_with(f"MSG#{chat_id}#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        newest_first = [self._message(m) for m in response.get("Items", [])]
        return history_lines(list(reversed(newest_first)))

    def add_messages(self, user_id: str, chat_id: str, title: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Append messages ({role, text, data?}) and create the chat if needed. Returns them with id + createdAt."""
        now = _now_ms()
        pk = self._pk(user_id)
        self.table.update_item(
            Key={"pk": pk, "sk": f"CHAT#{chat_id}"},
            UpdateExpression="SET title = if_not_exists(title, :t), createdAt = if_not_exists(createdAt, :n), updatedAt = :n",
            ExpressionAttributeValues={":t": title, ":n": now},
        )
        saved = []
        with self.table.batch_writer() as batch:
            for index, message in enumerate(messages):
                item = {"pk": pk, "sk": f"MSG#{chat_id}#{now:013d}#{index}", "role": message["role"], "text": message["text"]}
                if message.get("data") is not None:
                    item["data"] = json.dumps(message["data"], ensure_ascii=False)
                batch.put_item(Item=item)
                saved.append({**message, "id": _message_id(now, index), "createdAt": now})
        return saved

    def save_turn(self, user_id: str, chat_id: str, title: str, user_text: str, reply: dict[str, Any]) -> list[dict[str, Any]]:
        return self.add_messages(user_id, chat_id, title, [
            {"role": "user", "text": user_text},
            {"role": "assistant", "text": reply["answer"], "data": reply},
        ])

    def set_feedback(self, user_id: str, chat_id: str, message_id: str, rating: str | None, comment: str | None = None) -> bool:
        """Save 👍/👎 (None clears it) and, if given, a written comment on one assistant message."""
        ts, _, index = message_id.partition("-")
        key = {"pk": self._pk(user_id), "sk": f"MSG#{chat_id}#{ts}#{index}"}
        sets, removes, values = ["feedbackAt = :n"], [], {":n": _now_ms()}
        if rating:
            sets.append("feedback = :r")
            values[":r"] = rating
        else:
            removes.append("feedback")
        if comment:
            sets.append("feedbackComment = :c")
            values[":c"] = comment
        expression = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
        try:
            self.table.update_item(Key=key, UpdateExpression=expression, ConditionExpression="attribute_exists(pk)",
                                   ExpressionAttributeValues=values)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_exchange(self, user_id: str, chat_id: str, message_id: str) -> tuple[str, str]:
        """(user question, assistant answer) around an assistant message, for the feedback log."""
        chat = self.get_chat(user_id, chat_id) or {"messages": []}
        return _exchange(chat["messages"], message_id)

    def log_feedback(self, entry: dict[str, Any]) -> None:
        """One shared partition the team can read in a single query (newest last):
        pk = FEEDBACK, sk = <epoch ms>#<message id>."""
        self.table.put_item(Item={"pk": "FEEDBACK", "sk": f"{entry['createdAt']:013d}#{entry['messageId']}", **entry})

    def rename_chat(self, user_id: str, chat_id: str, title: str) -> bool:
        try:
            self.table.update_item(
                Key={"pk": self._pk(user_id), "sk": f"CHAT#{chat_id}"},
                UpdateExpression="SET title = :t",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":t": title},
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def delete_chat(self, user_id: str, chat_id: str) -> None:
        pk = self._pk(user_id)
        keys = self._prefix(user_id, f"MSG#{chat_id}#", ProjectionExpression="pk, sk")
        keys += self._prefix(user_id, f"DOC#{chat_id}#", ProjectionExpression="pk, sk")
        with self.table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key={"pk": key["pk"], "sk": key["sk"]})
            batch.delete_item(Key={"pk": pk, "sk": f"CHAT#{chat_id}"})

    # ------------------------------------------------------------------ documents

    def delete_from(self, user_id: str, chat_id: str, message_id: str) -> int:
        """Delete this message and every later one (edit / regenerate). Returns how many were removed."""
        keys = [k for k in self._prefix(user_id, f"MSG#{chat_id}#", ProjectionExpression="pk, sk")
                if _sort_key(k["sk"].split("#", 2)[2].replace("#", "-")) >= _sort_key(message_id)]
        with self.table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key={"pk": key["pk"], "sk": key["sk"]})
        return len(keys)

    # ------------------------------------------------------------------ documents

    def save_document(self, user_id: str, chat_id: str, document: dict[str, Any], doc_id: str | None = None) -> str:
        """Insert or replace one document record (also used for queued / failed status)."""
        doc_id = doc_id or document.get("doc_id") or f"{_now_ms():013d}"
        self.table.put_item(Item={
            "pk": self._pk(user_id),
            "sk": f"DOC#{chat_id}#{doc_id}",
            "data": json.dumps({**document, "doc_id": doc_id}, ensure_ascii=False),
        })
        return doc_id

    def get_document(self, user_id: str, chat_id: str, doc_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": self._pk(user_id), "sk": f"DOC#{chat_id}#{doc_id}"}).get("Item")
        return json.loads(item["data"]) if item else None

    def list_documents(self, user_id: str, chat_id: str) -> list[dict[str, Any]]:
        docs = []
        for item in self._prefix(user_id, f"DOC#{chat_id}#"):
            doc = json.loads(item["data"])
            doc.setdefault("doc_id", item["sk"].rsplit("#", 1)[-1])
            docs.append(doc)
        return docs


class MemoryChatStore:
    """Same interface, kept in process memory. Used for local development only."""

    def __init__(self) -> None:
        self.chats: dict[tuple[str, str], dict[str, Any]] = {}
        self.documents: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.feedback_log: list[dict[str, Any]] = []

    def list_chats(self, user_id: str) -> list[dict[str, Any]]:
        metas = [{k: v for k, v in c.items() if k != "messages"} for (u, _), c in self.chats.items() if u == user_id]
        return sorted(metas, key=lambda c: c["updatedAt"], reverse=True)

    def get_chat(self, user_id: str, chat_id: str) -> dict[str, Any] | None:
        chat = self.chats.get((user_id, chat_id))
        return {**chat, "messages": [dict(m) for m in chat["messages"]]} if chat else None

    def recent_history(self, user_id: str, chat_id: str, limit: int = 6) -> list[str]:
        chat = self.chats.get((user_id, chat_id))
        return history_lines(chat["messages"][-limit:]) if chat else []

    def add_messages(self, user_id: str, chat_id: str, title: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = _now_ms()
        chat = self.chats.setdefault(
            (user_id, chat_id), {"id": chat_id, "title": title, "createdAt": now, "updatedAt": now, "messages": []}
        )
        chat["updatedAt"] = now
        saved = [{**m, "id": _message_id(now, i), "createdAt": now} for i, m in enumerate(messages)]
        chat["messages"] += saved
        return saved

    def save_turn(self, user_id: str, chat_id: str, title: str, user_text: str, reply: dict[str, Any]) -> list[dict[str, Any]]:
        return self.add_messages(user_id, chat_id, title, [
            {"role": "user", "text": user_text},
            {"role": "assistant", "text": reply["answer"], "data": reply},
        ])

    def set_feedback(self, user_id: str, chat_id: str, message_id: str, rating: str | None, comment: str | None = None) -> bool:
        chat = self.chats.get((user_id, chat_id))
        message = next((m for m in (chat or {}).get("messages", []) if m["id"] == message_id), None)
        if not message:
            return False
        if rating:
            message["feedback"] = rating
        else:
            message.pop("feedback", None)
        if comment:
            message["feedbackComment"] = comment
        return True

    def get_exchange(self, user_id: str, chat_id: str, message_id: str) -> tuple[str, str]:
        chat = self.chats.get((user_id, chat_id)) or {"messages": []}
        return _exchange(chat["messages"], message_id)

    def log_feedback(self, entry: dict[str, Any]) -> None:
        self.feedback_log.append(entry)

    def rename_chat(self, user_id: str, chat_id: str, title: str) -> bool:
        chat = self.chats.get((user_id, chat_id))
        if not chat:
            return False
        chat["title"] = title
        return True

    def delete_chat(self, user_id: str, chat_id: str) -> None:
        self.chats.pop((user_id, chat_id), None)
        self.documents.pop((user_id, chat_id), None)

    def delete_from(self, user_id: str, chat_id: str, message_id: str) -> int:
        chat = self.chats.get((user_id, chat_id))
        if not chat:
            return 0
        keep = [m for m in chat["messages"] if _sort_key(m["id"]) < _sort_key(message_id)]
        removed = len(chat["messages"]) - len(keep)
        chat["messages"] = keep
        return removed

    def save_document(self, user_id: str, chat_id: str, document: dict[str, Any], doc_id: str | None = None) -> str:
        doc_id = doc_id or document.get("doc_id") or f"{_now_ms():013d}"
        docs = self.documents.setdefault((user_id, chat_id), [])
        docs[:] = [d for d in docs if d.get("doc_id") != doc_id] + [{**document, "doc_id": doc_id}]
        return doc_id

    def get_document(self, user_id: str, chat_id: str, doc_id: str) -> dict[str, Any] | None:
        return next((d for d in self.documents.get((user_id, chat_id), []) if d.get("doc_id") == doc_id), None)

    def list_documents(self, user_id: str, chat_id: str) -> list[dict[str, Any]]:
        return list(self.documents.get((user_id, chat_id), []))


def build_chat_store(table_name: str, region: str | None) -> DynamoChatStore | MemoryChatStore:
    return DynamoChatStore(table_name, region) if table_name else MemoryChatStore()
