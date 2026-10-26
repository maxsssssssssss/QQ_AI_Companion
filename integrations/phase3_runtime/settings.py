"""Reviewable module switches. These defaults grant no production permission."""
from copy import deepcopy
import math

FEATURES = ("persona", "natural_chat", "memory", "search", "proactive")
CONTINUOUS_PROACTIVE_POLICY = {
    "mode": "continuous", "window_start": 0, "window_end": 0,
    "max_per_day": 5, "max_generations_per_day": 5, "cooldown": 10800,
    "quiet_hours": [0, 9],
}
TUNED_PROACTIVE_POLICY = {
    "mode": "continuous", "window_start": 0, "window_end": 0,
    "max_per_day": 8, "max_generations_per_day": 48, "cooldown": 7200,
    "quiet_hours": [23, 9],
}
TUNED_PROACTIVE_HISTORY = {"summary_limit": 8, "ordinary_retention_hours": 24}


def tuned_proactive_candidate(settings):
    """Prepare the approved wider context/budgets, preserving activation state."""
    result = deepcopy(dict(settings))
    result["proactive_window"] = deepcopy(TUNED_PROACTIVE_POLICY)
    result["proactive_history"] = deepcopy(TUNED_PROACTIVE_HISTORY)
    return validate_settings(result)


def continuous_proactive_candidate(settings):
    """Update the requested policy without arming proactive delivery."""
    result = deepcopy(dict(settings))
    if result.get("proactive", False) is not False:
        raise ValueError("pending_proactive_candidate_requires_disabled_source")
    result["proactive"] = False
    result["proactive_window"] = deepcopy(CONTINUOUS_PROACTIVE_POLICY)
    return validate_settings(result)


def validate_settings(settings):
    # A native host adds schema defaults to old snapshots. A trial's zero
    # generation sentinel must inherit its existing limit, never broaden it.
    settings = deepcopy(dict(settings))
    stored_window = settings.get("proactive_window")
    if (isinstance(stored_window, dict) and stored_window.get("mode", "trial") == "trial"
        and type(stored_window.get("max_generations_per_day")) is int
        and stored_window["max_generations_per_day"] == 0):
        stored_window["max_generations_per_day"] = stored_window.get("max_per_day", 2)
    for key in ("enabled", *FEATURES, "background_web", "memory_recall_guidance"):
        if type(settings.get(key, False)) is not bool:
            raise ValueError("feature_must_be_boolean")
    if settings.get("background_web", False):
        raise ValueError("background_web_requires_separate_release")
    fallback_turns = settings.get("context_compression_fallback_turns", 0)
    if type(fallback_turns) is not int or not 0 <= fallback_turns <= 200:
        raise ValueError("invalid_context_compression_fallback_turns")
    history = settings.get("proactive_history")
    if history is not None:
        if (not isinstance(history, dict)
            or set(history) != {"summary_limit", "ordinary_retention_hours"}
            or type(history.get("summary_limit")) is not int
            or not 1 <= history["summary_limit"] <= 8
            or type(history.get("ordinary_retention_hours")) is not int
            or not 6 <= history["ordinary_retention_hours"] <= 24):
            raise ValueError("invalid_proactive_history")
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
    window = settings.get("proactive_window")
    if settings.get("proactive") or isinstance(window, dict) and window.get("mode") == "continuous":
        if not isinstance(window,dict):
            raise ValueError("approved_proactive_window_required")
        mode = window.get("mode", "trial")
        if mode not in {"trial", "continuous"}:
            raise ValueError("invalid_proactive_mode")
        start,end=window.get("window_start"),window.get("window_end")
        cooldown=window.get("cooldown",14400)
        if any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n)
               for n in (start,end,cooldown)):
            raise ValueError("invalid_proactive_window")
        continuous = mode == "continuous"
        if continuous:
            if start != 0 or end != 0:
                raise ValueError("continuous_proactive_requires_unbounded_window")
        elif not 0 < start < end or end-start > 72*3600:
            raise ValueError("proactive_trial_exceeds_72_hours")
        limit=window.get("max_per_day",2)
        generation_limit = window.get("max_generations_per_day", limit)
        ceiling, generation_ceiling, minimum_cooldown = (8, 48, 7200) if continuous else (2, 2, 14400)
        if (type(limit) is not int or not 1 <= limit <= ceiling
            or type(generation_limit) is not int or not 1 <= generation_limit <= generation_ceiling
            or cooldown < minimum_cooldown):
            raise ValueError("proactive_budget_exceeded")
        quiet=window.get("quiet_hours",[23,9])
        # Keep the previously approved continuous window readable for rollback;
        # the new candidate explicitly narrows it to [23, 9].
        permitted_quiet = ([0, 9], [23, 9]) if continuous else ([23, 9],)
        if not isinstance(quiet,(list,tuple)) or any(type(n) is not int for n in quiet) or list(quiet) not in permitted_quiet:
            raise ValueError("proactive_requires_approved_shanghai_hours")
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
