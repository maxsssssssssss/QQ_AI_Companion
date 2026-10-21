"""Pure validation shared by staging and the public AstrBot startup hook.

Exceptions contain only fixed reason codes, never configuration values.
"""
import re


class PolicyViolation(ValueError):
    pass


def validate_policy(config: dict, admin_id: str, platform_id: str, *, require_key=True):
    def require(condition, reason):
        if not condition:
            raise PolicyViolation(reason)

    require(bool(re.fullmatch(r"[1-9][0-9]{4,14}", admin_id)), "invalid_admin_id")
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", platform_id)), "invalid_platform_id")
    require(config.get("admins_id") == [admin_id], "unexpected_administrators")
    platform_settings = config.get("platform_settings", {})
    require(platform_settings.get("enable_id_white_list") is True, "whitelist_disabled")
    require(platform_settings.get("id_whitelist") ==
            [f"{platform_id}:FriendMessage:{admin_id}"], "unexpected_whitelist")
    require(platform_settings.get("unique_session") is True, "shared_sessions")
    for name in ("wl_ignore_admin_on_group", "wl_ignore_admin_on_friend",
                 "reply_with_mention", "reply_with_quote", "no_permission_reply"):
        require(platform_settings.get(name) is False, f"unsafe_{name}")
    require(platform_settings.get("segmented_reply", {}).get("enable") is False,
            "segmented_reply_enabled")
    platforms = config.get("platform", [])
    require(len(platforms) == 1, "unexpected_platform_count")
    platform = platforms[0]
    require(platform.get("id") == platform_id and platform.get("type") == "aiocqhttp",
            "unexpected_platform")
    require(platform.get("ws_reverse_host") == "0.0.0.0" and
            platform.get("ws_reverse_port") == 6199, "unexpected_onebot_endpoint")
    require(len(str(platform.get("ws_reverse_token", ""))) >= 32, "weak_onebot_token")
    provider_settings = config.get("provider_settings", {})
    for name in ("streaming_response", "web_search", "show_tool_use_status",
                 "show_tool_call_result"):
        require(provider_settings.get(name) is False, f"unsafe_{name}")
    require(provider_settings.get("computer_use_runtime") == "none",
            "computer_tools_enabled")
    require(provider_settings.get("file_extract", {}).get("enable") is False,
            "file_extraction_enabled")
    require(provider_settings.get("proactive_capability", {}).get("add_cron_tools") is False,
            "cron_tools_enabled")
    runner = config.get("agent_runner", {})
    require(runner.get("runner_type") == "local", "unexpected_agent_runner")
    runner_config = runner.get("config", {})
    require(runner_config.get("persona", {}).get("persona_id") == "qq_companion",
            "unexpected_persona")
    require(runner_config.get("model", {}).get("provider_id") == "deepseek/flash",
            "unexpected_default_model")
    # AstrBot 4.29.1 defines this as total transport attempts, including the
    # initial request. One is its minimum and disables transport retries.
    attempts = runner_config.get("model", {}).get("request_max_retries")
    require(type(attempts) is int and attempts == 1,
            "automatic_model_retries")
    require(runner_config.get("model", {}).get("fallback_provider_ids") == [],
            "implicit_model_fallback")
    require(config.get("t2i") is False, "image_rendering_enabled")
    require(config.get("timezone") == "Asia/Shanghai", "unexpected_timezone")
    sources = config.get("provider_sources", [])
    require(len(sources) == 1, "unexpected_provider_sources")
    source = sources[0]
    require(source.get("id") == "deepseek" and
            source.get("type") == "openai_chat_completion" and
            source.get("api_base") in {"https://api.deepseek.com", "https://api.deepseek.com/v1"},
            "unexpected_provider_endpoint")
    models = {model.get("id"): model for model in config.get("provider", [])}
    require(set(models) == {"deepseek/flash", "deepseek/pro"}, "unexpected_models")
    for model_id, model_name in (("deepseek/flash", "deepseek-flash"),
                                 ("deepseek/pro", "deepseek-v4-pro")):
        model = models[model_id]
        require(model.get("model") == model_name and model.get("provider_source_id") == "deepseek",
                "unexpected_model_mapping")
        extra = model.get("custom_extra_body", {})
        require(extra.get("thinking") == {"type": "disabled"}, "unbounded_thinking")
        limit = extra.get("max_tokens")
        require(isinstance(limit, int) and 1 <= limit <= 2048, "unbounded_output")
    if require_key:
        require(source.get("enable") is True and provider_settings.get("enable") is True,
                "deepseek_not_enabled")
        keys = source.get("key", [])
        require(isinstance(keys, list) and len(keys) == 1 and
                isinstance(keys[0], str) and len(keys[0]) >= 16,
                "missing_deepseek_key")
        require(models["deepseek/flash"].get("enable") is True, "flash_model_disabled")
    return True
