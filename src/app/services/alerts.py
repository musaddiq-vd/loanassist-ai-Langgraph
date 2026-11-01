"""Email alerts for answer feedback, sent through an SNS topic (email subscription)."""

import logging
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def should_alert(rating: str | None, comment: str | None) -> bool:
    """Alert the team on a 👎 or any written comment; plain 👍 is only logged."""
    return rating == "down" or bool(comment and comment.strip())


def feedback_alert_text(entry: dict[str, Any]) -> tuple[str, str]:
    rating = {"up": "Helpful (thumbs up)", "down": "Not helpful (thumbs down)"}.get(entry.get("rating"), "No rating")
    when = datetime.fromtimestamp(entry["createdAt"] / 1000, tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")
    # SNS subjects must be plain ASCII and under 100 characters.
    subject = f"LoanAssist feedback: {'needs attention' if entry.get('rating') == 'down' else 'new comment'}"
    body = "\n".join([
        "New feedback on a LoanAssist answer.",
        "",
        f"Rating:   {rating}",
        f"Comment:  {entry.get('comment') or '-'}",
        f"When:     {when}",
        f"Tool:     {entry.get('toolUsed') or '-'}",
        "",
        "Question:",
        entry.get("question") or "-",
        "",
        "Answer (first 1,000 characters):",
        (entry.get("answer") or "-")[:1000],
        "",
        f"Chat {entry['chatId']} · message {entry['messageId']} · user {entry['userId']}",
        "All feedback is stored in DynamoDB (pk = FEEDBACK) in the chats table.",
    ])
    return subject, body


class FeedbackAlerts:
    def __init__(self, topic_arn: str, region: str | None):
        self.topic_arn = topic_arn
        self.sns = boto3.client("sns", region_name=region) if topic_arn else None

    def send(self, entry: dict[str, Any]) -> None:
        if not self.sns:
            return
        subject, body = feedback_alert_text(entry)
        try:
            self.sns.publish(TopicArn=self.topic_arn, Subject=subject, Message=body)
        except Exception:
            # Alerts are best-effort; the feedback itself is already saved.
            logger.exception("Feedback alert failed")
