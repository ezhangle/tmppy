#  Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import itertools
from typing import Iterable, List, Dict, Union, Tuple, Set

import pytest

from _py2tmp import ir0, unify_ir0
from _py2tmp.unify_ir0 import UnificationResultKind, UnificationResult
from _py2tmp.testing.utils import main

def identifier_generator_fun():
    for i in itertools.count():
        yield 'X_%s' % i

def unify(exprs: List[ir0.Expr],
          patterns: List[ir0.Expr],
          expr_variables: Set[str],
          pattern_variables: Set[str]) -> unify_ir0.UnificationResult:
    return unify_ir0.unify(exprs, patterns, expr_variables, pattern_variables, iter(identifier_generator_fun()))

def literal(value: Union[bool, int]):
    return ir0.Literal(value)

def type_literal(cpp_type: str):
    return ir0.AtomicTypeLiteral.for_nonlocal_type(cpp_type)

def local_type_literal(cpp_type: str):
    return ir0.AtomicTypeLiteral.for_local(cpp_type, type=ir0.TypeType())

@pytest.mark.parametrize('expr_generator', [
    lambda: ir0.Literal(1),
    lambda: ir0.AtomicTypeLiteral.for_nonlocal_type('int'),
    lambda: ir0.PointerTypeExpr(type_literal('int')),
    lambda: ir0.ReferenceTypeExpr(type_literal('int')),
    lambda: ir0.RvalueReferenceTypeExpr(type_literal('int')),
    lambda: ir0.ConstTypeExpr(type_literal('int')),
    lambda: ir0.ArrayTypeExpr(type_literal('int')),
    lambda: ir0.FunctionTypeExpr(type_literal('int'), []),
    lambda: ir0.FunctionTypeExpr(type_literal('int'), [type_literal('float')]),
    lambda: ir0.ComparisonExpr(literal(1), literal(2), op='=='),
    lambda: ir0.Int64BinaryOpExpr(literal(1), literal(2), op='+'),
    lambda: ir0.NotExpr(literal(True)),
    lambda: ir0.UnaryMinusExpr(literal(1)),
    lambda: ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                                arg_types=[],
                                                                                                is_metafunction_that_may_return_error=False),
                                      args=[],
                                      instantiation_might_trigger_static_asserts=False),
    lambda: ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                                arg_types=[ir0.TypeType()],
                                                                                                is_metafunction_that_may_return_error=False),
                                      args=[type_literal('int')],
                                      instantiation_might_trigger_static_asserts=False),
    lambda: ir0.ClassMemberAccess(class_type_expr=type_literal('MyClass'), member_name='value_type', member_type=ir0.TypeType()),
], ids = [
    'Literal',
    'AtomicTypeLiteral',
    'PointerTypeExpr',
    'ReferenceTypeExpr',
    'RvalueReferenceTypeExpr',
    'ConstTypeExpr',
    'ArrayTypeExpr',
    'FunctionTypeExpr (no args)',
    'FunctionTypeExpr (1 arg)',
    'ComparisonExpr',
    'Int64BinaryOpExpr',
    'NotExpr',
    'UnaryMinusExpr',
    'TemplateInstantiation (no args)',
    'TemplateInstantiation (1 arg)',
    'ClassMemberAccess',
])
def test_unify_ir0_trivial_term_equality(expr_generator):
    result = unify([expr_generator()], [expr_generator()], expr_variables=set(), pattern_variables=set())
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == []

@pytest.mark.parametrize('expr1,expr2', [
    (ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local('Ts', type=ir0.VariadicType())),
     ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local('Us', type=ir0.VariadicType()))),
])
def test_unify_ir0_term_equality_variadic_type_expansion(expr1, expr2):
    result = unify([expr1], [expr2], expr_variables={'Ts'}, pattern_variables={'Us'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local('Us', type=ir0.VariadicType())),
         [ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local('Ts', type=ir0.VariadicType()))]),
    ]

@pytest.mark.parametrize('expr1,expr2', [
    (ir0.Literal(1), ir0.Literal(2)),
    (ir0.AtomicTypeLiteral.for_local('int', type=ir0.TypeType()),
     ir0.AtomicTypeLiteral.for_local('float', type=ir0.TypeType())),
    (ir0.AtomicTypeLiteral.for_local('int', type=ir0.TypeType()),
     ir0.AtomicTypeLiteral.for_local('int', type=ir0.VariadicType())),
    (ir0.AtomicTypeLiteral.for_nonlocal_template('std::vector', arg_types=[], is_metafunction_that_may_return_error=False),
     ir0.AtomicTypeLiteral.for_nonlocal_template('std::list', arg_types=[], is_metafunction_that_may_return_error=False)),
    (ir0.AtomicTypeLiteral.for_nonlocal_template('std::vector', arg_types=[], is_metafunction_that_may_return_error=False),
     ir0.AtomicTypeLiteral.for_nonlocal_template('std::vector', arg_types=[ir0.TypeType()], is_metafunction_that_may_return_error=False)),
    (ir0.AtomicTypeLiteral.for_nonlocal_template('std::vector', arg_types=[], is_metafunction_that_may_return_error=False),
     ir0.AtomicTypeLiteral.for_nonlocal_template('std::vector', arg_types=[], is_metafunction_that_may_return_error=True)),
    (ir0.AtomicTypeLiteral.for_nonlocal_type('int'),
     ir0.AtomicTypeLiteral.for_nonlocal_type('float')),
    (ir0.ComparisonExpr(literal(1), literal(2), op='=='),
     ir0.ComparisonExpr(literal(1), literal(2), op='!=')),
    (ir0.Int64BinaryOpExpr(literal(1), literal(2), op='+'),
     ir0.Int64BinaryOpExpr(literal(1), literal(2), op='-')),
    (ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                         arg_types=[],
                                                                                         is_metafunction_that_may_return_error=False),
                               args=[],
                               instantiation_might_trigger_static_asserts=False),
     ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                          arg_types=[],
                                                                                          is_metafunction_that_may_return_error=False),
                               args=[],
                               instantiation_might_trigger_static_asserts=True)),
    (ir0.ClassMemberAccess(class_type_expr=type_literal('MyClass'), member_name='value_type', member_type=ir0.TypeType()),
     ir0.ClassMemberAccess(class_type_expr=type_literal('MyClass'), member_name='pointer_type', member_type=ir0.TypeType())),
    (ir0.ClassMemberAccess(class_type_expr=type_literal('MyClass'), member_name='value_type', member_type=ir0.TypeType()),
     ir0.ClassMemberAccess(class_type_expr=type_literal('MyClass'), member_name='value_type', member_type=ir0.VariadicType())),
], ids = [
    'Literal, different value',
    'AtomicTypeLiteral.for_local(), different cpp_type',
    'AtomicTypeLiteral.for_local(), different type',
    'AtomicTypeLiteral.for_nonlocal_template, different cpp_type',
    'AtomicTypeLiteral.for_nonlocal_template, different arg_types',
    'AtomicTypeLiteral.for_nonlocal_template, different is_metafunction_that_may_return_error',
    'AtomicTypeLiteral.for_nonlocal_type, different cpp_type',
    'ComparisonExpr, different op',
    'Int64BinaryOpExpr, different op',
    'TemplateInstantiation, different instantiation_might_trigger_static_asserts',
    'ClassMemberAccess, different member_name',
    'ClassMemberAccess, different member_type',

])
def test_unify_ir0_term_equality_fails_different_local_values(expr1, expr2):
    result = unify([expr1], [expr2], expr_variables=set(), pattern_variables=set())
    assert result.kind == UnificationResultKind.IMPOSSIBLE

@pytest.mark.parametrize('expr_variables,pattern_variables', [
    (set(), {'T'}),
    ({'T'}, {'T'}),
])
def test_unify_ir0_same_type_variable_name_considered_different_from_pattern_variable(expr_variables, pattern_variables):
    result = unify([local_type_literal('T')], [local_type_literal('T')], expr_variables, pattern_variables)
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (local_type_literal('T'), local_type_literal('T')),
    ]

@pytest.mark.parametrize('expr_variables,pattern_variables', [
    ({'T'}, set()),
])
def test_unify_ir0_same_type_variable_name_considered_different_from_pattern_local(expr_variables, pattern_variables):
    result = unify([local_type_literal('T')], [local_type_literal('T')], expr_variables, pattern_variables)
    assert result.kind == UnificationResultKind.POSSIBLE

@pytest.mark.parametrize('expr_variables,pattern_variables', [
    (set(), set()),
])
def test_unify_ir0_same_type_name_considered_different_from_pattern_local(expr_variables, pattern_variables):
    result = unify([local_type_literal('T')], [local_type_literal('T')], expr_variables, pattern_variables)
    assert result.kind == UnificationResultKind.IMPOSSIBLE

@pytest.mark.parametrize('expr1,expr2', [
    (ir0.PointerTypeExpr(type_literal('int')),
     ir0.PointerTypeExpr(type_literal('float'))),
    (ir0.ReferenceTypeExpr(type_literal('int')),
     ir0.ReferenceTypeExpr(type_literal('float'))),
    (ir0.RvalueReferenceTypeExpr(type_literal('int')),
     ir0.RvalueReferenceTypeExpr(type_literal('float'))),
    (ir0.ConstTypeExpr(type_literal('int')),
     ir0.ConstTypeExpr(type_literal('float'))),
    (ir0.ArrayTypeExpr(type_literal('int')),
     ir0.ArrayTypeExpr(type_literal('float'))),
    (ir0.FunctionTypeExpr(type_literal('int'), []),
     ir0.FunctionTypeExpr(type_literal('float'), [])),
    (ir0.FunctionTypeExpr(type_literal('int'), [type_literal('float')]),
     ir0.FunctionTypeExpr(type_literal('int'), [type_literal('double')])),
    (ir0.FunctionTypeExpr(type_literal('int'), []),
     ir0.FunctionTypeExpr(type_literal('int'), [type_literal('double')])),
    (ir0.ComparisonExpr(literal(1), literal(2), op='=='),
     ir0.ComparisonExpr(literal(3), literal(2), op='==')),
    (ir0.ComparisonExpr(literal(1), literal(2), op='=='),
     ir0.ComparisonExpr(literal(1), literal(3), op='==')),
    (ir0.Int64BinaryOpExpr(literal(1), literal(2), op='+'),
     ir0.Int64BinaryOpExpr(literal(3), literal(2), op='+')),
    (ir0.Int64BinaryOpExpr(literal(1), literal(2), op='+'),
     ir0.Int64BinaryOpExpr(literal(1), literal(3), op='+')),
    (ir0.NotExpr(literal(True)),
     ir0.NotExpr(literal(False))),
    (ir0.UnaryMinusExpr(literal(1)),
     ir0.UnaryMinusExpr(literal(2))),
    (ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local('Ts', type=ir0.VariadicType())),
     ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local('Us', type=ir0.VariadicType()))),
    (ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                         arg_types=[],
                                                                                         is_metafunction_that_may_return_error=False),
                               args=[],
                               instantiation_might_trigger_static_asserts=False),
     ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::list',
                                                                                         arg_types=[],
                                                                                         is_metafunction_that_may_return_error=False),
                               args=[],
                               instantiation_might_trigger_static_asserts=False)),
    (ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                         arg_types=[ir0.TypeType()],
                                                                                         is_metafunction_that_may_return_error=False),
                               args=[type_literal('int')],
                               instantiation_might_trigger_static_asserts=False),
     ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                         arg_types=[ir0.TypeType()],
                                                                                         is_metafunction_that_may_return_error=False),
                               args=[type_literal('float')],
                               instantiation_might_trigger_static_asserts=False)),
    (ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                         arg_types=[],
                                                                                         is_metafunction_that_may_return_error=False),
                               args=[],
                               instantiation_might_trigger_static_asserts=False),
     ir0.TemplateInstantiation(template_expr=ir0.AtomicTypeLiteral.for_nonlocal_template(cpp_type='std::vector',
                                                                                         arg_types=[],
                                                                                         is_metafunction_that_may_return_error=False),
                               args=[],
                               instantiation_might_trigger_static_asserts=True)),
    (ir0.ClassMemberAccess(class_type_expr=type_literal('MyClass'), member_name='value_type', member_type=ir0.TypeType()),
     ir0.ClassMemberAccess(class_type_expr=type_literal('OtherClass'), member_name='value_type', member_type=ir0.TypeType())),
], ids=[
    'PointerTypeExpr',
    'ReferenceTypeExpr',
    'RvalueReferenceTypeExpr',
    'ConstTypeExpr',
    'ArrayTypeExpr',
    'FunctionTypeExpr, different return_type_expr',
    'FunctionTypeExpr, different arg_exprs values',
    'FunctionTypeExpr, different arg_exprs length',
    'ComparisonExpr, different lhs',
    'ComparisonExpr, different rhs',
    'Int64BinaryOpExpr, different lhs',
    'Int64BinaryOpExpr, different rhs',
    'NotExpr',
    'UnaryMinusExpr',
    'VariadicTypeExpansion',
    'TemplateInstantiation, different template_expr',
    'TemplateInstantiation, different args',
    'TemplateInstantiation, different instantiation_might_trigger_static_asserts',
    'ClassMemberAccess',
])
def test_unify_ir0_term_equality_fails_different_subexpressions(expr1, expr2):
    result = unify([expr1], [expr2], expr_variables=set(), pattern_variables=set())
    assert result.kind == UnificationResultKind.IMPOSSIBLE

def test_unify_ir0_certain_nontrivial():
    result = unify([ir0.PointerTypeExpr(type_literal('int'))],
                   [ir0.PointerTypeExpr(local_type_literal('T'))],
                   expr_variables=set(), pattern_variables={'T'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (local_type_literal('T'), type_literal('int')),
    ]

def test_unify_ir0_certain_nontrivial_multiple_equalities():
    result = unify([ir0.PointerTypeExpr(type_literal('int')), ir0.PointerTypeExpr(type_literal('int'))],
                   [ir0.PointerTypeExpr(local_type_literal('T')), ir0.PointerTypeExpr(local_type_literal('T'))],
                   expr_variables=set(), pattern_variables={'T'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (local_type_literal('T'), type_literal('int')),
    ]

def test_unify_ir0_certain_nontrivial_with_local():
    result = unify([ir0.PointerTypeExpr(local_type_literal('X'))],
                   [ir0.PointerTypeExpr(local_type_literal('T'))],
                   expr_variables=set(), pattern_variables={'T'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (local_type_literal('T'), local_type_literal('X')),
    ]

def test_unify_ir0_certain_nontrivial_with_local_variable():
    result = unify([ir0.PointerTypeExpr(local_type_literal('X'))],
                   [ir0.PointerTypeExpr(local_type_literal('T'))],
                   expr_variables={'X'}, pattern_variables={'T'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (local_type_literal('T'), local_type_literal('X')),
    ]

def test_unify_ir0_certain_nontrivial_with_variadic_type_variable():
    result = unify([ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[type_literal('float'),
                                                                          type_literal('double'),
                                                                          local_type_literal('T'),
                                                                          type_literal('char'),
                                                                          type_literal('void')])],
                   [ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[type_literal('float'),
                                                                         type_literal('double'),
                                                                         ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                                                                                   type=ir0.VariadicType())),
                                                                         type_literal('void')])],
                   expr_variables={'T'}, pattern_variables={'Ts'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                   type=ir0.VariadicType())),
         [local_type_literal('T'),
          type_literal('char')]),
    ]

def test_unify_ir0_certain_nontrivial_with_variadic_type_variable_matches_empty_list():
    result = unify([ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[])],
                   [ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                                                                                   type=ir0.VariadicType()))])],
                   expr_variables=set(), pattern_variables={'Ts'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                   type=ir0.VariadicType())),
         []),
    ]

def test_unify_ir0_certain_nontrivial_with_variadic_type_variable_matches_full_nonempty_list():
    result = unify([ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[type_literal('float'),
                                                                         type_literal('double')])],
                   [ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                                                                                   type=ir0.VariadicType()))])],
                   expr_variables=set(), pattern_variables={'Ts'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                   type=ir0.VariadicType())),
         [type_literal('float'),
          type_literal('double')]),
    ]


def test_unify_ir0_certain_nontrivial_with_variadic_type_variable_matches_empty_list_suffix():
    result = unify([ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[type_literal('float')])],
                   [ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[type_literal('float'),
                                                                         ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                                                                                   type=ir0.VariadicType()))])],
                   expr_variables=set(), pattern_variables={'Ts'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                   type=ir0.VariadicType())),
         []),
    ]

def test_unify_ir0_certain_nontrivial_with_variadic_type_variable_does_not_match():
    result = unify([ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[])],
                   [ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[type_literal('float'),
                                                                         ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                                                                                   type=ir0.VariadicType()))])],
                   expr_variables=set(), pattern_variables={'Ts'})
    assert result.kind == UnificationResultKind.IMPOSSIBLE

def test_unify_ir0_variadic_type_variable_matches_multiple_variadics():
    result = unify([ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[type_literal('float'),
                                                                         type_literal('double'),
                                                                         ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                                                                                   type=ir0.VariadicType())),
                                                                         type_literal('char'),
                                                                         ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Us',
                                                                                                                                   type=ir0.VariadicType())),
                                                                         type_literal('void')])],
                   [ir0.FunctionTypeExpr(type_literal('int'), arg_exprs=[type_literal('float'),
                                                                         type_literal('double'),
                                                                         ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Vs',
                                                                                                                                   type=ir0.VariadicType())),
                                                                         type_literal('void')])],
                   expr_variables={'Ts', 'Us'}, pattern_variables={'Vs'})
    assert result.kind == UnificationResultKind.CERTAIN
    assert result.value_by_pattern_variable == [
        (ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Vs',
                                                                   type=ir0.VariadicType())),
         [ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Ts',
                                                                    type=ir0.VariadicType())),
          type_literal('char'),
          ir0.VariadicTypeExpansion(ir0.AtomicTypeLiteral.for_local(cpp_type='Us',
                                                                    type=ir0.VariadicType()))]),
    ]

if __name__== '__main__':
    main(__file__)