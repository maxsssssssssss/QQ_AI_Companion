"""Reviewable module switches. These defaults grant no production permission."""
from copy import deepcopy
import math

FEATURES = ("persona", "natural_chat", "memory", "search", "proactive")


def validate_settings(settings):
    for key in ("enabled", *FEATURES, "background_web", "memory_recall_guidance"):
        if type(settings.get(key, False)) is not bool:
            raise ValueError("feature_must_be_boolean")
    if settings.get("background_web", False):
        raise ValueError("background_web_requires_separate_release")
    if any(settings.get(k) for k in FEATURES) and not settings.get("enabled"):
        raise ValueError("runtime_not_enabled")
    if any(settings.get(k) for k in FEATURES[1:]) and not settings.get("persona"):
        raise ValueError("persona_required")
    if any(settings.get(k) for k in ("memory", "search", "proactive")) and not settings.get("natural_chat"):
        raise ValueError("audited_delivery_required")
    aliases = settings.get('memory_topic_aliases', {})
    if (not isinstance(aliases, dict) or len(aliases)>32
        or any(not isinstance(k,str) or not 1<=len(k)<=50
            or not isinstance(v,list) or not 1<=len(v)<=12
            or any(not isinstance(term,str) or not 1<=len(term)<=40 for term in v)
            for k,v in aliases.items())):
        raise ValueError('invalid_memory_topic_aliases')
    if settings.get("proactive"):
        window=settings.get("proactive_window")
        if not isinstance(window,dict):
            raise ValueError("approved_proactive_window_required")
        start,end=window.get("window_start"),window.get("window_end")
        cooldown=window.get("cooldown",14400)
        if any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n)
               for n in (start,end,cooldown)):
            raise ValueError("invalid_proactive_window")
        if not 0 < start < end or end-start > 72*3600:
            raise ValueError("proactive_trial_exceeds_72_hours")
        limit=window.get("max_per_day",2)
        if type(limit) is not int or not 1 <= limit <= 2 or cooldown < 14400:
            raise ValueError("proactive_trial_budget_exceeded")
        quiet=window.get("quiet_hours",[23,9])
        if not isinstance(quiet,(list,tuple)) or any(type(n) is not int for n in quiet) or list(quiet) != [23,9]:
            raise ValueError("proactive_trial_requires_0900_2300_shanghai")
    # AstrBotConfig is a dict subclass with runtime locks on the instance;
    # copy its JSON payload, never deepcopy the host configuration object.
    return deepcopy(dict(settings))


def private_config(original):
    """Disable conflicting/unsupported native behaviors, preserving native APIs."""
    result = deepcopy(original)
    deny = {"enable_message_debounce", "enable_smart_message_debounce",
            "enable_semantic_message_debounce", "enable_segmented_proactive_reply",
            "enable_llm_controlled_segmenting", "enable_daily_plan", "include_schedule_in_messages",
            "enable_news_integration", "enable_news_boredom_read", "enable_news_daily_hot_read",
            "enable_web_exploration", "enable_web_exploration_boredom_search"}
    def walk(obj):
        for key, value in obj.items():
            if key in deny:
                obj[key] = False
            elif isinstance(value, dict):
                walk(value)
    walk(result)
    return result
