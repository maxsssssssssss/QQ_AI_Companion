"""Bounded exact arithmetic for the existing proactive Agent, without I/O.

The expression is a numeric calculation supplied by that Agent. A correct result
does not establish that the expression models a real-world claim, that its input
facts are true, or that the caller's unit label has been converted or validated.
"""
from __future__ import annotations

import ast
import asyncio
from decimal import Decimal, localcontext
from fractions import Fraction
import inspect
import json
import re
import time


NAME = 'autonomy_calculate'
MAX_LENGTH = 256
MAX_NODES = 64
MAX_DEPTH = 16
MAX_VALUE = 10**24
MAX_BITS = 256
_CHARS = re.compile(r'[0-9eE+*/(). \t-]+')
_NUMBER = re.compile(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?')
_UNIT = re.compile(r'[\w %°℃℉/·²³^.\-]{0,32}')


class ArithmeticRejected(ValueError):
    """Only a fixed safe reason is exposed; never echo invalid input."""


def _bounded(value):
    if (abs(value)>MAX_VALUE or abs(value.numerator).bit_length()>MAX_BITS
            or value.denominator.bit_length()>MAX_BITS):
        raise ArithmeticRejected('numeric_limit')
    return value


def calculate(expression,unit=''):
    """Return exact rational/decimal results, interpreting no variables or units."""
    if (not isinstance(expression,str) or not 0<len(expression)<=MAX_LENGTH
            or not _CHARS.fullmatch(expression)):
        raise ArithmeticRejected('invalid_expression')
    if not isinstance(unit,str) or not _UNIT.fullmatch(unit):
        raise ArithmeticRejected('invalid_unit_label')
    source=expression.strip()
    try:tree=ast.parse(source,mode='eval')
    except (SyntaxError,ValueError,RecursionError):raise ArithmeticRejected('invalid_expression') from None
    if sum(1 for _ in ast.walk(tree))>MAX_NODES:
        raise ArithmeticRejected('expression_complexity')

    def visit(node,depth=0):
        if depth>MAX_DEPTH:raise ArithmeticRejected('expression_depth')
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            literal=ast.get_source_segment(source,node)
            if (not literal or len(literal)>40 or not _NUMBER.fullmatch(literal)
                    or len(re.sub(r'\D','',literal))>30):
                raise ArithmeticRejected('invalid_number')
            # Fraction(Decimal(raw)) avoids ast.Constant's binary float loss.
            decimal=Decimal(literal)
            if not decimal.is_finite() or abs(decimal.adjusted())>100:
                raise ArithmeticRejected('numeric_limit')
            return _bounded(Fraction(decimal))
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=visit(node.operand,depth+1)
            return value if isinstance(node.op,ast.UAdd) else -value
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            left,right=visit(node.left,depth+1),visit(node.right,depth+1)
            if isinstance(node.op,ast.Add):return _bounded(left+right)
            if isinstance(node.op,ast.Sub):return _bounded(left-right)
            if isinstance(node.op,ast.Mult):return _bounded(left*right)
            if not right:raise ArithmeticRejected('division_by_zero')
            return _bounded(left/right)
        raise ArithmeticRejected('unsupported_operation')

    value=visit(tree.body)
    denominator=value.denominator
    for factor in (2,5):
        while denominator%factor==0:denominator//=factor
    terminating=denominator==1
    with localcontext() as ctx:
        ctx.prec=512 if terminating else 30
        decimal_result=format(Decimal(value.numerator)/Decimal(value.denominator),'f')
    if '.' in decimal_result:decimal_result=decimal_result.rstrip('0').rstrip('.')
    fraction=f'{value.numerator}/{value.denominator}'
    return {'ok':True,'expression':expression,'exact_result':decimal_result if terminating else fraction,
            'exact_fraction':fraction,'decimal_result':decimal_result,'decimal_is_exact':terminating,
            'unit':unit,'unit_semantics':'caller_label_only_no_conversion',
            'scope':'arithmetic_only_not_validation_of_real_world_claim'}


def _field(scope,name):
    return scope.get(name) if isinstance(scope,dict) else getattr(scope,name,None)


def _reason(value):
    return value if isinstance(value,str) and re.fullmatch('[a-z][a-z0-9_]{0,79}',value) else 'opportunity_interrupted'


def build_calculator_tool(state,scope,admit):
    """One scoped FunctionTool, durable four-attempt cap, private existing audit."""
    from astrbot.core.agent.tool import FunctionTool
    if not callable(admit):raise ValueError('calculator_admission_required')
    signature=inspect.signature(admit)
    try:
        signature.bind(scope)
        scoped_admit=True
    except TypeError:
        signature.bind()
        scoped_admit=False

    class CalculatorTool(FunctionTool):
        def __init__(self):
            super().__init__(name=NAME,description=(
                'Local exact arithmetic using numbers, parentheses and + - * /. '
                'Use to check numerical claims before sharing. No powers, names, code, '
                'unit conversion, web access or QQ send. At most four attempts per opportunity. '
                'The result proves only the supplied arithmetic, not the meaning or truth of '
                'the input facts. unit is an optional caller-provided label.'),
                parameters={'type':'object','properties':{
                    'expression':{'type':'string','minLength':1,'maxLength':MAX_LENGTH},
                    'unit':{'type':'string','maxLength':32,'default':''}},
                    'required':['expression'],'additionalProperties':False},handler=None)

        async def admission(self):
            evaluation=state.evaluation(_field(scope,'evaluation_id'))
            if not evaluation or any(evaluation.get(k)!=_field(scope,k)
                                     for k in ('opportunity_id','session')):
                return 'calculator_scope_mismatch'
            result=admit(scope) if scoped_admit else admit()
            if inspect.isawaitable(result):result=await result
            return 'allowed' if result is True or result=='allowed' else _reason(result)

        async def call(self,context,**kwargs):
            reason=await self.admission()
            if reason!='allowed':return json.dumps({'ok':False,'reason':reason})
            evaluation_id=_field(scope,'evaluation_id')
            reservation=state.reserve_calculation(evaluation_id)
            if not reservation['allowed']:
                return json.dumps({'ok':False,'reason':reservation['reason']})
            call_id=reservation['id'];start=time.monotonic();status='failed';metadata={'local_only':True}
            try:
                expression=kwargs.get('expression');unit=kwargs.get('unit','')
                request={'attempt_id':call_id,'tool':NAME,'phase':'request',
                    'expression':expression if isinstance(expression,str) and len(expression)<=MAX_LENGTH
                       and _CHARS.fullmatch(expression) else '[invalid]',
                    'unit':unit if isinstance(unit,str) and _UNIT.fullmatch(unit) else '[invalid]'}
                audit=state.write_audit(evaluation_id,'tool',request)
                if not audit.get('complete'):raise ArithmeticRejected('calculator_audit_unavailable')
                if set(kwargs)-{'expression','unit'}:raise ArithmeticRejected('invalid_arguments')
                result=calculate(expression,unit)
                reason=await self.admission()
                if reason!='allowed':
                    status='cancelled';result={'ok':False,'reason':reason}
                else:status='succeeded'
                audit=state.write_audit(evaluation_id,'tool',{'attempt_id':call_id,
                    'tool':NAME,'phase':'result','result':result})
                if not audit.get('complete'):
                    status='failed';raise ArithmeticRejected('calculator_audit_unavailable')
                return json.dumps(result,ensure_ascii=False)
            except ArithmeticRejected as exc:
                status='failed' if str(exc)=='calculator_audit_unavailable' else 'rejected'
                metadata['reason']=str(exc)
                return json.dumps({'ok':False,'reason':str(exc)})
            except asyncio.CancelledError:
                status='cancelled';metadata['reason']='cancelled';raise
            except Exception as exc:
                status='failed';metadata['error_type']=type(exc).__name__;raise
            finally:
                # Ledger failure propagates: never return an unrecorded result.
                if not state.finish_call(call_id,status,elapsed=time.monotonic()-start,metadata=metadata):
                    raise RuntimeError('calculator_terminal_ledger_required')

    return CalculatorTool()
