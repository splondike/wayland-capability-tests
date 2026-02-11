import ast
import traceback


def print_assertion_values(assertion_exception):
    """
    Poor man's implementation of the pytest functionality that shows the
    values of variables used in a failing assertion
    """

    frame = None
    for frame, _ in traceback.walk_tb(assertion_exception.__traceback__):
        frame = frame

    code_line = None
    with open(frame.f_code.co_filename) as fh:
        for idx, line in enumerate(fh.readlines()):
            if (idx+1) == frame.f_lineno:
                code_line = line.strip()
                break

    result = ast.parse(code_line)

    assert_node = None
    for child in ast.iter_child_nodes(result):
        if isinstance(child, ast.Assert):
            assert_node = child
            break

    bits = []
    for child in ast.iter_child_nodes(next(ast.iter_child_nodes(assert_node))):
        if isinstance(child, ast.Name):
            value = frame.f_locals.get(child.id, frame.f_globals.get(child.id))
            bits.append(repr(value))
        elif isinstance(child, ast.Constant):
            bits.append(repr(child.value))
        else:
            operators = [
                (ast.Eq, "=="),
                (ast.NotEq, "!="),
                (ast.In, "in"),
                (ast.NotIn, "not in"),
                (ast.Gt, ">"),
                (ast.GtE, ">="),
                (ast.Lt, "<"),
                (ast.LtE, "<="),
            ]
            found = False
            for op, representation in operators:
                if isinstance(child, op):
                    bits.append(representation)
                    found = True
                    break

            if not found:
                # Otherwise just evaluate the expression to work out its value.
                # Bit unsafe maybe, but oh well. Lets us support function
                # calls, arithmetic, etc.
                code_obj = compile(
                    code_line[child.col_offset:child.end_col_offset],
                    frame.f_code.co_filename,
                    "eval"  # We're an expression
                )
                val = eval(
                    code_obj,
                    globals=frame.f_globals,
                    locals=frame.f_locals
                )
                bits.append(repr(val))

    print("    assert", " ".join(bits))
