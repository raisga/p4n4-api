"""MQTT topic names and filters (`+` matches one level, `#` the rest), per MQTT 3.1.1 §4.7."""

from __future__ import annotations

MAX_TOPIC_CHARS = 256


def check_topic(topic: str) -> str:
    """A topic one may publish to, or ValueError."""
    if not topic or len(topic) > MAX_TOPIC_CHARS:
        raise ValueError(f"topic must be 1-{MAX_TOPIC_CHARS} characters")
    if "+" in topic or "#" in topic:
        raise ValueError("topic can't contain wildcards (+ or #) when publishing")
    if "\0" in topic:
        raise ValueError("topic can't contain a null character")
    if topic.startswith("$"):
        raise ValueError("topics starting with $ belong to the broker")
    return topic


def check_filter(topic_filter: str) -> str:
    """A valid topic filter, or ValueError."""
    if not topic_filter or len(topic_filter) > MAX_TOPIC_CHARS or "\0" in topic_filter:
        raise ValueError(f"{topic_filter!r} is not a topic filter")
    levels = topic_filter.split("/")
    for i, level in enumerate(levels):
        if "#" in level and (level != "#" or i != len(levels) - 1):
            raise ValueError(f"{topic_filter!r}: '#' must be a whole, last level")
        if "+" in level and level != "+":
            raise ValueError(f"{topic_filter!r}: '+' must be a whole level")
    return topic_filter


def matches(topic_filter: str, topic: str) -> bool:
    """Whether `topic` matches `topic_filter`. `#` also matches its parent (`a/#` matches
    `a`), and wildcards at the first level don't match `$` topics."""
    if topic.startswith("$") and topic_filter[:1] in ("+", "#"):
        return False
    filter_levels, topic_levels = topic_filter.split("/"), topic.split("/")
    for i, level in enumerate(filter_levels):
        if level == "#":
            return True
        if i >= len(topic_levels) or (level != "+" and level != topic_levels[i]):
            return False
    return len(filter_levels) == len(topic_levels)
