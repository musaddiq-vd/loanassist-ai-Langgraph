"""Chat history storage.

DynamoDB layout (single table, partitioned per user):
    pk = USER#<user id>
    sk = CHAT#<chat id>                       -> title, createdAt, updatedAt
    sk = MSG#<chat id>#<epoch ms>#<0|1>       -> role, text, data (JSON)

Each message is its own item, so long chats never hit DynamoDB's 400 KB item limit.
"""

import json
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


def _now_ms() -> int:
    return int(time.time() * 1000)


def _history_lines(messages: list[dict[str, Any]]) -> list[str]:
    """Format messages the way the agent prompt expects."""
    return [f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['text']}" for m in messages]


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
        message: dict[str, Any] = {"role": item["role"], "text": item.get("text", "")}
        if item.get("data"):
            message["data"] = json.loads(item["data"])
        return message

    def list_chats(self, user_id: str) -> list[dict[str, Any]]:
        items = self._query_all(
            KeyConditionExpression=Key("pk").eq(self._pk(user_id)) & Key("sk").begins_with("CHAT#")
        )
        return sorted((self._meta(i) for i in items), key=lambda c: c["updatedAt"], reverse=True)

    def get_chat(self, user_id: str, chat_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"pk": self._pk(user_id), "sk": f"CHAT#{chat_id}"}).get("Item")
        if not item:
            return None
        messages = self._query_all(
            KeyConditionExpression=Key("pk").eq(self._pk(user_id)) & Key("sk").begins_with(f"MSG#{chat_id}#")
        )
        return {**self._meta(item), "messages": [self._message(m) for m in messages]}

    def recent_history(self, user_id: str, chat_id: str, limit: int = 6) -> list[str]:
        response = self.table.query(
            KeyConditionExpression=Key("pk").eq(self._pk(user_id)) & Key("sk").begins_with(f"MSG#{chat_id}#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        newest_first = [self._message(m) for m in response.get("Items", [])]
        return _history_lines(list(reversed(newest_first)))

    def save_turn(self, user_id: str, chat_id: str, title: str, user_text: str, reply: dict[str, Any]) -> None:
        now = _now_ms()
        pk = self._pk(user_id)
        self.table.update_item(
            Key={"pk": pk, "sk": f"CHAT#{chat_id}"},
            UpdateExpression="SET title = if_not_exists(title, :t), createdAt = if_not_exists(createdAt, :n), updatedAt = :n",
            ExpressionAttributeValues={":t": title, ":n": now},
        )
        with self.table.batch_writer() as batch:
            batch.put_item(Item={"pk": pk, "sk": f"MSG#{chat_id}#{now:013d}#0", "role": "user", "text": user_text})
            batch.put_item(Item={
                "pk": pk,
                "sk": f"MSG#{chat_id}#{now:013d}#1",
                "role": "assistant",
                "text": reply["answer"],
                "data": json.dumps(reply, ensure_ascii=False),
            })

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
        keys = self._query_all(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(f"MSG#{chat_id}#"),
            ProjectionExpression="pk, sk",
        )
        with self.table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key={"pk": key["pk"], "sk": key["sk"]})
            batch.delete_item(Key={"pk": pk, "sk": f"CHAT#{chat_id}"})


class MemoryChatStore:
    """Same interface, kept in process memory. Used for local development only."""

    def __init__(self) -> None:
        self.chats: dict[tuple[str, str], dict[str, Any]] = {}

    def list_chats(self, user_id: str) -> list[dict[str, Any]]:
        metas = [{k: v for k, v in c.items() if k != "messages"} for (u, _), c in self.chats.items() if u == user_id]
        return sorted(metas, key=lambda c: c["updatedAt"], reverse=True)

    def get_chat(self, user_id: str, chat_id: str) -> dict[str, Any] | None:
        chat = self.chats.get((user_id, chat_id))
        return {**chat, "messages": list(chat["messages"])} if chat else None

    def recent_history(self, user_id: str, chat_id: str, limit: int = 6) -> list[str]:
        chat = self.chats.get((user_id, chat_id))
        return _history_lines(chat["messages"][-limit:]) if chat else []

    def save_turn(self, user_id: str, chat_id: str, title: str, user_text: str, reply: dict[str, Any]) -> None:
        now = _now_ms()
        chat = self.chats.setdefault(
            (user_id, chat_id), {"id": chat_id, "title": title, "createdAt": now, "updatedAt": now, "messages": []}
        )
        chat["updatedAt"] = now
        chat["messages"] += [
            {"role": "user", "text": user_text},
            {"role": "assistant", "text": reply["answer"], "data": reply},
        ]

    def rename_chat(self, user_id: str, chat_id: str, title: str) -> bool:
        chat = self.chats.get((user_id, chat_id))
        if not chat:
            return False
        chat["title"] = title
        return True

    def delete_chat(self, user_id: str, chat_id: str) -> None:
        self.chats.pop((user_id, chat_id), None)


def build_chat_store(table_name: str, region: str | None) -> DynamoChatStore | MemoryChatStore:
    return DynamoChatStore(table_name, region) if table_name else MemoryChatStore()
