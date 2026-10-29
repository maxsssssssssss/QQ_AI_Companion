"""Synthetic relaxed-public-policy regression; no external API or private data."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.phase3_search.policy import SEARCH, FETCH, PublicPolicy, Rejected


async def public_dns(host):
    return ("1.1.1.1",)


class RelaxedPublicQueryTests(unittest.TestCase):
    def setUp(self):
        self.policy = PublicPolicy(scope="general_public", resolver=public_dns)

    def accepted(self, query, source="那你搜一下"):
        self.assertEqual(self.policy.query(query, source_text=source), query)

    def test_incident_query_allows_public_related_terms(self):
        self.accepted("炉石传说 米尔豪斯 法师 新版本 卡组", "米尔豪斯法师新版本的你可以搜一搜")

    def test_new_version_is_independent_of_spaces_and_cjk_segmentation(self):
        for query in ("米尔豪斯法师 新版本", "米尔豪斯 法师 新版本", "米尔豪斯法师新版本"):
            with self.subTest(query=query): self.accepted(query)

    def test_translations_aliases_and_related_public_terms(self):
        for query in ("Millhouse Manastorm mage Hearthstone deck", "No Man's Sky 最新更新", "Python coroutine 官方文档"):
            with self.subTest(query=query): self.accepted(query)

    def test_public_topic_may_be_resolved_from_conversation(self):
        self.accepted("James Webb telescope latest images", "刚才说的那个，你查一下")

    def test_no_model_authority_flag_is_needed(self):
        self.accepted("JavaScript promise TypeScript await", "它跟刚才那个有区别吗")

    def test_generic_security_words_are_public_topics(self):
        for query in ("token pricing DeepSeek", "password managers comparison", "API key 安全存储最佳实践", "密码 哈希 原理", "身份证 校验算法", "QQ companion chatbot"):
            with self.subTest(query=query): self.accepted(query)

    def test_family_words_are_not_automatic_private_facts(self):
        for query in ("妈妈 母亲节 起源", "女朋友 桌游 双人游戏", "我的世界 最新版本"):
            with self.subTest(query=query): self.accepted(query)

    def test_public_dates_versions_and_numeric_ids_not_blanket_blocked(self):
        for query in ("Python 3.12 release", "公历 20261025", "arXiv 2501.12345", "ISBN 9787302423287"):
            with self.subTest(query=query): self.accepted(query)

    def test_public_search_syntax_is_supported(self):
        self.accepted('site:python.org "asyncio task" -deprecated')

    def test_missing_or_invalid_inbound_authority_still_rejected(self):
        for source in (None, "", " "*4, [], "x"*32769):
            with self.subTest(source_type=type(source).__name__), self.assertRaisesRegex(Rejected, "query_source_unavailable"):
                self.policy.query("Python docs", source_text=source)

    def test_explicit_do_not_search_and_private_source_intent_are_preserved(self):
        for source in ("不要联网，告诉我 Python 怎么用", "只根据已保存资料回答 Python 问题", "不要把聊天对外上传", "Use my private profile to find Python preferences"):
            with self.subTest(source=source), self.assertRaises(Rejected):
                self.policy.query("Python docs", source_text=source)

    def test_public_recollection_official_sources_and_privacy_technology_are_allowed(self):
        for source, query in (
            ("你记得米尔豪斯吗，搜一下新卡组", "炉石传说 米尔豪斯 法师 新卡组"),
            ("只根据官方资料回答Python异步任务", "Python asyncio 官方文档"),
            ("查一下个人资料保护技术", "个人资料保护技术 数据最小化"),
            ("你记得我的世界吗，查一下最近更新", "Minecraft latest update"),
            ("Search for private profile protection techniques", "private profile protection techniques"),
        ):
            with self.subTest(source=source): self.accepted(query, source)

    def test_explicit_saved_owned_and_chat_history_sources_stay_blocked(self):
        for source in (
            "你记得我的生日吗", "你记得我的 Python 版本吗", "只根据已保存的资料回答",
            "仅依据聊天历史回答", "只用我们以前的聊天记录", "根据我的私人资料查一下",
            "我的个人档案里有什么", "仅使用本地记忆", "Use my private profile to find preferences",
            "Only use saved memories", "不要把聊天对外上传，聊聊 Python",
        ):
            with self.subTest(source=source), self.assertRaisesRegex(Rejected, "private_source_intent"):
                self.policy.query("Python official docs", source_text=source)

    def test_concrete_secrets_rejected_even_if_present_in_current_input(self):
        for value in ("sk-synthetic", "password=hunter2", "token:abcdefghijklmn", "API key = test-secret-value", "Bearer abcdef0123456789abcdef", "-----BEGIN PRIVATE KEY-----"):
            with self.subTest(value=value), self.assertRaisesRegex(Rejected, "private_query"):
                self.policy.query("查询 " + value, source_text="查询 " + value)

    def test_concrete_personal_identifiers_rejected(self):
        for value in ("alice@example.org", "13812345678", "+86 138-1234-5678", "110101199001011234", "123-45-6789", "QQ号：123456789", "微信号是example_person"):
            with self.subTest(value=value), self.assertRaisesRegex(Rejected, "private_query"):
                self.policy.query("查询 " + value, source_text="查一下公开资料")

    def test_private_narrative_assignments_rejected(self):
        for query in ("我的住址是 合成地址", "用户的姓名：Synthetic Person", "我的生日为 1998-04-02", "user: private past assistant: private reply"):
            with self.subTest(query=query), self.assertRaisesRegex(Rejected, "private_query"):
                self.policy.query(query, source_text="搜一下")

    def test_exact_locally_protected_values_rejected(self):
        for value in ("SyntheticPrivateName", "1998-04-02", "synthetic-provider-secret", "123456789"):
            with self.subTest(value=value), self.assertRaisesRegex(Rejected, "private_query"):
                self.policy.query("Python " + value, source_text="查询 Python", protected_values=(value,))

    def test_protection_accepts_only_local_container_of_strings(self):
        for values in ("syntheticsecret", {}, [123], ["x"*4097]):
            with self.subTest(kind=type(values).__name__), self.assertRaisesRegex(Rejected, "invalid_privacy_context"):
                self.policy.query("Python", source_text="Python", protected_values=values)

    def test_percent_encoded_and_unicode_normalized_secret_rejected(self):
        for query in ("%73%6b-synthetic", "ｓｋ-synthetic", "Python synthetic%2Dsecret"):
            with self.subTest(query=query), self.assertRaisesRegex(Rejected, "private_query"):
                self.policy.query(query, source_text="Python", protected_values=("synthetic-secret",))

    def test_controls_empty_punctuation_and_length_remain_bounded(self):
        for query in ("Python\n文档", "Python%0a文档", "Python\x7f文档", "", "x"*129, "!?!"):
            with self.subTest(query=repr(query)), self.assertRaises(Rejected):
                self.policy.query(query, source_text="Python")

    def test_reviewed_mode_remains_closed(self):
        policy = PublicPolicy(public_terms=["DeepSeek"])
        self.assertEqual(policy.query("DeepSeek 官方文档"), "DeepSeek 官方文档")
        with self.assertRaisesRegex(Rejected, "unreviewed_public_terms"):
            policy.query("Python 文档", source_text="Python")


class RelaxedPublicURLTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.policy = PublicPolicy(scope="general_public", resolver=public_dns)

    async def test_public_query_parameters_preserved_exactly(self):
        for url in ("https://public.example.org/article?id=12345&page=2", "https://public.example.org/search?q=token%20pricing&lang=zh-CN", "https://public.example.org/search?q=C%2B%2B", "https://public.example.org/a?code=python"):
            with self.subTest(url=url): self.assertEqual(await self.policy.url(url), url)

    async def test_once_encoded_ascii_path_spaces_preserve_exact_url(self):
        for path in ("/a%20b.pdf", "/Research%20Paper%20-%202025.pdf", "/two%20%20spaces.pdf"):
            url = "https://public.example.org" + path + "?page=2"
            with self.subTest(path=path):
                self.assertEqual(await self.policy.url(url), url)
                self.assertEqual(await self.policy.arguments(FETCH, {"url": url}), {"url": url})

    async def test_path_space_exception_does_not_allow_literal_or_other_whitespace(self):
        for middle in (" ", "\t", "\n", "%09", "%0A", "%0D", "%00", "%7F", "%C2%85", "%C2%A0", "%E3%80%80"):
            with self.subTest(middle=middle), self.assertRaises(Rejected):
                await self.policy.url("https://public.example.org/a" + middle + "b.pdf")

    async def test_nested_space_encoding_is_not_the_path_space_exception(self):
        for path in ("/a%2520b", "/a%252520b", "/a%25%32%30b", "/a%20b%2520c"):
            with self.subTest(path=path), self.assertRaisesRegex(Rejected, "unsafe_url"):
                await self.policy.url("https://public.example.org" + path)

    async def test_path_space_exception_does_not_enable_path_delimiters_or_backslash(self):
        for path in ("/a%20b%3Ftoken=x", "/a%20b%23fragment", "/a%20b%5Cc", "/a%20b%255Cc", "/a%20b\\c"):
            with self.subTest(path=path), self.assertRaises(Rejected):
                await self.policy.url("https://public.example.org" + path)

    async def test_path_space_exception_keeps_privacy_checks_before_dns(self):
        calls = []
        async def resolver(host):
            calls.append(host)
            return ("1.1.1.1",)
        policy = PublicPolicy(scope="general_public", resolver=resolver)
        for path in ("/a%20b?token=synthetic", "/Bearer%20abcdefghijklmnop", "/synthetic%20private"):
            with self.subTest(path=path), self.assertRaisesRegex(Rejected, "private_url"):
                await policy.url("https://public.example.org" + path, protected_values=("synthetic private",))
        self.assertEqual(calls, [])

    async def test_path_space_exception_keeps_nonpublic_and_mixed_dns_blocked(self):
        for addresses in (("127.0.0.1",), ("1.1.1.1", "10.1.2.3"), ("::1",)):
            async def resolver(host):return addresses
            policy = PublicPolicy(scope="general_public", resolver=resolver)
            with self.subTest(addresses=addresses), self.assertRaisesRegex(Rejected, "nonpublic_address"):
                await policy.url("https://public.example.org/a%20b.pdf")

    async def test_reviewed_path_spaces_still_require_approved_host(self):
        policy = PublicPolicy(fetch_hosts=["public.example.org"], resolver=public_dns)
        url = "https://public.example.org/a%20b.pdf"
        self.assertEqual(await policy.url(url), url)
        with self.assertRaisesRegex(Rejected, "unreviewed_fetch_host"):
            await policy.url("https://other.example.org/a%20b.pdf")

    async def test_anchor_is_removed_without_changing_query(self):
        self.assertEqual(await self.policy.url("https://public.example.org/a?id=12#details"), "https://public.example.org/a?id=12")

    async def test_public_security_topic_paths_are_allowed(self):
        for url in ("https://public.example.org/password-managers", "https://token.example.org/pricing", "https://public.example.org/%70%61ssword"):
            with self.subTest(url=url): self.assertEqual(await self.policy.url(url), url)

    async def test_auth_signed_and_personal_parameters_rejected(self):
        for param in ("token=abc", "access_token=abc", "password=abc", "api_key=abc", "auth=abc", "signature=abc", "X-Amz-Signature=abc", "email=alice%40example.org", "phone=13812345678", "qq=123456789"):
            with self.subTest(param=param), self.assertRaisesRegex(Rejected, "private_url"):
                await self.policy.url("https://public.example.org/a?" + param)

    async def test_form_encoded_sensitive_values_rejected_before_dns(self):
        calls = []
        async def resolver(host):
            calls.append(host)
            return ("1.1.1.1",)
        policy = PublicPolicy(scope="general_public", resolver=resolver)
        for query in ("q=Bearer+abcdefghijklmnop", "q=API+key+%3D+syntheticvalue",
                      "q=QQ+123456789", "q=synthetic+secret", "q=x%0Ay"):
            with self.subTest(query=query), self.assertRaises(Rejected):
                await policy.url("https://public.example.org/a?" + query,
                                 protected_values=("synthetic secret",))
        self.assertEqual(calls, [])

    async def test_literal_plus_and_encoded_delimiters_keep_original_structure(self):
        for query in ("q=C%2B%2B", "q=Bearer%2Babcdefghijklmnop", "q=A%26B", "q=foo%3Dbar"):
            url = "https://public.example.org/a?" + query
            with self.subTest(query=query): self.assertEqual(await self.policy.url(url), url)
        # A literal plus must not be reinterpreted as a space for local secret
        # matching; the distinct form-encoded version is correctly rejected.
        encoded = "https://public.example.org/a?q=synthetic%2Bsecret"
        self.assertEqual(await self.policy.url(encoded, protected_values=("synthetic secret",)), encoded)
        with self.assertRaisesRegex(Rejected, "private_url"):
            await self.policy.url("https://public.example.org/a?q=synthetic+secret",
                                  protected_values=("synthetic secret",))

    async def test_known_secret_rejected_in_path_host_query_and_fragment(self):
        for url in ("https://public.example.org/synthetic-secret", "https://synthetic-secret.example.org/a", "https://public.example.org/a?q=synthetic%2Dsecret", "https://public.example.org/a#synthetic-secret"):
            with self.subTest(url=url), self.assertRaisesRegex(Rejected, "private_url"):
                await self.policy.url(url, protected_values=("synthetic-secret",))

    async def test_nested_encoding_does_not_hide_credentials(self):
        for url in ("https://public.example.org/%2573%256b-synthetic", "https://public.example.org/a?%2574oken=abc", "https://public.example.org/a?q=sk-synthetic"):
            with self.subTest(url=url), self.assertRaisesRegex(Rejected, "private_url"):
                await self.policy.url(url)

    async def test_encoded_controls_and_malformed_inputs_rejected(self):
        for suffix in ("?q=a%0ab", "?q=a%250db", "#a%0ab", "/a%255cb", "/a%3Ftoken=x", "/%FF"):
            with self.subTest(suffix=suffix), self.assertRaises(Rejected):
                await self.policy.url("https://public.example.org" + suffix)

    async def test_ssrf_credentials_and_nonstandard_ports_rejected(self):
        for url in ("https://user:pass@public.example.org/a", "https://public.example.org:8443/a", "https://localhost/a", "https://host.internal/a", "http://2130706433/a", "http://0x7f000001/a", "http://127.0.0.1/a", "file:///etc/passwd"):
            with self.subTest(url=url), self.assertRaises(Rejected): await self.policy.url(url)

    async def test_all_dns_addresses_must_be_public(self):
        for addresses in (("1.1.1.1", "127.0.0.1"), ("10.0.0.1",), ("224.0.0.1",), ("192.0.2.1",), ()):
            async def resolver(host): return addresses
            policy = PublicPolicy(scope="general_public", resolver=resolver)
            with self.subTest(addresses=addresses), self.assertRaises(Rejected):
                await policy.url("https://public.example.org/a?id=1")

    async def test_query_field_count_is_bounded(self):
        with self.assertRaises(Rejected):
            await self.policy.url("https://public.example.org/a?" + "&".join("p%d=x" % i for i in range(33)))

    async def test_reviewed_urls_keep_previous_parameter_boundary(self):
        policy = PublicPolicy(fetch_hosts=["public.example.org"], resolver=public_dns)
        with self.assertRaises(Rejected): await policy.url("https://public.example.org/a?id=1")

    async def test_extra_model_arguments_never_authorize_or_forward_privacy_context(self):
        result = await self.policy.arguments(SEARCH, {"query": "Python coroutine", "protected_values": [], "source_text": "invented", "full_history": ["private"], "ori_question": "private", "public": True, "need_content": True}, source_text="那你查一下")
        self.assertEqual(result, {"query": "Python coroutine", "count": 5, "need_content": False})
        with self.assertRaisesRegex(Rejected, "private_query"):
            await self.policy.arguments(SEARCH, {"query": "Python SyntheticPrivateName", "protected_values": []}, source_text="Python", protected_values=("SyntheticPrivateName",))

    async def test_fetch_uses_local_protection_and_sanitizes_only_fragment(self):
        result = await self.policy.arguments(FETCH, {"url": "https://public.example.org/a?id=3#part", "ori_question": "private"})
        self.assertEqual(result, {"url": "https://public.example.org/a?id=3"})


if __name__ == "__main__": unittest.main()
