#!/usr/bin/env python3
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

import difflib
import inspect
import itertools
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile
import textwrap
import traceback
import unittest
from functools import wraps, lru_cache
from typing import Callable, Iterable, Any

import py2tmp_test_config as config
import pytest

from _py2tmp.compiler._compile import compile_source_code
from _py2tmp.compiler._link import compute_merged_header_for_linking
from _py2tmp.compiler.output_files import ObjectFileContent, merge_object_files
from _py2tmp.compiler.stages import CompilationError
from _py2tmp.ir0 import ir0
from _py2tmp.ir0_optimization import ConfigurationKnobs, DEFAULT_VERBOSE_SETTING

CHECK_TESTS_WERE_FULLY_OPTIMIZED = True

TEST_MODULE_NAME = 'test_module'
BUILTINS_OBJECT_FILE_PATH = './builtins.tmppyc'

class TestFailedException(Exception):
    pass

def bisect_with_predicate(last_known_good: int, first_known_bad: int, is_good: Callable[[int], bool]):
    assert last_known_good < first_known_bad
    while last_known_good + 1 < first_known_bad:
        middle = (last_known_good + first_known_bad + 1) // 2
        assert last_known_good < middle < first_known_bad
        if is_good(middle):
            last_known_good = middle
        else:
            first_known_bad = middle
        assert last_known_good < first_known_bad
    assert last_known_good + 1 == first_known_bad
    return first_known_bad

def run_test_with_optional_optimization(run: Callable[[bool], str], allow_reaching_max_optimization_loops=False):
    try:
        ConfigurationKnobs.verbose = DEFAULT_VERBOSE_SETTING

        e1 = None
        try:
            ConfigurationKnobs.max_num_optimization_steps = 0
            cpp_source = run(allow_toplevel_static_asserts_after_optimization=True)
        except (TestFailedException, AttributeError, AssertionError) as e:
            e1 = e
            e1_traceback = traceback.format_exc()

        e2 = None
        try:
            ConfigurationKnobs.max_num_optimization_steps = -1
            ConfigurationKnobs.optimization_step_counter = 0
            ConfigurationKnobs.reached_max_num_remaining_loops_counter = 0
            run(allow_toplevel_static_asserts_after_optimization=True)
        except (TestFailedException, AttributeError, AssertionError) as e:
            e2 = e

        if e1 and e2:
            raise e1

        if not e1 and not e2:
            if ConfigurationKnobs.reached_max_num_remaining_loops_counter != 0 and not allow_reaching_max_optimization_loops:
                ConfigurationKnobs.verbose = True
                optimized_cpp_source = run(allow_toplevel_static_asserts_after_optimization=True)
                raise TestFailedException('The test passed, but hit max_num_remaining_loops.\nOptimized C++ code:\n%s' % optimized_cpp_source)
            if CHECK_TESTS_WERE_FULLY_OPTIMIZED:
                run(allow_toplevel_static_asserts_after_optimization=False)
            return

        def predicate(max_num_optimization_steps):
            ConfigurationKnobs.max_num_optimization_steps = max_num_optimization_steps
            try:
                run(allow_toplevel_static_asserts_after_optimization=True)
                return True
            except (TestFailedException, AttributeError, AssertionError) as e:
                return False

        if e2:
            # Fails with ir0_optimization, succeeds without.
            # Bisect to find the issue.
            bisect_result = bisect_with_predicate(0, ConfigurationKnobs.optimization_step_counter, predicate)
            ConfigurationKnobs.verbose = True
            ConfigurationKnobs.max_num_optimization_steps = bisect_result
            try:
                run(allow_toplevel_static_asserts_after_optimization=True)
            except TestFailedException as e:
                [message] = e.args
                raise TestFailedException('Found test that fails after ir0_optimization.\n%s\nNon-optimized C++ code:\n%s' % (textwrap.dedent(message),
                                                                                                                              cpp_source))
            except (AttributeError, AssertionError):
                raise TestFailedException('Found test that fails after ir0_optimization.\n%s\nNon-optimized C++ code:\n%s' % (traceback.format_exc(),
                                                                                                                              cpp_source))

            raise Exception('This should never happen, the test failed before with the same max_num_optimization_steps.')
        else:
            # Fails without ir0_optimization, succeeds with.
            # Bisect to find the issue.
            bisect_result = bisect_with_predicate(0, ConfigurationKnobs.optimization_step_counter, lambda n: not predicate(n))
            ConfigurationKnobs.verbose = True
            ConfigurationKnobs.max_num_optimization_steps = bisect_result
            run(allow_toplevel_static_asserts_after_optimization=True)
            raise TestFailedException('Found test that succeeds only after ir0_optimization: ' + e1_traceback)
    except TestFailedException as e:
        [message] = e.args
        pytest.fail(message, pytrace=False)

def pretty_print_command(command):
    return ' '.join('"' + x + '"' for x in command)

def add_line_numbers(source_code):
    lines = source_code.splitlines()
    last_line_num_length = len(str(len(lines)))
    return '\n'.join('%%%sd: %%s' % last_line_num_length % (n + 1, line) for n, line in enumerate(lines))

class CommandFailedException(Exception):
    def __init__(self, command, stdout, stderr, error_code):
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.error_code = error_code

    def __str__(self):
        return textwrap.dedent('''\
        Ran command: {command}
        Exit code {error_code}
        
        Stdout:
        {stdout}

        Stderr:
        {stderr}
        ''').format(command=pretty_print_command(self.command), error_code=self.error_code, stdout=self.stdout, stderr=self.stderr)

def run_command(executable, args=[]):
    command = [executable] + args
    # print('Executing command:', pretty_print_command(command))
    try:
        p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        (stdout, stderr) = p.communicate()
    except Exception as e:
        raise Exception("While executing: %s" % command)
    if p.returncode != 0:
        raise CommandFailedException(command, stdout, stderr, p.returncode)
    # print('Execution successful.')
    # print('stdout:')
    # print(stdout)
    # print('')
    # print('stderr:')
    # print(stderr)
    # print('')
    return (stdout, stderr)

def run_compiled_executable(executable):
    run_command(executable)

class CompilationFailedException(Exception):
    def __init__(self, command, error_message):
        self.command = command
        self.error_message = error_message

    def __str__(self):
        return textwrap.dedent('''\
        Ran command: {command}
        Error message:
        {error_message}
        ''').format(command=pretty_print_command(self.command), error_message=self.error_message)

class PosixCompiler:
    def __init__(self):
        self.executable = config.CXX
        self.name = config.CXX_COMPILER_NAME

    def compile_discarding_output(self, source, include_dirs, args=[]):
        try:
            args = args + ['-c', source, '-o', os.path.devnull]
            self._compile(include_dirs, args=args)
        except CommandFailedException as e:
            raise CompilationFailedException(e.command, e.stderr)

    def compile_and_link(self, source, include_dirs, output_file_name, args=[]):
        self._compile(
            include_dirs,
            args = (
                [source]
                + config.ADDITIONAL_LINKER_FLAGS.split()
                + args
                + ['-o', output_file_name]
            ))

    def _compile(self, include_dirs, args):
        include_flags = ['-I%s' % include_dir for include_dir in include_dirs]
        args = (
            ['-W', '-Wall', '-g0', '-Werror', '-Wno-error=tautological-compare', '-std=c++11']
            + include_flags
            + config.ADDITIONAL_COMPILER_FLAGS.split()
            + args
        )
        run_command(self.executable, args)

class MsvcCompiler:
    def __init__(self):
        self.executable = config.CXX
        self.name = config.CXX_COMPILER_NAME

    def compile_discarding_output(self, source, include_dirs, args=[]):
        try:
            args = args + ['/c', source]
            self._compile(include_dirs, args = args)
        except CommandFailedException as e:
            # Note that we use stdout here, unlike above. MSVC reports compilation warnings and errors on stdout.
            raise CompilationFailedException(e.command, e.stdout)

    def compile_and_link(self, source, include_dirs, output_file_name, args=[]):
        self._compile(
            include_dirs,
            args = (
                [source]
                + config.ADDITIONAL_LINKER_FLAGS.split()
                + args
                + ['/Fe' + output_file_name]
            ))

    def _compile(self, include_dirs, args):
        include_flags = ['-I%s' % include_dir for include_dir in include_dirs]
        args = (
            ['/nologo', '/FS', '/W4', '/D_SCL_SECURE_NO_WARNINGS', '/WX']
            + include_flags
            + config.ADDITIONAL_COMPILER_FLAGS.split()
            + args
        )
        run_command(self.executable, args)

if config.CXX_COMPILER_NAME == 'MSVC':
    compiler = MsvcCompiler()
    py2tmp_error_message_extraction_regex = 'error C2338: (.*)'
else:
    compiler = PosixCompiler()
    py2tmp_error_message_extraction_regex = 'static.assert(.*)'

_assert_helper = unittest.TestCase()

def _create_temporary_file(file_content, file_name_suffix=''):
    file_descriptor, file_name = tempfile.mkstemp(text=True, suffix=file_name_suffix)
    file = os.fdopen(file_descriptor, mode='w')
    file.write(file_content)
    file.close()
    return file_name

def _cap_to_lines(s, n):
    lines = s.splitlines()
    if len(lines) <= n:
        return s
    else:
        return '\n'.join(lines[0:n] + ['...'])

def try_remove_temporary_file(filename):
    try:
        os.remove(filename)
    except:
        # When running tests on Windows using Appveyor, the remove command fails for temporary files sometimes.
        # This shouldn't cause the tests to fail, so we ignore the exception and go ahead.
        pass

def expect_cpp_code_compile_error_helper(check_error_fun: Callable[[CompilationFailedException, Iterable[str], Iterable[str], Iterable[str]], Any],
                                         tmppy_source: str, 
                                         object_file_content: ObjectFileContent,
                                         cxx_source: str):
    main_module = object_file_content.modules_by_name[TEST_MODULE_NAME]

    source_file_name = _create_temporary_file(cxx_source, file_name_suffix='.cpp')

    try:
        compiler.compile_discarding_output(
            source=source_file_name,
            include_dirs=[config.MPYL_INCLUDE_DIR],
            args=[])
        raise TestFailedException(textwrap.dedent('''\
            The test should have failed to compile, but it compiled successfully.
            
            TMPPy source:
            {tmppy_source}

            TMPPy IR1:
            {tmppy_ir1}
            
            C++ source code:
            {cxx_source}
            ''').format(tmppy_source = add_line_numbers(tmppy_source),
                        tmppy_ir1=str(main_module.ir1_module),
                        cxx_source = add_line_numbers(cxx_source)))
    except CompilationFailedException as e1:
        e = e1

    error_message = e.error_message
    error_message_lines = error_message.splitlines()
    # Different compilers output a different number of spaces when pretty-printing types.
    # When using libc++, sometimes std::foo identifiers are reported as std::__1::foo.
    normalized_error_message = error_message.replace(' ', '').replace('std::__1::', 'std::')
    normalized_error_message_lines = normalized_error_message.splitlines()
    error_message_head = _cap_to_lines(error_message, 40)

    check_error_fun(e, error_message_lines, error_message_head, normalized_error_message_lines)

    try_remove_temporary_file(source_file_name)

def expect_cpp_code_generic_compile_error(expected_error_regex: str, 
                                          tmppy_source: str, 
                                          object_file_contents: ObjectFileContent,
                                          cxx_source: str):
    """
    Tests that the given source produces the expected error during compilation.

    :param expected_error_regex: A regex used to match the _py2tmp error type,
           e.g. 'NoBindingFoundForAbstractClassError<ScalerImpl>'.
    :param cxx_source: The second part of the source code. This will be dedented.
    """
    
    main_module = object_file_contents.modules_by_name[TEST_MODULE_NAME]

    expected_error_regex = expected_error_regex.replace(' ', '')

    def check_error(e, error_message_lines, error_message_head, normalized_error_message_lines):
        for line in normalized_error_message_lines:
            if re.search(expected_error_regex, line):
                return
        raise TestFailedException(textwrap.dedent('''\
                Expected error {expected_error} but the compiler output did not contain that.
                Compiler command line: {compiler_command}
                Error message was:
                {error_message}

                TMPPy source:
                {tmppy_source}
                    
                TMPPy IR1:
                {tmppy_ir1}
                
                C++ source:
                {cxx_source}
                ''').format(expected_error = expected_error_regex,
                            compiler_command=e.command,
                            tmppy_source = add_line_numbers(tmppy_source),
                            tmppy_ir1 = str(main_module.ir1_module),
                            cxx_source = add_line_numbers(cxx_source),
                            error_message = error_message_head))

    expect_cpp_code_compile_error_helper(check_error, tmppy_source, object_file_contents, cxx_source)


def expect_cpp_code_compile_error(
        expected_py2tmp_error_regex: str,
        expected_py2tmp_error_desc_regex: str,
        tmppy_source: str,
        object_file_content: ObjectFileContent,
        cxx_source: str):
    """
    Tests that the given source produces the expected error during compilation.

    :param expected_py2tmp_error_regex: A regex used to match the _py2tmp error type,
           e.g. 'NoBindingFoundForAbstractClassError<ScalerImpl>'.
    :param expected_py2tmp_error_desc_regex: A regex used to match the _py2tmp error description,
           e.g. 'No explicit binding was found for C, and C is an abstract class'.
    :param cxx_source: The C++ source code. This will be dedented.
    """
    if '\n' in expected_py2tmp_error_regex:
        raise Exception('expected_py2tmp_error_regex should not contain newlines')
    if '\n' in expected_py2tmp_error_desc_regex:
        raise Exception('expected_py2tmp_error_desc_regex should not contain newlines')

    main_module = object_file_content.modules_by_name[TEST_MODULE_NAME]

    expected_py2tmp_error_regex = expected_py2tmp_error_regex.replace(' ', '')

    def check_error(e, error_message_lines, error_message_head, normalized_error_message_lines):
        for line_number, line in enumerate(normalized_error_message_lines):
            match = re.search('tmppy::impl::(.*Error<.*>)', line)
            if match:
                actual_py2tmp_error_line_number = line_number
                actual_py2tmp_error = match.groups()[0]
                if config.CXX_COMPILER_NAME == 'MSVC':
                    # MSVC errors are of the form:
                    #
                    # C:\Path\To\header\foo.h(59): note: see reference to class template instantiation 'tmppy::impl::MyError<X, Y>' being compiled
                    #         with
                    #         [
                    #              X=int,
                    #              Y=double
                    #         ]
                    #
                    # So we need to parse the following few lines and use them to replace the placeholder types in the tmppy error type.
                    try:
                        replacement_lines = []
                        if normalized_error_message_lines[line_number + 1].strip() == 'with':
                            for line in itertools.islice(normalized_error_message_lines, line_number + 3, None):
                                line = line.strip()
                                if line == ']':
                                    break
                                if line.endswith(','):
                                    line = line[:-1]
                                replacement_lines.append(line)
                        for replacement_line in replacement_lines:
                            match = re.search('([A-Za-z0-9_-]*)=(.*)', replacement_line)
                            if not match:
                                raise Exception('Failed to parse replacement line: %s' % replacement_line) from e
                            (type_variable, type_expression) = match.groups()
                            actual_py2tmp_error = re.sub(r'\b' + type_variable + r'\b', type_expression, actual_py2tmp_error)
                    except Exception:
                        raise Exception('Failed to parse MSVC template type arguments')
                break
        else:
            raise TestFailedException(textwrap.dedent('''\
                    Expected error {expected_error} but the compiler output did not contain user-facing _py2tmp errors.
                    Compiler command line: {compiler_command}
                    Error message was:
                    {error_message}

                    TMPPy source:
                    {tmppy_source}
                    
                    TMPPy IR1:
                    {tmppy_ir1}
                    
                    C++ source code:
                    {cxx_source}
                    ''').format(expected_error = expected_py2tmp_error_regex,
                                compiler_command = e.command,
                                tmppy_source = add_line_numbers(tmppy_source),
                                tmppy_ir1 = str(main_module.ir1_module),
                                cxx_source = add_line_numbers(cxx_source),
                                error_message = error_message_head))

        for line_number, line in enumerate(error_message_lines):
            match = re.search(py2tmp_error_message_extraction_regex, line)
            if match:
                actual_static_assert_error_line_number = line_number
                actual_static_assert_error = match.groups()[0]
                break
        else:
            raise TestFailedException(textwrap.dedent('''\
                    Expected error {expected_error} but the compiler output did not contain static_assert errors.
                    Compiler command line: {compiler_command}
                    Error message was:
                    {error_message}
                    
                    TMPPy source:
                    {tmppy_source}
                    
                    TMPPy IR1:
                    {tmppy_ir1}
                    
                    C++ source code:
                    {cxx_source}
                    ''').format(expected_error = expected_py2tmp_error_regex,
                                compiler_command=e.command,
                                tmppy_source = add_line_numbers(tmppy_source),
                                tmppy_ir1 = str(main_module.ir1_module),
                                cxx_source = add_line_numbers(cxx_source),
                                error_message = error_message_head))

        try:
            regex_search_result = re.search(expected_py2tmp_error_regex, actual_py2tmp_error)
        except Exception as e:
            raise Exception('re.search() failed for regex \'%s\'' % expected_py2tmp_error_regex) from e
        if not regex_search_result:
            raise TestFailedException(textwrap.dedent('''\
                    The compilation failed as expected, but with a different error type.
                    Expected _py2tmp error type:    {expected_py2tmp_error_regex}
                    Error type was:               {actual_py2tmp_error}
                    Expected static assert error: {expected_py2tmp_error_desc_regex}
                    Static assert was:            {actual_static_assert_error}
                    
                    Error message was:
                    {error_message}
                    
                    TMPPy source:
                    {tmppy_source}
                    
                    TMPPy IR1:
                    {tmppy_ir1}
                    
                    C++ source code:
                    {cxx_source}
                    '''.format(expected_py2tmp_error_regex = expected_py2tmp_error_regex,
                               actual_py2tmp_error = actual_py2tmp_error,
                               expected_py2tmp_error_desc_regex = expected_py2tmp_error_desc_regex,
                               actual_static_assert_error = actual_static_assert_error,
                               tmppy_source = add_line_numbers(tmppy_source),
                               tmppy_ir1 = str(main_module.ir1_module),
                               cxx_source = add_line_numbers(cxx_source),
                               error_message = error_message_head)))
        try:
            regex_search_result = re.search(expected_py2tmp_error_desc_regex, actual_static_assert_error)
        except Exception as e:
            raise Exception('re.search() failed for regex \'%s\'' % expected_py2tmp_error_desc_regex) from e
        if not regex_search_result:
            raise TestFailedException(textwrap.dedent('''\
                    The compilation failed as expected, but with a different error message.
                    Expected _py2tmp error type:    {expected_py2tmp_error_regex}
                    Error type was:               {actual_py2tmp_error}
                    Expected static assert error: {expected_py2tmp_error_desc_regex}
                    Static assert was:            {actual_static_assert_error}
                    
                    Error message:
                    {error_message}
                    
                    TMPPy source:
                    {tmppy_source}
                    
                    TMPPy IR1:
                    {tmppy_ir1}
                    
                    C++ source code:
                    {cxx_source}
                    '''.format(expected_py2tmp_error_regex = expected_py2tmp_error_regex,
                               actual_py2tmp_error = actual_py2tmp_error,
                               expected_py2tmp_error_desc_regex = expected_py2tmp_error_desc_regex,
                               actual_static_assert_error = actual_static_assert_error,
                               tmppy_source = add_line_numbers(tmppy_source),
                               tmppy_ir1 = str(main_module.ir1_module),
                               cxx_source = add_line_numbers(cxx_source),
                               error_message = error_message_head)))

        # 6 is just a constant that works for both g++ (<=6.0.0 at least) and clang++ (<=4.0.0 at least).
        # It might need to be changed.
        if actual_py2tmp_error_line_number > 6 or actual_static_assert_error_line_number > 6:
            raise TestFailedException(textwrap.dedent('''\
                    The compilation failed with the expected message, but the error message contained too many lines before the relevant ones.
                    The error type was reported on line {actual_py2tmp_error_line_number} of the message (should be <=6).
                    The static assert was reported on line {actual_static_assert_error_line_number} of the message (should be <=6).
                    Error message:
                    {error_message}

                    TMPPy source:
                    {tmppy_source}
                    
                    TMPPy IR1:
                    {tmppy_ir1}
                    
                    C++ source code:
                    {cxx_source}
                    '''.format(actual_py2tmp_error_line_number = actual_py2tmp_error_line_number,
                               actual_static_assert_error_line_number = actual_static_assert_error_line_number,
                               tmppy_source = add_line_numbers(tmppy_source),
                               tmppy_ir1 = str(main_module.ir1_module),
                               cxx_source = add_line_numbers(cxx_source),
                               error_message = error_message_head)))

        for line in error_message_lines[:max(actual_py2tmp_error_line_number, actual_static_assert_error_line_number)]:
            if re.search('tmppy::impl', line):
                raise TestFailedException(
                    'The compilation failed with the expected message, but the error message contained some metaprogramming types in the output (besides Error). Error message:\n%s' + error_message_head)

    expect_cpp_code_compile_error_helper(check_error, tmppy_source, object_file_content, cxx_source)

def expect_cpp_code_success(tmppy_source: str, 
                            object_file_content: ObjectFileContent,
                            cxx_source: str,
                            main_module_name=TEST_MODULE_NAME):
    """
    Tests that the given source compiles and runs successfully.

    :param cxx_source: The C++ source code. This will be dedented.
    """
    
    main_module = object_file_content.modules_by_name[main_module_name]

    if 'main(' not in cxx_source:
        cxx_source += textwrap.dedent('''
            int main() {
            }
            ''')

    source_file_name = _create_temporary_file(cxx_source, file_name_suffix='.cpp')
    executable_suffix = {'posix': '', 'nt': '.exe'}[os.name]
    output_file_name = _create_temporary_file('', executable_suffix)

    e = None

    try:
        compiler.compile_and_link(
            source=source_file_name,
            include_dirs=[config.MPYL_INCLUDE_DIR],
            output_file_name=output_file_name,
            args=[])
    except CommandFailedException as e1:
        e = e1

    if e:
        raise TestFailedException(textwrap.dedent('''\
                The generated C++ source did not compile.
                Compiler command line: {compiler_command}
                Error message was:
                {error_message}
                
                TMPPy source:
                {tmppy_source}
                
                TMPPy IR1:
                {tmppy_ir1}
                
                C++ source:
                {cxx_source}
                ''').format(compiler_command=e.command,
                            tmppy_source = add_line_numbers(tmppy_source),
                            tmppy_ir1 = str(main_module.ir1_module),
                            cxx_source = add_line_numbers(cxx_source),
                            error_message = _cap_to_lines(e.stderr, 40)))

    try:
        run_compiled_executable(output_file_name)
    except CommandFailedException as e1:
        e = e1

    if e:
        raise TestFailedException(textwrap.dedent('''\
                The generated C++ executable did not run successfully.
                stderr was:
                {error_message}

                TMPPy source:
                {tmppy_source}
                
                C++ source:
                {cxx_source}
                ''').format(tmppy_source = add_line_numbers(tmppy_source),
                            cxx_source = add_line_numbers(cxx_source),
                            error_message = _cap_to_lines(e.stderr, 40)))

    # Note that we don't delete the temporary files if the test failed. This is intentional, keeping them around helps debugging the failure.
    try_remove_temporary_file(source_file_name)
    try_remove_temporary_file(output_file_name)

def _get_function_body(f):
    # The body of some tests is a multiline string because they would otherwise cause the pytest test file to fail
    # parsing.
    if f.__doc__:
        return textwrap.dedent(f.__doc__)

    source_code, _ = inspect.getsourcelines(f)

    # Skip the annotation and the line where the function is defined.
    expected_line = 'def %s():\n' % f.__name__
    while source_code[0] != expected_line:
        source_code = source_code[1:]
    source_code = source_code[1:]

    return textwrap.dedent(''.join(source_code))

@lru_cache()
def get_builtins_object_file_content():
    with open(BUILTINS_OBJECT_FILE_PATH, 'rb') as file:
        object_file = pickle.loads(file.read())
    assert isinstance(object_file, ObjectFileContent)
    return object_file

def compile(python_source,
            module_name=TEST_MODULE_NAME,
            context_object_file_content: ObjectFileContent = ObjectFileContent({})):
    return compile_source_code(module_name=module_name,
                                       source_code=python_source,
                                       context_object_file_content=merge_object_files([get_builtins_object_file_content(),
                                                                                       context_object_file_content]),
                                       include_intermediate_irs_for_debugging=True)

def link(object_file_content: ObjectFileContent,
         main_module_name=TEST_MODULE_NAME):
    from _py2tmp.compiler._link import link
    return link(main_module_name=main_module_name,
                object_file_content=object_file_content)

def _convert_to_cpp_expecting_success(tmppy_source, allow_toplevel_static_asserts_after_optimization):
    try:
        object_file_content = compile(tmppy_source)
        e = None
    except CompilationError as e1:
        e = e1
    if e:
        raise TestFailedException(textwrap.dedent('''\
                The conversion from TMPPy to C++ failed.
                stderr was:
                {error_message}
                
                TMPPy source:
                {tmppy_source}
                ''').format(tmppy_source = add_line_numbers(tmppy_source),
                            error_message = e.args[0]))
    
    assert object_file_content

    if not allow_toplevel_static_asserts_after_optimization:
        def identifier_generator_fun():
            for i in itertools.count():
                yield 'TmppyInternal2_' + str(i)
        identifier_generator = identifier_generator_fun()
        merged_header_for_linking = compute_merged_header_for_linking(main_module_name=TEST_MODULE_NAME,
                                                                              object_file_content=object_file_content,
                                                                              identifier_generator=identifier_generator)

        for elem in merged_header_for_linking.toplevel_content:
            if isinstance(elem, ir0.StaticAssert):
                # Re-run the ir0_optimization in verbose mode so that we output more detail on the error.
                ConfigurationKnobs.reached_max_num_remaining_loops_counter = 0
                ConfigurationKnobs.verbose = True
                ConfigurationKnobs.max_num_optimization_steps = -1
                object_file_content = compile(tmppy_source)
                main_module = object_file_content.modules_by_name[TEST_MODULE_NAME]
                cpp_source = link(object_file_content)

                if ConfigurationKnobs.reached_max_num_remaining_loops_counter:
                    raise TestFailedException('Reached max_num_remaining_loops_counter.')

                raise TestFailedException(textwrap.dedent('''\
                        The conversion from TMPPy to C++ succeeded, but there were static_assert()s left after ir0_optimization.
                        
                        TMPPy source:
                        {tmppy_source}
                        
                        TMPPy IR1:
                        {tmppy_ir1}
                        
                        Generated C++ source:
                        {cpp_source}
                        ''').format(tmppy_source=add_line_numbers(tmppy_source),
                                    tmppy_ir1=str(main_module.ir1_module),
                                    cpp_source=cpp_source))

    cpp_source = link(object_file_content)
    return object_file_content, cpp_source

def assert_compilation_succeeds(extra_cpp_prelude='', always_allow_toplevel_static_asserts_after_optimization=False):
    def eval(f):
        @wraps(f)
        def wrapper():
            def run_test(allow_toplevel_static_asserts_after_optimization: bool):
                tmppy_source = _get_function_body(f)
                object_file_content, cpp_source = _convert_to_cpp_expecting_success(tmppy_source, allow_toplevel_static_asserts_after_optimization or always_allow_toplevel_static_asserts_after_optimization)
                expect_cpp_code_success(tmppy_source, object_file_content, extra_cpp_prelude + cpp_source)
                return cpp_source
            run_test_with_optional_optimization(run_test)
        return wrapper

    return eval

def assert_code_optimizes_to(expected_cpp_source: str, extra_cpp_prelude=''):
    def eval(f):
        @wraps(f)
        def wrapper():
            tmppy_source = _get_function_body(f)

            def run_test(allow_toplevel_static_asserts_after_optimization: bool):
                object_file_content, cpp_source = _convert_to_cpp_expecting_success(tmppy_source, allow_toplevel_static_asserts_after_optimization=True)
                expect_cpp_code_success(tmppy_source, object_file_content, extra_cpp_prelude + cpp_source)
                return cpp_source
            run_test_with_optional_optimization(run_test)

            try:
                ConfigurationKnobs.verbose = DEFAULT_VERBOSE_SETTING
                ConfigurationKnobs.max_num_optimization_steps = -1
                ConfigurationKnobs.reached_max_num_remaining_loops_counter = 0
                object_file_content, cpp_source = _convert_to_cpp_expecting_success(tmppy_source, allow_toplevel_static_asserts_after_optimization=True)
                main_module = object_file_content.modules_by_name[TEST_MODULE_NAME]

                cpp_source_lines = cpp_source.splitlines(True)
                assert cpp_source_lines[0:2] == [
                    '#include <tmppy/tmppy.h>\n',
                    '#include <type_traits>\n',
                ]
                cpp_source = ''.join(cpp_source_lines[2:])

                assert expected_cpp_source[0] == '\n'
                if cpp_source != expected_cpp_source[1:]:
                    raise TestFailedException(textwrap.dedent('''\
                            The generated code didn't match the expected code.
                            
                            TMPPy source:
                            {tmppy_source}
                            
                            TMPPy IR1:
                            {tmppy_ir1}
                            
                            Generated C++ source:
                            {cpp_source}
                            
                            Expected C++ source:
                            {expected_cpp_source}
                            
                            Diff:
                            {cpp_source_diff}
                            ''').format(tmppy_source=add_line_numbers(tmppy_source),
                                        tmppy_ir1=str(main_module.ir1_module),
                                        cpp_source=str(cpp_source),
                                        expected_cpp_source=str(expected_cpp_source[1:]),
                                        cpp_source_diff=''.join(difflib.unified_diff(expected_cpp_source[1:].splitlines(True),
                                                                                     cpp_source.splitlines(True),
                                                                                     fromfile='expected.h',
                                                                                     tofile='actual.h'))))

                if ConfigurationKnobs.reached_max_num_remaining_loops_counter != 0:
                    raise TestFailedException('The generated code was the expected one, but hit max_num_remaining_loops')

            except TestFailedException as e:
                [message] = e.args
                pytest.fail(message, pytrace=False)

        return wrapper
    return eval

# TODO: Check that the error is s reported on the desired line (moving the regex to a comment in the test).
def assert_compilation_fails_with_generic_error(expected_error_regex: str, allow_reaching_max_optimization_loops=False):
    def eval(f):
        @wraps(f)
        def wrapper():
            def run_test(allow_toplevel_static_asserts_after_optimization: bool):
                tmppy_source = _get_function_body(f)
                object_file_content, cpp_source = _convert_to_cpp_expecting_success(tmppy_source, allow_toplevel_static_asserts_after_optimization=True)
                expect_cpp_code_generic_compile_error(
                    expected_error_regex,
                    tmppy_source,
                    object_file_content,
                    cpp_source)
                return cpp_source
            run_test_with_optional_optimization(run_test, allow_reaching_max_optimization_loops)
        return wrapper
    return eval

# TODO: Check that the error is s reported on the desired line (moving the regex to a comment in the test).
def assert_compilation_fails_with_static_assert_error(expected_error_regex: str):
    def eval(f):
        @wraps(f)
        def wrapper():
            def run_test(allow_toplevel_static_asserts_after_optimization: bool):
                tmppy_source = _get_function_body(f)
                object_file_content, cpp_source = _convert_to_cpp_expecting_success(tmppy_source, allow_toplevel_static_asserts_after_optimization=True)
                expect_cpp_code_generic_compile_error(
                    r'(error: static assertion failed: |error: static_assert failed .|static_assert failed due to requirement.*)' + expected_error_regex,
                    tmppy_source,
                    object_file_content,
                    cpp_source)
                return cpp_source
            run_test_with_optional_optimization(run_test)
        return wrapper
    return eval

def _split_list(l, num_elems_in_chunk):
    args = [iter(l)] * num_elems_in_chunk
    return list(itertools.zip_longest(*args))

def _get_line_from_diagnostic(diagnostic):
    matches = re.match('<unknown>:([0-9]*):', diagnostic)
    return int(matches.group(1))

def check_compilation_error(e: CompilationError,
                            tmppy_source: str):
    actual_source_lines = []
    expected_error_regex = None
    expected_error_line = None
    expected_note_by_line = dict()
    for line_index, line in enumerate(tmppy_source.splitlines()):
        error_regex_marker = ' # error: '
        note_regex_marker = ' # note: '
        if error_regex_marker in line:
            if expected_error_regex:
                raise TestFailedException('Multiple expected errors in the same test are not supported')
            [line, expected_error_regex] = line.split(error_regex_marker)
            expected_error_line = line_index + 1
        elif note_regex_marker in line:
            [line, expected_note_regex] = line.split(note_regex_marker)
            expected_note_by_line[line_index + 1] = expected_note_regex
        actual_source_lines.append(line)

    if not expected_error_regex:
        raise TestFailedException(textwrap.dedent('''\
                assert_conversion_fails was used, but no expected error regex was found.
                
                TMPPy source:
                {tmppy_source}
                ''').format(tmppy_source = add_line_numbers(tmppy_source)))

    # py2tmp diagnostics take up 3 lines each, e.g.:
    # <unknown>:2:11: error: Empty lists are not currently supported.
    #   return []
    #          ^
    py2tmp_diagnostics = _split_list(e.args[0].splitlines(), num_elems_in_chunk=3)
    error_diagnostic = py2tmp_diagnostics[0]

    expected_error_regex = '<unknown>:[0-9]*:[0-9]*: error: ' + expected_error_regex
    if not re.match(expected_error_regex, error_diagnostic[0]):
        raise TestFailedException(textwrap.dedent('''\
                An exception was thrown, but it didn\'t match the expected error regex.
                Expected error regex: {expected_error_regex}
                Actual error:
                {actual_error}
                
                TMPPy source:
                {tmppy_source}
                ''').format(expected_error_regex = expected_error_regex,
                            actual_error = '\n'.join(error_diagnostic),
                            tmppy_source = add_line_numbers(tmppy_source)))

    matches = re.match('<unknown>:([0-9]*):', error_diagnostic[0])
    actual_error_line = int(matches.group(1))
    if expected_error_line != actual_error_line:
        raise TestFailedException(textwrap.dedent('''\
                An exception matching the expected regex was thrown, but the error mentioned the wrong line: {actual_error_line} was reported instead of {expected_error_line}
                Expected error regex: {expected_error_regex}
                Actual error:
                {actual_error}
                
                TMPPy source:
                {tmppy_source}
                ''').format(actual_error_line=actual_error_line,
                            expected_error_line=expected_error_line,
                            expected_error_regex = expected_error_regex,
                            actual_error = '\n'.join(error_diagnostic),
                            tmppy_source = add_line_numbers(tmppy_source)))

    actual_note_by_line = {_get_line_from_diagnostic(note[0]): note
                           for note in py2tmp_diagnostics[1:]}
    for expected_note_line, expected_note_regex in expected_note_by_line.items():
        actual_note = actual_note_by_line.get(expected_note_line)
        if not actual_note:
            raise Exception('Expected the note %s on line %s but no note was emitted mentioning this line. Emitted notes: %s' % (
                expected_note_regex, expected_note_line, json.dumps(actual_note_by_line, indent=4)))
        expected_note_regex = '<unknown>:[0-9]*:[0-9]*: note: ' + expected_note_regex
        if not re.match(expected_note_regex, actual_note[0]):
            raise TestFailedException(textwrap.dedent('''\
                    A note diagnostic was emitted, but it didn\'t match the expected note regex.
                    Expected note regex: {expected_note_regex}
                    Actual note:
                    {actual_note}
                    
                    TMPPy source:
                    {tmppy_source}
                    ''').format(expected_note_regex = expected_note_regex,
                                actual_note = '\n'.join(actual_note),
                                tmppy_source = add_line_numbers(tmppy_source)))

    for actual_note_line, actual_note in actual_note_by_line.items():
        expected_note = expected_note_by_line.get(actual_note_line)
        if not expected_note:
            raise TestFailedException(textwrap.dedent('''\
                    Unexpected note:
                    {actual_note}
                    
                    TMPPy source:
                    {tmppy_source}
                    ''').format(actual_note = '\n'.join(actual_note),
                                tmppy_source = add_line_numbers(tmppy_source)))

def assert_conversion_fails(f):
    @wraps(f)
    def wrapper():
        def run_test(allow_toplevel_static_asserts_after_optimization: bool):
            tmppy_source = _get_function_body(f)
            e = None
            try:
                object_file_content = compile(tmppy_source)
            except CompilationError as e1:
                e = e1

            if not e:
                main_module = object_file_content.modules_by_name[TEST_MODULE_NAME]
                raise TestFailedException(textwrap.dedent('''\
                        Expected an exception, but the _py2tmp conversion completed successfully.
                        TMPPy source:
                        {tmppy_source}
    
                        TMPPy IR1:
                        {tmppy_ir1}
                        ''').format(tmppy_source=add_line_numbers(tmppy_source),
                                    tmppy_ir1=str(main_module.ir1_module)))

            check_compilation_error(e, tmppy_source)

            return '(no C++ source)'
        run_test_with_optional_optimization(run_test)

    return wrapper

# Note: this is not the main function of this file, it's meant to be used as main function from test_*.py files.
def main(file):
    code = pytest.main(args = sys.argv + [os.path.realpath(file)])
    exit(code)
