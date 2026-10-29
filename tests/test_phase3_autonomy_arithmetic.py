"""Exact arithmetic and durable scoped FunctionTool; no network or model calls."""
import asyncio
from fractions import Fraction
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from integrations.phase3_runtime.autonomy_arithmetic import (
    ArithmeticRejected, calculate, build_calculator_tool, NAME)
from integrations.phase3_runtime.autonomy_state import AutonomyState

NATIVE = importlib.util.find_spec('astrbot') is not None
_native_temp=tempfile.TemporaryDirectory(prefix='arithmetic-native-')
if NATIVE:
    os.environ.setdefault('ASTRBOT_ROOT',_native_temp.name)
    from astrbot.core.agent.tool import FunctionTool, ToolSet


class ExactArithmeticTests(unittest.TestCase):
    def test_decimal_fraction_parentheses_precedence_and_unit(self):
        for expression,expected in [('0.1+0.2','0.3'),('(1+2)*3/2','4.5'),('-(-2)+(+3)','5'),
                                    ('2.5*60','150'),('1e-3 + .002','0.003'),('12/3-4','0')]:
            with self.subTest(expression=expression):
                result=calculate(expression,'秒');self.assertEqual(result['exact_result'],expected)
                self.assertTrue(result['decimal_is_exact']);self.assertEqual(result['unit'],'秒')
                self.assertEqual(result['expression'],expression)
                self.assertEqual(result['unit_semantics'],'caller_label_only_no_conversion')
                self.assertEqual(result['scope'],'arithmetic_only_not_validation_of_real_world_claim')

    def test_nonterminating_fraction_is_explicitly_approximate(self):
        result=calculate('1/3');self.assertEqual(result['exact_result'],'1/3')
        self.assertEqual(result['exact_fraction'],'1/3');self.assertFalse(result['decimal_is_exact'])
        self.assertEqual(result['decimal_result'],'0.'+'3'*30)

    def test_long_terminating_decimal_is_not_silently_rounded(self):
        denominator=str(2**75)
        expression=f'1-(1/{denominator})*(1/{denominator})*(1/{denominator})'
        result=calculate(expression)
        self.assertTrue(result['decimal_is_exact'])
        self.assertEqual(Fraction(result['decimal_result']),1-Fraction(1,2**225))

    def test_rejects_code_names_powers_floor_division_and_non_numbers(self):
        for expression in ('__import__("os").system("id")','open(1)','1**2','5//2','3%2',
                           'True','nan','inf','1_000','[1,2]','(1,2)','2(3)','1;2','1\n+2','0x10',True,None):
            with self.subTest(expression=expression),self.assertRaises(ArithmeticRejected):calculate(expression)

    def test_bounds_length_ast_depth_number_size_and_intermediates(self):
        for expression in ('1'*257,'+'.join(['1']*40),'-'*20+'1','1e999','1e-100','1e24*2','1/0'):
            with self.subTest(expression=expression),self.assertRaises(ArithmeticRejected):calculate(expression)
        self.assertEqual(calculate('1e24')['exact_result'],'1000000000000000000000000')

    def test_unit_is_bounded_label_only_and_does_not_execute_or_convert(self):
        result=calculate('1+1','小时');self.assertEqual(result['exact_result'],'2')
        for unit in ('secret\noutput','https://example.com','x'*33,False,None):
            with self.subTest(unit=unit),self.assertRaises(ArithmeticRejected):calculate('1',unit)


@unittest.skipUnless(NATIVE,'pinned AstrBot required for FunctionTool integration')
class ScopedCalculatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'existing.sqlite3';self.db=sqlite3.connect(self.path)
        self.addCleanup(lambda:self.db.close());self.now=1800000000.0
        self.state=AutonomyState(self.db,clock=lambda:self.now,audit_dir=self.root/'private-audit')
        self.scope={'session':'test:FriendMessage:10001','opportunity_id':'op','evaluation_id':'eval'}
        self.begin();self.admission='allowed';self.tool=self.build()

    def begin(self):
        return self.state.begin_opportunity('op',evaluation_id=self.scope['evaluation_id'],candidate_id='candidate',
            session=self.scope['session'],deadline=self.now+600,input_version='v1')
    def build(self):return build_calculator_tool(self.state,self.scope,lambda scope:self.admission)
    async def call(self,**kwargs):return json.loads(await self.tool.call(None,**kwargs))

    async def test_native_tool_schema_exact_output_and_private_audit(self):
        self.assertIsInstance(self.tool,FunctionTool);self.assertEqual(self.tool.name,NAME)
        toolset=ToolSet(tools=[self.tool]);self.assertIs(toolset.get_tool(NAME),self.tool)
        result=await self.call(expression='7*24*60',unit='分钟')
        self.assertEqual(result['exact_result'],'10080')
        self.assertEqual(self.state.summary()['calls'],{'calculator':{'succeeded':1}})
        rows=self.db.execute('SELECT path FROM autonomy_audits ORDER BY rowid').fetchall()
        request,result_audit=[json.loads((self.root/'private-audit'/row[0]).read_text()) for row in rows]
        self.assertEqual(request['expression'],'7*24*60')
        self.assertEqual(result_audit['result']['exact_result'],'10080')
        self.assertTrue(all((self.root/'private-audit'/row[0]).stat().st_mode&0o777==0o600 for row in rows))

    async def test_zero_argument_admission_callback_is_supported_without_retry(self):
        checks=[]
        def admit():checks.append(True);return 'allowed'
        self.tool=build_calculator_tool(self.state,self.scope,admit)
        self.assertEqual((await self.call(expression='1+1'))['exact_result'],'2')
        self.assertEqual(len(checks),2)

    async def test_four_attempts_persist_across_restart_and_evaluation(self):
        for expression in ('1+1','1/0','1**2','2+2'):await self.call(expression=expression)
        self.assertEqual((await self.call(expression='3'))['reason'],'opportunity_calculator_budget')
        self.db.close();self.db=sqlite3.connect(self.path)
        self.state=AutonomyState(self.db,clock=lambda:self.now,audit_dir=self.root/'private-audit')
        self.scope['evaluation_id']='after-restart';self.begin();self.tool=self.build()
        self.assertEqual((await self.call(expression='3'))['reason'],'opportunity_calculator_budget')
        self.assertEqual(self.state.summary()['calls']['calculator'],{'rejected':2,'succeeded':2})

    async def test_search_model_and_calculator_budgets_are_separate_and_ordinals_unique(self):
        for _ in range(4):self.assertTrue((await self.call(expression='1'))['ok'])
        for kind in ('search','fetch','search'):
            result=self.state.reserve_tool_attempt('eval',kind);self.assertTrue(result['allowed'])
            self.state.finish_call(result['id'],'failed')
        self.assertEqual(self.state.reserve_tool_attempt('eval','fetch')['reason'],'opportunity_tool_budget')
        self.assertTrue(self.state.reserve_model_request('eval')['allowed'])
        ordinals=[row[0] for row in self.db.execute('SELECT ordinal FROM autonomy_calls')]
        self.assertEqual(sorted(ordinals),list(range(1,9)))
        self.assertEqual(self.state.summary()['limits']['tool_attempts'],288)
        self.assertEqual(self.state.summary()['limits']['calculations_per_opportunity'],4)

    async def test_admission_denial_and_identity_mismatch_do_not_consume(self):
        for reason in ('quiet_hours','new_input','stopping'):
            self.admission=reason;self.assertEqual((await self.call(expression='1'))['reason'],reason)
        self.admission='allowed';self.scope['session']='test:FriendMessage:other'
        self.assertEqual((await self.call(expression='1'))['reason'],'calculator_scope_mismatch')
        self.assertEqual(self.state.summary()['calls'],{})

    async def test_malformed_arguments_charge_attempt_and_are_not_executed(self):
        result=await self.call(expression='__import__("os")',unexpected='do not execute')
        self.assertFalse(result['ok']);self.assertEqual(result['reason'],'invalid_arguments')
        self.assertEqual(self.state.summary()['calls'],{'calculator':{'rejected':1}})
        audit=self.db.execute('SELECT path FROM autonomy_audits').fetchone()[0]
        self.assertEqual(json.loads((self.root/'private-audit'/audit).read_text())['expression'],'[invalid]')

    async def test_new_input_after_calculation_records_cancelled_and_returns_no_result(self):
        checks=[0]
        async def admit(scope):
            checks[0]+=1;return 'allowed' if checks[0]==1 else 'new_input'
        self.tool=build_calculator_tool(self.state,self.scope,admit)
        result=await self.call(expression='1+1')
        self.assertEqual(result,{'ok':False,'reason':'new_input'})
        self.assertEqual(self.state.summary()['calls'],{'calculator':{'cancelled':1}})

    async def test_missing_audit_fails_closed_and_counts_attempt(self):
        with patch.object(self.state,'write_audit',return_value={'complete':False}):result=await self.call(expression='1+1')
        self.assertEqual(result,{'ok':False,'reason':'calculator_audit_unavailable'})
        self.assertEqual(self.state.summary()['calls'],{'calculator':{'failed':1}})

    async def test_ledger_reservation_failure_never_calculates(self):
        with patch.object(self.state,'reserve_calculation',side_effect=sqlite3.OperationalError('synthetic')):
            with patch('integrations.phase3_runtime.autonomy_arithmetic.calculate') as calc:
                with self.assertRaises(sqlite3.OperationalError):await self.call(expression='1')
                calc.assert_not_called()
        self.assertEqual(self.state.summary()['calls'],{})

    async def test_terminal_ledger_failure_never_returns_unrecorded_success(self):
        with patch.object(self.state,'finish_call',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'terminal_ledger'):await self.call(expression='1')

    async def test_task_cancellation_after_reservation_is_terminal(self):
        checks=[0]
        async def admit(scope):
            checks[0]+=1
            if checks[0]>1:raise asyncio.CancelledError()
            return 'allowed'
        self.tool=build_calculator_tool(self.state,self.scope,admit)
        with self.assertRaises(asyncio.CancelledError):await self.call(expression='1')
        self.assertEqual(self.state.summary()['calls'],{'calculator':{'cancelled':1}})

    async def test_expired_evaluation_is_not_a_new_calculation_chance(self):
        self.now+=600
        self.assertEqual((await self.call(expression='1'))['reason'],'exploration_deadline')
        self.assertEqual(self.state.summary()['calls'],{})


if __name__=='__main__':unittest.main()
