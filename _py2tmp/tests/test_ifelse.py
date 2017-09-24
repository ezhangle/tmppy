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

from py2tmp.testing import *

@assert_compilation_succeeds
def test_if_else_success():
    from tmppy import Type
    def f(x: bool):
        if x:
            return Type('int')
        else:
            return Type('float')
    assert f(True) == Type('int')

@assert_compilation_succeeds
def test_if_else_only_if_returns_success():
    from tmppy import Type
    def f(x: bool):
        if x:
            return Type('int')
        else:
            y = Type('float')
        return y
    assert f(True) == Type('int')

@assert_compilation_succeeds
def test_if_else_only_else_returns_success():
    from tmppy import Type
    def f(x: bool):
        if x:
            y = Type('int')
        else:
            return Type('float')
        return y
    assert f(True) == Type('int')

@assert_compilation_succeeds
def test_if_returns_success():
    from tmppy import Type
    def f(x: bool):
        if x:
            return Type('int')
        return Type('float')
    assert f(True) == Type('int')

@assert_compilation_succeeds
def test_if_else_neither_returns_success():
    from tmppy import Type
    def f(x: bool):
        if x:
            y = Type('int')
        else:
            y = Type('float')
        return y
    assert f(True) == Type('int')

@assert_compilation_succeeds
def test_if_else_assert_in_if_branch_never_taken_ok():
    def f(x: bool):
        if False:
            b = False
            assert b
        return True
    assert f(True) == True

@assert_compilation_succeeds
def test_if_else_assert_in_else_branch_never_taken_ok():
    def f(x: bool):
        if True:
            b = True
            assert b
        else:
            b = False
            assert b
        return True
    assert f(True) == True

@assert_compilation_succeeds
def test_if_else_assert_in_continuation_never_executed_ok():
    from tmppy import Type
    def f(x: bool):
        if True:
            return Type('int')
        b = False
        assert b
    assert f(True) == Type('int')

@assert_compilation_succeeds
def test_if_else_with_comparisons_success():
    from tmppy import Type
    def f(x: Type):
        if x == Type('int'):
            b = x == Type('int')
        else:
            return x == Type('float')
        return b == True
    assert f(Type('int')) == True

@assert_compilation_succeeds
def test_if_else_variable_forwarded_to_if_branch_success():
    def f(x: bool):
        if x:
            return x
        else:
            return False
    assert f(True) == True

@assert_compilation_succeeds
def test_if_else_variable_forwarded_to_else_branch_success():
    def f(x: bool):
        if x:
            return False
        else:
            return x
    assert f(False) == False

@assert_compilation_succeeds
def test_if_else_variable_forwarded_to_continuation_success():
    def f(x: bool):
        if False:
            return False
        return x
    assert f(True) == True

@assert_compilation_succeeds
def test_if_else_variable_forwarded_to_both_branches_success():
    def f(x: bool):
        if x:
            return x
        else:
            return x
    assert f(True) == True

@assert_conversion_fails
def test_if_else_condition_not_bool_error():
    from tmppy import Type
    def f(x: Type):
        if x:  # error: The condition in an if statement must have type bool, but was: Type
            return Type('int')
        else:
            return Type('float')

@assert_conversion_fails
def test_if_else_defining_same_var_with_different_types():
    from tmppy import Type
    def f(x: Type):
        if True:
            y = Type('int')  # note: A previous definition with type Type was here.
        else:
            y = True  # error: The variable y is defined with type bool here, but it was previously defined with type Type in another branch.
        return True

@assert_conversion_fails
def test_if_else_returning_different_types_error():
    from tmppy import Type
    def f(x: Type):
        if True:
            return Type('int')  # note: A previous return statement returning a Type was here.
        else:
            return True  # error: Found return statement with different return type: bool instead of Type.

@assert_compilation_succeeds
def test_if_else_if_branch_defining_additional_var_success():
    from tmppy import Type
    def f(x: bool):
        if x:
            y = Type('int')
            b = True
        else:
            y = Type('float')
        return y
    assert f(True) == Type('int')

@assert_compilation_succeeds
def test_if_else_else_branch_defining_additional_var_success():
    from tmppy import Type
    def f(x: bool):
        if x:
            y = Type('int')
        else:
            y = Type('float')
            b = True
        return y
    assert f(True) == Type('int')

# TODO: We could return a better error in this case, by keeping track of "sometimes defined" symbols in the symbol table.
@assert_conversion_fails
def test_if_else_defining_different_vars_possibly_undefined_var_used_in_continuation_error():
    from tmppy import Type
    def f(x: bool):
        if x:
            y = Type('int')
        else:
            y = Type('float')
            b = True
        return b  # error: Reference to undefined variable/function

@assert_conversion_fails
def test_if_else_if_branch_does_not_return_error():
    from tmppy import Type
    def f(x: bool):
        if x:
            y = Type('int') # error: Missing return statement.
        else:
            return True

@assert_conversion_fails
def test_if_else_else_branch_does_not_return_error():
    from tmppy import Type
    def f(x: bool):
        if x:
            return True
        else:
            y = Type('int') # error: Missing return statement.

@assert_conversion_fails
def test_if_else_missing_else_branch_no_return_after_error():
    def f(x: bool):
        if x:  # error: Missing return statement. You should add an else branch that returns, or a return after the if.
            return True

@assert_compilation_succeeds
def test_if_else_sequential_success():
    from tmppy import Type
    def f(x: bool):
        if x:
            return False
        else:
            y = Type('int')
        if y == Type('float'):
            return False
        else:
            return True
    assert f(False) == True
