"""Evaluate numeric expressions over simulation samples."""
import ast
import cmath
from itertools import pairwise
import math
import operator
import re


def _power(base, exponent):
    if abs(exponent) > 100:
        raise ValueError("Exponent out of range")
    return base ** exponent


FUNCTIONS = {
    "abs": abs, "mag": abs, "real": lambda z: z.real, "imag": lambda z: z.imag,
    "phase": lambda z: math.degrees(cmath.phase(z)),
    "log": cmath.log, "ln": cmath.log, "loge": cmath.log, "log10": cmath.log10,
    "db": lambda z: 20 * math.log10(abs(z)), "sqrt": cmath.sqrt,
    "exp": cmath.exp, "sin": cmath.sin, "cos": cmath.cos, "pow": _power,
    "multiply": operator.mul,
}
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
             ast.Div: operator.truediv, ast.Pow: _power}
SIGNAL_CALL = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b([vi])\(\s*([\w.#-]+)\s*\)''', re.I)


def linear_square_integral(width, first, last):
    return width*(first*first+first*last+last*last)/3


def level_crossings(xs, ys, level, lower, upper):
    previous = None
    for (x0,y0),(x1,y1) in pairwise(zip(xs,ys)):
        if not all(math.isfinite(v) for v in (x0,x1,y0,y1)) or y0==y1:
            continue
        if min(y0,y1)<=level<=max(y0,y1):
            x = x0+(x1-x0)*(level-y0)/(y1-y0)
            if lower<=x<=upper and x!=previous:
                previous = x
                yield x


def evaluate(expression, plot):
    if not 0 < len(expression) <= 500:
        raise ValueError("Enter an expression of up to 500 characters")
    # SPICE node names such as "in" and "net-1" are valid signal names,
    # even though they are not valid Python identifiers.
    expression = SIGNAL_CALL.sub(
        lambda match: f"trace({f'{match[1]}({match[2]})'!r})" if match[1] else match[0],
        expression,
    )
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError("Invalid expression") from error
    names = {name.casefold(): index for index, name in enumerate(plot.names)}

    def signal(name):
        if name.casefold() not in names:
            raise ValueError(f"Unknown signal {name!r}")
        index = names[name.casefold()]
        return lambda row: row[index]

    def compile_node(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = complex(node.value)
            if math.isfinite(value.real):
                return lambda row: value
        if isinstance(node, ast.Name):
            return signal(node.id)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = compile_node(node.operand)
            sign = -1 if isinstance(node.op, ast.USub) else 1
            return lambda row: sign * operand(row)
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            left, right = compile_node(node.left), compile_node(node.right)
            operation = OPERATORS[type(node.op)]
            return lambda row: operation(left(row), right(row))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id.lower()
            if name in ("trace", "v", "i") and len(node.args) == 1:
                key = node.args[0]
                if isinstance(key, ast.Name) and name != "trace":
                    return signal(f"{name}({key.id})")
                if isinstance(key, ast.Constant) and type(key.value) in (str, int):
                    return signal(str(key.value) if name == "trace" else f"{name}({key.value})")
            if name in ("rms","samplerms","max","min","mean") and len(node.args)==1:
                operand = compile_node(node.args[0])
                points = []
                for row in plot.rows:
                    try:
                        z = complex(operand(row))
                        value = abs(z) if z.imag else z.real
                    except (ValueError,ArithmeticError):
                        value = math.nan
                    points.append((row[0].real,value))
                values = [value for _,value in points if math.isfinite(value)]
                if not values:
                    raise ValueError("No finite samples for "+name)
                if name in ("rms","samplerms"):
                    width = energy = 0.
                    if name=="rms":
                        for (x0,y0),(x1,y1) in pairwise(points):
                            if all(math.isfinite(v) for v in (x0,y0,x1,y1)):
                                step = abs(x1-x0)
                                width += step
                                energy += linear_square_integral(step,y0,y1)
                    result = math.sqrt(energy/width if width else sum(v*v for v in values)/len(values))
                elif name=="mean":
                    result = sum(values)/len(values)
                else:
                    result = (max if name=="max" else min)(values)
                return lambda row: result
            if name in FUNCTIONS and len(node.args) == (2 if name in ("pow", "multiply") else 1):
                function = FUNCTIONS[name]
                args = [compile_node(arg) for arg in node.args]
                return lambda row: function(*(arg(row) for arg in args))
        raise ValueError("Use signal names, numeric operators, and supported plot functions")

    operation = compile_node(tree.body)
    samples = []
    valid = False
    for row in plot.rows:
        try:
            value = complex(operation(row))
            if not math.isfinite(value.real) or not math.isfinite(value.imag):
                raise ValueError("Nonfinite result")
        except (ArithmeticError, ValueError):
            value = complex(math.nan, math.nan)
        else:
            valid = True
        samples.append(value)
    if not valid:
        raise ValueError("The expression has no finite samples")
    return samples


DESIGN_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*",re.ASCII)
DESIGN_OPERATORS = {ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,
             ast.Div:operator.truediv,ast.Pow:operator.pow}


def evaluate_design(expression,lookup,number):
    if not isinstance(expression,str) or not 0<len(expression.strip())<=160:
        raise ValueError("Enter an expression of up to 160 characters.")
    expression = expression.strip().removeprefix("{").removesuffix("}").strip()
    expression = re.sub(
        r"(?<![\w.])(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?(?:meg|[tgkmunpf])\b",
        lambda match:repr(number(match[0])),expression,flags=re.I)
    def visit(node):
        if isinstance(node,ast.Constant) and type(node.value) in (float,int):
            return float(node.value)
        if isinstance(node,ast.Name):
            return lookup(node.id)
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            return (1 if isinstance(node.op,ast.UAdd) else -1)*visit(node.operand)
        if isinstance(node,ast.BinOp) and type(node.op) in DESIGN_OPERATORS:
            a,b = visit(node.left),visit(node.right)
            if isinstance(node.op,ast.Pow) and abs(b)>64:
                raise ValueError("Exponent is outside the supported range.")
            return DESIGN_OPERATORS[type(node.op)](a,b)
        raise ValueError("Use names, SPICE numbers and arithmetic operators.")
    try:
        value = visit(ast.parse(expression,mode="eval").body)
        if not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError("Design expressions must produce finite real values.")
        return value
    except (SyntaxError,ArithmeticError) as error:
        raise ValueError(f"Invalid design expression: {expression}") from error


def resolve_design(expressions,number):
    if (not isinstance(expressions,dict) or len(expressions)>64
            or any(not isinstance(k,str) or not DESIGN_IDENTIFIER.fullmatch(k) for k in expressions)):
        raise ValueError("Use up to 64 design variables with identifier names.")
    resolved = {}
    active = set()
    def get(name):
        if name in resolved:
            return resolved[name]
        if name in active:
            raise ValueError(f"Cyclic design variable: {name}")
        if name not in expressions:
            raise ValueError(f"Unknown design variable: {name}")
        active.add(name)
        resolved[name] = evaluate_design(expressions[name],get,number)
        active.remove(name)
        return resolved[name]
    for name in expressions:
        get(name)
    return resolved


def design_scalar(value, variables, number):
    try:
        return number(value)
    except ValueError:
        return evaluate_design(value,lambda name: design_lookup(variables,name),number)


def design_lookup(variables,name):
    if name not in variables:
        raise ValueError(f"Unknown design variable: {name}")
    return variables[name]
