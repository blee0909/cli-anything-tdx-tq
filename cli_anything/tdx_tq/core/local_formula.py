"""Local vectorized TDX formula compiler, MyTT-style primitives, and built-in formula library.

Zero-eval deterministic tokenizer and AST evaluator supporting multi-statement TDX formulas,
recursive SMA, dynamic DMA, rolling statistics, technical indicators, and screening formulas.
"""
from __future__ import annotations

import math
import operator
import re
from typing import Any, Callable

import numpy as np
import pandas as pd

from cli_anything.tdx_tq.core.errors import TdxParamError

# Comparison operator dispatch table
COMP_OPS: dict[str, Callable[[Any, Any], Any]] = {
    '=': operator.eq,
    '==': operator.eq,
    '!=': operator.ne,
    '<>': operator.ne,
    '>': operator.gt,
    '<': operator.lt,
    '>=': operator.ge,
    '<=': operator.le,
}

# Reserved series and constant aliases that cannot be shadowed by parameters
RESERVED_SERIES_NAMES = {
    'C', 'CLOSE',
    'O', 'OPEN',
    'H', 'HIGH',
    'L', 'LOW',
    'V', 'VOL', 'VOLUME',
    'AMO', 'AMOUNT',
    'TRUE', 'FALSE',
}

SERIES_FIELD_MAP = {
    'C': 'close',
    'CLOSE': 'close',
    'O': 'open',
    'OPEN': 'open',
    'H': 'high',
    'HIGH': 'high',
    'L': 'low',
    'LOW': 'low',
    'V': 'volume',
    'VOL': 'volume',
    'VOLUME': 'volume',
    'AMO': 'amount',
    'AMOUNT': 'amount',
}


# =====================================================================
# 1. Tokenizer & AST Definitions (Zero-eval / Zero-exec)
# =====================================================================

class Token:
    __slots__ = ('type', 'value', 'pos')

    def __init__(self, token_type: str, value: Any, pos: int):
        self.type = token_type
        self.value = value
        self.pos = pos

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value!r}, pos={self.pos})"


def strip_tdx_comments(text: str) -> str:
    """Strip block comments `{ ... }` anywhere in source with nesting validation."""
    result: list[str] = []
    depth = 0
    for idx, ch in enumerate(text):
        if ch == '{':
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
            else:
                raise TdxParamError(f"公式语法错误：在位置 {idx} 遇到未匹配的闭合注释符号 '}}'")
        else:
            if depth == 0:
                result.append(ch)
    if depth > 0:
        raise TdxParamError("公式语法错误：存在未闭合的注释符号 '{'")
    return "".join(result)


def tokenize(source: str) -> list[Token]:
    """Tokenize TDX formula source into safe lexical tokens."""
    clean_text = strip_tdx_comments(source)
    tokens: list[Token] = []
    i = 0
    n = len(clean_text)

    while i < n:
        ch = clean_text[i]

        if ch.isspace():
            i += 1
            continue

        # Two-character operators
        if i + 1 < n:
            two = clean_text[i:i + 2]
            if two == ':=':
                tokens.append(Token('ASSIGN', ':=', i))
                i += 2
                continue
            if two == '==':
                tokens.append(Token('EQ', '==', i))
                i += 2
                continue
            if two in ('!=', '<>'):
                tokens.append(Token('NEQ', '!=', i))
                i += 2
                continue
            if two == '>=':
                tokens.append(Token('GE', '>=', i))
                i += 2
                continue
            if two == '<=':
                tokens.append(Token('LE', '<=', i))
                i += 2
                continue
            if two == '&&':
                tokens.append(Token('AND', '&&', i))
                i += 2
                continue
            if two == '||':
                tokens.append(Token('OR', '||', i))
                i += 2
                continue

        # Single-character punctuation / operators
        if ch == ':':
            tokens.append(Token('COLON', ':', i))
            i += 1
            continue
        if ch == ';':
            tokens.append(Token('SEMICOLON', ';', i))
            i += 1
            continue
        if ch == ',':
            tokens.append(Token('COMMA', ',', i))
            i += 1
            continue
        if ch == '(':
            tokens.append(Token('LPAREN', '(', i))
            i += 1
            continue
        if ch == ')':
            tokens.append(Token('RPAREN', ')', i))
            i += 1
            continue
        if ch == '+':
            tokens.append(Token('PLUS', '+', i))
            i += 1
            continue
        if ch == '-':
            tokens.append(Token('MINUS', '-', i))
            i += 1
            continue
        if ch == '*':
            tokens.append(Token('MUL', '*', i))
            i += 1
            continue
        if ch == '/':
            tokens.append(Token('DIV', '/', i))
            i += 1
            continue
        if ch == '=':
            tokens.append(Token('EQ', '=', i))
            i += 1
            continue
        if ch == '>':
            tokens.append(Token('GT', '>', i))
            i += 1
            continue
        if ch == '<':
            tokens.append(Token('LT', '<', i))
            i += 1
            continue
        if ch == '!':
            tokens.append(Token('NOT', '!', i))
            i += 1
            continue

        # Numbers (including .5 and 10.5)
        if ch.isdigit() or (ch == '.' and i + 1 < n and clean_text[i + 1].isdigit()):
            start_pos = i
            while i < n and (clean_text[i].isdigit() or clean_text[i] == '.'):
                i += 1
            # Scientific notation 1e-5
            if i < n and clean_text[i] in ('e', 'E'):
                i += 1
                if i < n and clean_text[i] in ('+', '-'):
                    i += 1
                while i < n and clean_text[i].isdigit():
                    i += 1
            num_str = clean_text[start_pos:i]
            try:
                val = float(num_str)
            except ValueError:
                raise TdxParamError(f"公式语法错误：无法解析数字 '{num_str}' (位置 {start_pos})")
            tokens.append(Token('NUMBER', val, start_pos))
            continue

        # Identifiers and keywords (NOT, AND, OR)
        if ch.isalpha() or ch == '_':
            start_pos = i
            while i < n and (clean_text[i].isalnum() or clean_text[i] == '_'):
                i += 1
            ident = clean_text[start_pos:i].upper()
            if ident == 'AND':
                tokens.append(Token('AND', 'AND', start_pos))
            elif ident == 'OR':
                tokens.append(Token('OR', 'OR', start_pos))
            elif ident == 'NOT':
                tokens.append(Token('NOT', 'NOT', start_pos))
            else:
                tokens.append(Token('IDENT', ident, start_pos))
            continue

        # Reject any other invalid characters (like '.', '$', '@', '[', ']', etc.)
        raise TdxParamError(f"公式语法错误：不支持的字符 '{ch}' (位置 {i})")

    tokens.append(Token('EOF', '', n))
    return tokens


# AST Nodes
class ASTNode:
    pass


class NumberNode(ASTNode):
    def __init__(self, value: float):
        self.value = value


class VarNode(ASTNode):
    def __init__(self, name: str):
        self.name = name.upper()


class UnaryOpNode(ASTNode):
    def __init__(self, op: str, expr: ASTNode):
        self.op = op
        self.expr = expr


class BinaryOpNode(ASTNode):
    def __init__(self, op: str, left: ASTNode, right: ASTNode):
        self.op = op
        self.left = left
        self.right = right


class CallNode(ASTNode):
    def __init__(self, func_name: str, args: list[ASTNode]):
        self.func_name = func_name.upper()
        self.args = args


class StatementNode:
    def __init__(self, name: str, expr: ASTNode, is_output: bool):
        self.name = name.upper()
        self.expr = expr
        self.is_output = is_output  # True if ':' or bare expression, False if ':='


class FormulaParser:
    """Recursive-descent parser for TDX expressions."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def check(self, token_type: str) -> bool:
        return self.peek().type == token_type

    def match(self, *token_types: str) -> bool:
        if self.peek().type in token_types:
            self.pos += 1
            return True
        return False

    def consume(self, token_type: str, err_msg: str) -> Token:
        if self.check(token_type):
            tok = self.peek()
            self.pos += 1
            return tok
        curr = self.peek()
        raise TdxParamError(f"公式语法错误：{err_msg} (在 '{curr.value}' 位置 {curr.pos})")

    def parse_expr(self) -> ASTNode:
        return self.parse_or()

    def parse_or(self) -> ASTNode:
        node = self.parse_and()
        while self.match('OR'):
            op = self.tokens[self.pos - 1].value
            right = self.parse_and()
            node = BinaryOpNode(op, node, right)
        return node

    def parse_and(self) -> ASTNode:
        node = self.parse_comp()
        while self.match('AND'):
            op = self.tokens[self.pos - 1].value
            right = self.parse_comp()
            node = BinaryOpNode(op, node, right)
        return node

    def parse_comp(self) -> ASTNode:
        node = self.parse_add()
        while self.match('EQ', 'NEQ', 'GT', 'LT', 'GE', 'LE'):
            op = self.tokens[self.pos - 1].value
            right = self.parse_add()
            node = BinaryOpNode(op, node, right)
        return node

    def parse_add(self) -> ASTNode:
        node = self.parse_mult()
        while self.match('PLUS', 'MINUS'):
            op = self.tokens[self.pos - 1].value
            right = self.parse_mult()
            node = BinaryOpNode(op, node, right)
        return node

    def parse_mult(self) -> ASTNode:
        node = self.parse_unary()
        while self.match('MUL', 'DIV'):
            op = self.tokens[self.pos - 1].value
            right = self.parse_unary()
            node = BinaryOpNode(op, node, right)
        return node

    def parse_unary(self) -> ASTNode:
        if self.match('PLUS', 'MINUS', 'NOT'):
            op = self.tokens[self.pos - 1].value
            expr = self.parse_unary()
            return UnaryOpNode(op, expr)
        return self.parse_primary()

    def parse_primary(self) -> ASTNode:
        if self.match('NUMBER'):
            return NumberNode(self.tokens[self.pos - 1].value)

        if self.match('LPAREN'):
            expr = self.parse_expr()
            self.consume('RPAREN', "缺少匹配的右括号 ')'")
            return expr

        if self.match('IDENT'):
            name = self.tokens[self.pos - 1].value
            if self.match('LPAREN'):
                args: list[ASTNode] = []
                if not self.check('RPAREN'):
                    while True:
                        args.append(self.parse_expr())
                        if not self.match('COMMA'):
                            break
                self.consume('RPAREN', f"函数调用 '{name}' 缺少右括号 ')'")
                return CallNode(name, args)
            return VarNode(name)

        curr = self.peek()
        raise TdxParamError(f"公式语法错误：无法解析的表达式项 '{curr.value}' (位置 {curr.pos})")


def parse_formula_statements(source: str, *, select_mode: bool = False) -> list[StatementNode]:
    """Parse entire TDX formula source into a list of StatementNode instances."""
    tokens = tokenize(source)

    # Split token stream into statements by top-level ';'
    statements_tokens: list[list[Token]] = []
    current: list[Token] = []
    paren_depth = 0

    for tok in tokens:
        if tok.type == 'EOF':
            break
        if tok.type == 'LPAREN':
            paren_depth += 1
            current.append(tok)
        elif tok.type == 'RPAREN':
            paren_depth = max(0, paren_depth - 1)
            current.append(tok)
        elif tok.type == 'SEMICOLON' and paren_depth == 0:
            if current:
                statements_tokens.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        statements_tokens.append(current)

    if not statements_tokens:
        raise TdxParamError("公式内容为空，没有有效的语句可执行")

    parsed_statements: list[StatementNode] = []
    bare_count = 0

    for stmt_toks in statements_tokens:
        if not stmt_toks:
            continue

        # Case 1: Hidden assignment `IDENT := EXPR`
        if len(stmt_toks) >= 2 and stmt_toks[0].type == 'IDENT' and stmt_toks[1].type == 'ASSIGN':
            var_name = stmt_toks[0].value
            expr_toks = stmt_toks[2:]
            if not expr_toks:
                raise TdxParamError(f"赋值语句 '{var_name} :=' 缺少表达式内容")
            expr_toks.append(Token('EOF', '', stmt_toks[-1].pos + 1))
            parser = FormulaParser(expr_toks)
            ast = parser.parse_expr()
            if not parser.check('EOF'):
                tok = parser.peek()
                raise TdxParamError(f"赋值语句 '{var_name}' 表达式后存在多余内容: '{tok.value}'")
            parsed_statements.append(StatementNode(name=var_name, expr=ast, is_output=False))
            continue

        # Case 2: Output statement `IDENT : EXPR [, modifiers...]`
        if len(stmt_toks) >= 2 and stmt_toks[0].type == 'IDENT' and stmt_toks[1].type == 'COLON':
            var_name = stmt_toks[0].value
            rest = stmt_toks[2:]
            if not rest:
                raise TdxParamError(f"输出语句 '{var_name} :' 缺少表达式内容")

            # Strip trailing drawing/color modifiers after top-level comma
            p_depth = 0
            cut_idx = len(rest)
            for idx, tok in enumerate(rest):
                if tok.type == 'LPAREN':
                    p_depth += 1
                elif tok.type == 'RPAREN':
                    p_depth = max(0, p_depth - 1)
                elif tok.type == 'COMMA' and p_depth == 0:
                    cut_idx = idx
                    break

            expr_toks = rest[:cut_idx]
            if not expr_toks:
                raise TdxParamError(f"输出语句 '{var_name}' 缺少有效表达式")
            expr_toks.append(Token('EOF', '', expr_toks[-1].pos + 1))
            parser = FormulaParser(expr_toks)
            ast = parser.parse_expr()
            if not parser.check('EOF'):
                tok = parser.peek()
                raise TdxParamError(f"输出语句 '{var_name}' 表达式后存在无法识别的符号: '{tok.value}'")
            parsed_statements.append(StatementNode(name=var_name, expr=ast, is_output=True))
            continue

        # Case 3: Bare expression (e.g. `CROSS(MA(C, 5), MA(C, 20))`)
        # Strip trailing modifiers after top-level comma if any
        p_depth = 0
        cut_idx = len(stmt_toks)
        for idx, tok in enumerate(stmt_toks):
            if tok.type == 'LPAREN':
                p_depth += 1
            elif tok.type == 'RPAREN':
                p_depth = max(0, p_depth - 1)
            elif tok.type == 'COMMA' and p_depth == 0:
                cut_idx = idx
                break

        expr_toks = stmt_toks[:cut_idx]
        if not expr_toks:
            raise TdxParamError("公式包含空的表达式语句")
        expr_toks.append(Token('EOF', '', expr_toks[-1].pos + 1))
        parser = FormulaParser(expr_toks)
        ast = parser.parse_expr()
        if not parser.check('EOF'):
            tok = parser.peek()
            raise TdxParamError(f"表达式后存在无法识别的符号: '{tok.value}'")

        bare_count += 1
        output_name = 'SELECT' if select_mode else ('RESULT' if bare_count == 1 else f'RESULT_{bare_count}')
        parsed_statements.append(StatementNode(name=output_name, expr=ast, is_output=True))

    return parsed_statements


# =====================================================================
# 2. Vectorized Math & Logic Helpers (Safe Division, Truthiness)
# =====================================================================

def _ensure_series(val: Any, length: int | None = None, index: pd.Index | None = None) -> pd.Series:
    """Ensure a value is a float pd.Series."""
    if isinstance(val, pd.Series):
        if not pd.api.types.is_float_dtype(val.dtype):
            return val.astype(float)
        return val
    if isinstance(val, (int, float, np.number)):
        fval = float(val)
        if index is not None:
            return pd.Series(fval, index=index, dtype=float)
        if length is not None:
            return pd.Series([fval] * length, dtype=float)
        return pd.Series([fval], dtype=float)
    if isinstance(val, (list, np.ndarray)):
        return pd.Series(val, index=index, dtype=float)
    raise TdxParamError(f"无法将数据类型 '{type(val).__name__}' 转为指标序列")


def safe_div(a: pd.Series | float, b: pd.Series | float) -> pd.Series | float:
    """Division safe against ZeroDivisionError, Inf, -Inf, returning NaN."""
    is_a_scalar = isinstance(a, (int, float, np.number))
    is_b_scalar = isinstance(b, (int, float, np.number))
    if is_a_scalar and is_b_scalar:
        fa, fb = float(a), float(b)
        if fb == 0.0 or not (math.isfinite(fb) and math.isfinite(fa)):
            return float('nan')
        res = fa / fb
        return res if math.isfinite(res) else float('nan')

    sa = _ensure_series(a) if not is_a_scalar else float(a)
    sb = _ensure_series(b) if not is_b_scalar else float(b)

    if isinstance(sb, pd.Series):
        denom = sb.replace(0.0, np.nan)
        res = sa / denom
    else:
        if sb == 0.0 or not math.isfinite(sb):
            return pd.Series(np.nan, index=sa.index, dtype=float)
        res = sa / sb

    return res.replace([np.inf, -np.inf], np.nan)


def safe_comp(op: str, a: pd.Series | float, b: pd.Series | float) -> pd.Series | float:
    """Safe comparison returning 1.0 (True), 0.0 (False), and NaN if either operand is NaN."""
    if op not in COMP_OPS:
        raise TdxParamError(f"未知比较运算符: {op}")
    fn = COMP_OPS[op]

    is_a_scalar = isinstance(a, (int, float, np.number))
    is_b_scalar = isinstance(b, (int, float, np.number))
    if is_a_scalar and is_b_scalar:
        fa, fb = float(a), float(b)
        if not (math.isfinite(fa) and math.isfinite(fb)):
            return float('nan')
        return 1.0 if fn(fa, fb) else 0.0

    sa = _ensure_series(a) if not is_a_scalar else float(a)
    sb = _ensure_series(b) if not is_b_scalar else float(b)

    if isinstance(sa, pd.Series) and isinstance(sb, pd.Series):
        mask_nan = sa.isna() | sb.isna()
    elif isinstance(sa, pd.Series):
        mask_nan = sa.isna() | np.isnan(sb)
    else:
        mask_nan = np.isnan(sa) | sb.isna()

    res = fn(sa, sb).astype(float)
    res[mask_nan] = np.nan
    return res


def safe_and(a: pd.Series | float, b: pd.Series | float) -> pd.Series | float:
    """Logical AND treating NaN as falsey (0.0)."""
    is_a_scalar = isinstance(a, (int, float, np.number))
    is_b_scalar = isinstance(b, (int, float, np.number))
    if is_a_scalar and is_b_scalar:
        fa, fb = float(a), float(b)
        return 1.0 if (math.isfinite(fa) and fa != 0.0 and math.isfinite(fb) and fb != 0.0) else 0.0

    target_idx = a.index if isinstance(a, pd.Series) else b.index
    sa = _ensure_series(a, index=target_idx)
    sb = _ensure_series(b, index=target_idx)
    return ((sa.fillna(0.0) != 0.0) & (sb.fillna(0.0) != 0.0)).astype(float)


def safe_or(a: pd.Series | float, b: pd.Series | float) -> pd.Series | float:
    """Logical OR treating NaN as falsey (0.0)."""
    is_a_scalar = isinstance(a, (int, float, np.number))
    is_b_scalar = isinstance(b, (int, float, np.number))
    if is_a_scalar and is_b_scalar:
        fa, fb = float(a), float(b)
        return 1.0 if ((math.isfinite(fa) and fa != 0.0) or (math.isfinite(fb) and fb != 0.0)) else 0.0

    target_idx = a.index if isinstance(a, pd.Series) else b.index
    sa = _ensure_series(a, index=target_idx)
    sb = _ensure_series(b, index=target_idx)
    return ((sa.fillna(0.0) != 0.0) | (sb.fillna(0.0) != 0.0)).astype(float)


def safe_not(a: pd.Series | float) -> pd.Series | float:
    """Logical NOT treating NaN as falsey (0.0)."""
    if isinstance(a, (int, float, np.number)):
        fa = float(a)
        ta = math.isfinite(fa) and fa != 0.0
        return 0.0 if ta else 1.0

    sa = _ensure_series(a)
    ta = sa.fillna(0.0) != 0.0
    return (~ta).astype(float)


# =====================================================================
# 3. Vectorized TDX Function Primitives (28+ Core Functions)
# =====================================================================

def tdx_ref(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int < 0:
        raise TdxParamError(f"REF函数的偏移周期 N 必须为非负整数，当前为: {n_int}")
    ser = _ensure_series(s)
    if n_int == 0:
        return ser.copy()
    return ser.shift(n_int)


def tdx_ma(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int <= 0:
        raise TdxParamError(f"MA函数的计算周期 N 必须大于0，当前为: {n_int}")
    ser = _ensure_series(s)
    return ser.rolling(n_int, min_periods=n_int).mean()


def tdx_ema(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int <= 0:
        raise TdxParamError(f"EMA函数的计算周期 N 必须大于0，当前为: {n_int}")
    ser = _ensure_series(s)
    return ser.ewm(span=n_int, adjust=False, min_periods=1).mean()


def tdx_sma(s: pd.Series | float, n: float, m: float = 1.0) -> pd.Series:
    n_val = float(n)
    m_val = float(m)
    if n_val <= 0 or m_val <= 0 or m_val > n_val:
        raise TdxParamError(f"SMA函数的参数必须满足 N > 0 且 0 < M <= N，当前 N={n_val}, M={m_val}")
    ser = _ensure_series(s)
    arr = ser.to_numpy(dtype=float)
    out = np.full_like(arr, np.nan)

    first_valid = -1
    for idx, v in enumerate(arr):
        if math.isfinite(v):
            first_valid = idx
            break
    if first_valid == -1:
        return pd.Series(out, index=ser.index, dtype=float)

    y = arr[first_valid]
    out[first_valid] = y
    decay = (n_val - m_val) / n_val
    weight = m_val / n_val

    for i in range(first_valid + 1, len(arr)):
        v = arr[i]
        if not math.isfinite(v):
            out[i] = np.nan
            y = np.nan
        else:
            if math.isfinite(y):
                y = weight * v + decay * y
            else:
                y = v
            out[i] = y
    return pd.Series(out, index=ser.index, dtype=float)


def tdx_wma(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int <= 0:
        raise TdxParamError(f"WMA函数的计算周期 N 必须大于0，当前为: {n_int}")
    ser = _ensure_series(s)
    weights = np.arange(1, n_int + 1, dtype=float)
    weights /= weights.sum()
    return ser.rolling(n_int, min_periods=n_int).apply(lambda w: np.dot(w, weights), raw=True)


def tdx_dma(s: pd.Series | float, a: pd.Series | float) -> pd.Series:
    ser = _ensure_series(s)
    arr_s = ser.to_numpy(dtype=float)
    if isinstance(a, (int, float, np.number)):
        arr_a = np.full_like(arr_s, float(a))
    else:
        ser_a = _ensure_series(a, index=ser.index)
        arr_a = ser_a.to_numpy(dtype=float)

    arr_a = np.clip(arr_a, 0.0, 1.0)
    out = np.full_like(arr_s, np.nan)

    first_valid = -1
    for idx, v in enumerate(arr_s):
        if math.isfinite(v):
            first_valid = idx
            break
    if first_valid == -1:
        return pd.Series(out, index=ser.index, dtype=float)

    y = arr_s[first_valid]
    out[first_valid] = y
    for i in range(first_valid + 1, len(arr_s)):
        x = arr_s[i]
        alpha = arr_a[i]
        if not (math.isfinite(x) and math.isfinite(alpha)):
            out[i] = np.nan
        else:
            if math.isfinite(y):
                y = alpha * x + (1.0 - alpha) * y
            else:
                y = x
            out[i] = y
    return pd.Series(out, index=ser.index, dtype=float)


def tdx_hhv(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int < 0:
        raise TdxParamError(f"HHV函数的周期 N 必须为非负整数，当前为: {n_int}")
    ser = _ensure_series(s)
    if n_int == 0:
        return ser.cummax()
    return ser.rolling(n_int, min_periods=1).max()


def tdx_llv(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int < 0:
        raise TdxParamError(f"LLV函数的周期 N 必须为非负整数，当前为: {n_int}")
    ser = _ensure_series(s)
    if n_int == 0:
        return ser.cummin()
    return ser.rolling(n_int, min_periods=1).min()


def tdx_hhvbars(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int < 0:
        raise TdxParamError(f"HHVBARS函数的周期 N 必须为非负整数，当前为: {n_int}")
    ser = _ensure_series(s)
    arr = ser.to_numpy(dtype=float)
    out = np.full_like(arr, np.nan)
    for i in range(len(arr)):
        start = 0 if n_int == 0 else max(0, i - n_int + 1)
        window = arr[start:i + 1]
        valid_mask = ~np.isnan(window)
        if not np.any(valid_mask):
            continue
        max_val = np.nanmax(window)
        indices = np.where(window == max_val)[0]
        out[i] = float(len(window) - 1 - indices[-1])
    return pd.Series(out, index=ser.index, dtype=float)


def tdx_llvbars(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int < 0:
        raise TdxParamError(f"LLVBARS函数的周期 N 必须为非负整数，当前为: {n_int}")
    ser = _ensure_series(s)
    arr = ser.to_numpy(dtype=float)
    out = np.full_like(arr, np.nan)
    for i in range(len(arr)):
        start = 0 if n_int == 0 else max(0, i - n_int + 1)
        window = arr[start:i + 1]
        valid_mask = ~np.isnan(window)
        if not np.any(valid_mask):
            continue
        min_val = np.nanmin(window)
        indices = np.where(window == min_val)[0]
        out[i] = float(len(window) - 1 - indices[-1])
    return pd.Series(out, index=ser.index, dtype=float)


def tdx_sum(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int < 0:
        raise TdxParamError(f"SUM函数的周期 N 必须为非负整数，当前为: {n_int}")
    ser = _ensure_series(s)
    if n_int == 0:
        return ser.cumsum()
    return ser.rolling(n_int, min_periods=n_int).sum()


def tdx_std(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int <= 0:
        raise TdxParamError(f"STD函数的周期 N 必须大于0，当前为: {n_int}")
    ser = _ensure_series(s)
    return ser.rolling(n_int, min_periods=n_int).std(ddof=1)


def tdx_stdp(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int <= 0:
        raise TdxParamError(f"STDP函数的周期 N 必须大于0，当前为: {n_int}")
    ser = _ensure_series(s)
    return ser.rolling(n_int, min_periods=n_int).std(ddof=0)


def tdx_avedev(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int <= 0:
        raise TdxParamError(f"AVEDEV函数的周期 N 必须大于0，当前为: {n_int}")
    ser = _ensure_series(s)
    return ser.rolling(n_int, min_periods=n_int).apply(lambda w: np.mean(np.abs(w - np.mean(w))), raw=True)


def tdx_cross(a: pd.Series | float, b: pd.Series | float) -> pd.Series:
    sa = _ensure_series(a)
    sb = _ensure_series(b)
    if len(sa) != len(sb):
        if len(sa) == 1:
            sa = pd.Series(sa.iloc[0], index=sb.index, dtype=float)
        elif len(sb) == 1:
            sb = pd.Series(sb.iloc[0], index=sa.index, dtype=float)
        else:
            raise TdxParamError("CROSS函数两个序列长度不一致")
    else:
        sb = pd.Series(sb.values, index=sa.index, dtype=float)

    prev_a = sa.shift(1)
    prev_b = sb.shift(1)
    valid = prev_a.notna() & prev_b.notna() & sa.notna() & sb.notna()
    cond = (prev_a <= prev_b) & (sa > sb)
    return (valid & cond).astype(float)


def tdx_count(cond: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int < 0:
        raise TdxParamError(f"COUNT函数的周期 N 必须为非负整数，当前为: {n_int}")
    ser = _ensure_series(cond)
    truth = (ser.fillna(0.0) != 0.0).astype(float)
    if n_int == 0:
        return truth.cumsum()
    return truth.rolling(n_int, min_periods=1).sum()


def tdx_every(cond: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int <= 0:
        raise TdxParamError(f"EVERY函数的周期 N 必须大于0，当前为: {n_int}")
    cnt = tdx_count(cond, n_int)
    return (cnt == n_int).astype(float)


def tdx_exist(cond: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int <= 0:
        raise TdxParamError(f"EXIST函数的周期 N 必须大于0，当前为: {n_int}")
    cnt = tdx_count(cond, n_int)
    return (cnt >= 1.0).astype(float)


def tdx_barslast(cond: pd.Series | float) -> pd.Series:
    ser = _ensure_series(cond)
    arr = (ser.fillna(0.0) != 0.0).to_numpy(dtype=bool)
    out = np.full(len(arr), np.nan)
    last_idx = -1
    for i, is_true in enumerate(arr):
        if is_true:
            last_idx = i
        if last_idx != -1:
            out[i] = float(i - last_idx)
    return pd.Series(out, index=ser.index, dtype=float)


def tdx_barslastcount(cond: pd.Series | float) -> pd.Series:
    ser = _ensure_series(cond)
    arr = (ser.fillna(0.0) != 0.0).to_numpy(dtype=bool)
    out = np.zeros(len(arr), dtype=float)
    c = 0.0
    for i, is_true in enumerate(arr):
        if is_true:
            c += 1.0
        else:
            c = 0.0
        out[i] = c
    return pd.Series(out, index=ser.index, dtype=float)


def tdx_if(cond: pd.Series | float, a: pd.Series | float, b: pd.Series | float) -> pd.Series:
    ser_cond = _ensure_series(cond)
    val_a = _ensure_series(a, index=ser_cond.index) if isinstance(a, pd.Series) else float(a)
    val_b = _ensure_series(b, index=ser_cond.index) if isinstance(b, pd.Series) else float(b)
    truth = (ser_cond.fillna(0.0) != 0.0)
    res = np.where(truth, val_a, val_b)
    return pd.Series(res, index=ser_cond.index, dtype=float)


def tdx_ifn(cond: pd.Series | float, a: pd.Series | float, b: pd.Series | float) -> pd.Series:
    return tdx_if(cond, b, a)


def tdx_max(a: pd.Series | float, b: pd.Series | float) -> pd.Series | float:
    if isinstance(a, (int, float, np.number)) and isinstance(b, (int, float, np.number)):
        return max(float(a), float(b))
    sa = _ensure_series(a)
    sb = _ensure_series(b, index=sa.index) if isinstance(b, pd.Series) else float(b)
    return np.maximum(sa, sb)


def tdx_min(a: pd.Series | float, b: pd.Series | float) -> pd.Series | float:
    if isinstance(a, (int, float, np.number)) and isinstance(b, (int, float, np.number)):
        return min(float(a), float(b))
    sa = _ensure_series(a)
    sb = _ensure_series(b, index=sa.index) if isinstance(b, pd.Series) else float(b)
    return np.minimum(sa, sb)


def tdx_abs(x: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)):
        return abs(float(x))
    return _ensure_series(x).abs()


def tdx_sqrt(x: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)):
        fx = float(x)
        return math.sqrt(fx) if fx >= 0 else float('nan')
    ser = _ensure_series(x)
    return pd.Series(np.sqrt(np.where(ser >= 0.0, ser, np.nan)), index=ser.index, dtype=float)


def tdx_ln(x: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)):
        fx = float(x)
        return math.log(fx) if fx > 0 else float('nan')
    ser = _ensure_series(x)
    return pd.Series(np.log(np.where(ser > 0.0, ser, np.nan)), index=ser.index, dtype=float)


def tdx_exp(x: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)):
        try:
            return math.exp(float(x))
        except OverflowError:
            return float('nan')
    ser = _ensure_series(x)
    return np.exp(ser)


def tdx_pow(x: pd.Series | float, y: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)) and isinstance(y, (int, float, np.number)):
        try:
            return float(math.pow(float(x), float(y)))
        except (ValueError, OverflowError):
            return float('nan')
    sx = _ensure_series(x)
    sy = _ensure_series(y, index=sx.index) if isinstance(y, pd.Series) else float(y)
    with np.errstate(invalid='ignore'):
        res = np.power(sx, sy)
    return res.replace([np.inf, -np.inf], np.nan)


def tdx_round(x: pd.Series | float, d: float = 2.0) -> pd.Series | float:
    d_int = int(d)
    if isinstance(x, (int, float, np.number)):
        return round(float(x), d_int)
    return _ensure_series(x).round(d_int)


def tdx_ceiling(x: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)):
        return math.ceil(float(x))
    ser = _ensure_series(x)
    return pd.Series(np.ceil(ser), index=ser.index, dtype=float)


def tdx_floor(x: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)):
        return math.floor(float(x))
    ser = _ensure_series(x)
    return pd.Series(np.floor(ser), index=ser.index, dtype=float)


def tdx_intpart(x: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)):
        return math.trunc(float(x))
    ser = _ensure_series(x)
    return pd.Series(np.trunc(ser), index=ser.index, dtype=float)


def tdx_sign(x: pd.Series | float) -> pd.Series | float:
    if isinstance(x, (int, float, np.number)):
        fx = float(x)
        return 1.0 if fx > 0 else (-1.0 if fx < 0 else 0.0)
    ser = _ensure_series(x)
    return pd.Series(np.sign(ser), index=ser.index, dtype=float)


def tdx_between(x: pd.Series | float, a: pd.Series | float, b: pd.Series | float) -> pd.Series | float:
    sx = _ensure_series(x)
    sa = _ensure_series(a, index=sx.index) if isinstance(a, pd.Series) else float(a)
    sb = _ensure_series(b, index=sx.index) if isinstance(b, pd.Series) else float(b)

    low = np.minimum(sa, sb)
    high = np.maximum(sa, sb)

    mask_nan = sx.isna()
    if isinstance(low, pd.Series):
        mask_nan = mask_nan | low.isna()
    elif np.isnan(low):
        mask_nan = pd.Series(True, index=sx.index)

    if isinstance(high, pd.Series):
        mask_nan = mask_nan | high.isna()
    elif np.isnan(high):
        mask_nan = pd.Series(True, index=sx.index)

    cond = (sx >= low) & (sx <= high)
    res = cond.astype(float)
    res[mask_nan] = np.nan
    return res


def tdx_slope(s: pd.Series | float, n: float) -> pd.Series:
    n_int = int(n)
    if n_int < 2:
        raise TdxParamError(f"SLOPE函数的计算周期 N 必须大于等于2，当前为: {n_int}")
    ser = _ensure_series(s)
    x = np.arange(n_int, dtype=float)
    x_mean = (n_int - 1.0) / 2.0
    x_dev = x - x_mean
    ss_xx = np.sum(x_dev ** 2)
    weights = x_dev / ss_xx
    return ser.rolling(n_int, min_periods=n_int).apply(lambda w: np.dot(w, weights), raw=True)


def tdx_barscount(s: pd.Series | float) -> pd.Series:
    ser = _ensure_series(s)
    return ser.notna().cumsum().astype(float)


# Registry of supported functions: (func, min_args, max_args)
TDX_FUNCTIONS: dict[str, tuple[Callable, int, int]] = {
    'REF': (tdx_ref, 2, 2),
    'MA': (tdx_ma, 2, 2),
    'EMA': (tdx_ema, 2, 2),
    'SMA': (tdx_sma, 2, 3),
    'WMA': (tdx_wma, 2, 2),
    'DMA': (tdx_dma, 2, 2),
    'HHV': (tdx_hhv, 2, 2),
    'LLV': (tdx_llv, 2, 2),
    'HHVBARS': (tdx_hhvbars, 2, 2),
    'LLVBARS': (tdx_llvbars, 2, 2),
    'SUM': (tdx_sum, 2, 2),
    'STD': (tdx_std, 2, 2),
    'STDP': (tdx_stdp, 2, 2),
    'AVEDEV': (tdx_avedev, 2, 2),
    'CROSS': (tdx_cross, 2, 2),
    'COUNT': (tdx_count, 2, 2),
    'EVERY': (tdx_every, 2, 2),
    'EXIST': (tdx_exist, 2, 2),
    'BARSLAST': (tdx_barslast, 1, 1),
    'BARSLASTCOUNT': (tdx_barslastcount, 1, 1),
    'IF': (tdx_if, 3, 3),
    'IFF': (tdx_if, 3, 3),
    'IFN': (tdx_ifn, 3, 3),
    'MAX': (tdx_max, 2, 2),
    'MIN': (tdx_min, 2, 2),
    'ABS': (tdx_abs, 1, 1),
    'SQRT': (tdx_sqrt, 1, 1),
    'LN': (tdx_ln, 1, 1),
    'LOG': (tdx_ln, 1, 1),
    'EXP': (tdx_exp, 1, 1),
    'POW': (tdx_pow, 2, 2),
    'ROUND': (tdx_round, 1, 2),
    'CEILING': (tdx_ceiling, 1, 1),
    'FLOOR': (tdx_floor, 1, 1),
    'INTPART': (tdx_intpart, 1, 1),
    'SIGN': (tdx_sign, 1, 1),
    'BETWEEN': (tdx_between, 3, 3),
    'SLOPE': (tdx_slope, 2, 2),
    'BARSCOUNT': (tdx_barscount, 1, 1),
}


# =====================================================================
# 4. AST Evaluator
# =====================================================================

def evaluate_ast(node: ASTNode, context: dict[str, Any]) -> pd.Series | float:
    """Evaluate an AST node within the execution context."""
    if isinstance(node, NumberNode):
        return node.value

    if isinstance(node, VarNode):
        var_name = node.name
        # 1. Local intermediate/output variables computed earlier in formula
        if var_name in context['locals']:
            return context['locals'][var_name]

        # 2. Formula parameters
        if var_name in context['params']:
            return context['params'][var_name]

        # 3. Built-in constants
        if var_name == 'TRUE':
            return 1.0
        if var_name == 'FALSE':
            return 0.0

        # 4. Reserved market series aliases
        if var_name in SERIES_FIELD_MAP:
            field = SERIES_FIELD_MAP[var_name]
            series_dict = context['series']
            if field not in series_dict or series_dict[field] is None:
                raise TdxParamError(f"公式引用了字段 {var_name}，但行情数据中缺少 {field} 字段")
            return series_dict[field]

        raise TdxParamError(f"公式引用了未定义的变量或参数: '{var_name}'")

    if isinstance(node, UnaryOpNode):
        val = evaluate_ast(node.expr, context)
        op = node.op
        if op == '+':
            return val
        if op == '-':
            if isinstance(val, (int, float, np.number)):
                return -float(val)
            return -_ensure_series(val)
        if op in ('NOT', '!'):
            return safe_not(val)
        raise TdxParamError(f"未知一元运算符: {op}")

    if isinstance(node, BinaryOpNode):
        op = node.op
        left = evaluate_ast(node.left, context)
        right = evaluate_ast(node.right, context)

        if op in ('+', '-', '*'):
            is_scalar_l = isinstance(left, (int, float, np.number))
            is_scalar_r = isinstance(right, (int, float, np.number))
            if is_scalar_l and is_scalar_r:
                fl, fr = float(left), float(right)
                res = fl + fr if op == '+' else (fl - fr if op == '-' else fl * fr)
                return res if math.isfinite(res) else float('nan')

            sl = _ensure_series(left) if not is_scalar_l else float(left)
            sr = _ensure_series(right) if not is_scalar_r else float(right)
            res = sl + sr if op == '+' else (sl - sr if op == '-' else sl * sr)
            return res.replace([np.inf, -np.inf], np.nan)

        if op == '/':
            return safe_div(left, right)

        if op in ('=', '==', '!=', '<>', '>', '<', '>=', '<='):
            return safe_comp(op, left, right)

        if op in ('AND', '&&'):
            return safe_and(left, right)

        if op in ('OR', '||'):
            return safe_or(left, right)

        raise TdxParamError(f"未知二元运算符: {op}")

    if isinstance(node, CallNode):
        func_name = node.func_name
        if func_name not in TDX_FUNCTIONS:
            raise TdxParamError(f"公式调用了未支持的函数: '{func_name}'")

        func_entry, min_args, max_args = TDX_FUNCTIONS[func_name]
        arg_count = len(node.args)
        if not (min_args <= arg_count <= max_args):
            expected = str(min_args) if min_args == max_args else f"{min_args} 到 {max_args}"
            raise TdxParamError(f"函数 {func_name} 需要 {expected} 个参数，但得到了 {arg_count} 个")

        evaluated_args = [evaluate_ast(arg, context) for arg in node.args]
        return func_entry(*evaluated_args)

    raise TdxParamError(f"未知 AST 节点类型: {type(node).__name__}")


# =====================================================================
# 5. Built-in Authentic TDX Formula Library
# =====================================================================

BUILTIN_FORMULA_LIST: list[dict[str, Any]] = [
    # -------------------------------------------------------------
    # formula_type == 0: Technical Indicators (zb)
    # -------------------------------------------------------------
    {
        'acCode': 'MA',
        'name': 'MA 移动平均线',
        'description': '经典移动平均线系统',
        'formula_type': 0,
        'params': [('N1', 5.0), ('N2', 10.0), ('N3', 20.0), ('N4', 60.0)],
        'source': (
            "MA1: MA(CLOSE, N1);\n"
            "MA2: MA(CLOSE, N2);\n"
            "MA3: MA(CLOSE, N3);\n"
            "MA4: MA(CLOSE, N4);"
        ),
    },
    {
        'acCode': 'EMA',
        'name': 'EMA 指数平滑移动平均线',
        'description': '指数平滑移动平均线系统',
        'formula_type': 0,
        'params': [('N1', 5.0), ('N2', 10.0), ('N3', 20.0), ('N4', 60.0)],
        'source': (
            "EMA1: EMA(CLOSE, N1);\n"
            "EMA2: EMA(CLOSE, N2);\n"
            "EMA3: EMA(CLOSE, N3);\n"
            "EMA4: EMA(CLOSE, N4);"
        ),
    },
    {
        'acCode': 'MACD',
        'name': 'MACD 指数平滑异同移动平均线',
        'description': '经典平滑异同平均线指标',
        'formula_type': 0,
        'params': [('SHORT', 12.0), ('LONG', 26.0), ('MID', 9.0)],
        'source': (
            "DIF: EMA(CLOSE, SHORT) - EMA(CLOSE, LONG);\n"
            "DEA: EMA(DIF, MID);\n"
            "MACD: (DIF - DEA) * 2, COLORSTICK;"
        ),
    },
    {
        'acCode': 'KDJ',
        'name': 'KDJ 随机指标',
        'description': '经典随机摆动指标',
        'formula_type': 0,
        'params': [('N', 9.0), ('M1', 3.0), ('M2', 3.0)],
        'source': (
            "RSV: (CLOSE - LLV(LOW, N)) / (HHV(HIGH, N) - LLV(LOW, N)) * 100;\n"
            "K: SMA(RSV, M1, 1);\n"
            "D: SMA(K, M2, 1);\n"
            "J: 3 * K - 2 * D;"
        ),
    },
    {
        'acCode': 'RSI',
        'name': 'RSI 相对强弱指标',
        'description': '相对强弱强弱度分析指标',
        'formula_type': 0,
        'params': [('N1', 6.0), ('N2', 12.0), ('N3', 24.0)],
        'source': (
            "LC := REF(CLOSE, 1);\n"
            "RSI1: SMA(MAX(CLOSE - LC, 0), N1, 1) / SMA(ABS(CLOSE - LC), N1, 1) * 100;\n"
            "RSI2: SMA(MAX(CLOSE - LC, 0), N2, 1) / SMA(ABS(CLOSE - LC), N2, 1) * 100;\n"
            "RSI3: SMA(MAX(CLOSE - LC, 0), N3, 1) / SMA(ABS(CLOSE - LC), N3, 1) * 100;"
        ),
    },
    {
        'acCode': 'BOLL',
        'name': 'BOLL 布林带',
        'description': '布林线波动通道指标',
        'formula_type': 0,
        'params': [('N', 20.0), ('P', 2.0)],
        'source': (
            "BOLL: MA(CLOSE, N);\n"
            "UB: BOLL + P * STD(CLOSE, N);\n"
            "LB: BOLL - P * STD(CLOSE, N);"
        ),
    },
    {
        'acCode': 'WR',
        'name': 'WR 威廉指标',
        'description': '威廉超买超卖摆动指标',
        'formula_type': 0,
        'params': [('N', 10.0), ('N1', 6.0)],
        'source': (
            "WR1: 100 * (HHV(HIGH, N) - CLOSE) / (HHV(HIGH, N) - LLV(LOW, N));\n"
            "WR2: 100 * (HHV(HIGH, N1) - CLOSE) / (HHV(HIGH, N1) - LLV(LOW, N1));"
        ),
    },
    {
        'acCode': 'BIAS',
        'name': 'BIAS 乖离率',
        'description': '乖离率超买超卖指标',
        'formula_type': 0,
        'params': [('L1', 6.0), ('L2', 12.0), ('L3', 24.0)],
        'source': (
            "BIAS1: (CLOSE - MA(CLOSE, L1)) / MA(CLOSE, L1) * 100;\n"
            "BIAS2: (CLOSE - MA(CLOSE, L2)) / MA(CLOSE, L2) * 100;\n"
            "BIAS3: (CLOSE - MA(CLOSE, L3)) / MA(CLOSE, L3) * 100;"
        ),
    },
    {
        'acCode': 'CCI',
        'name': 'CCI 顺势指标',
        'description': '顺势交易与常态区间指标',
        'formula_type': 0,
        'params': [('N', 14.0)],
        'source': (
            "TYP := (HIGH + LOW + CLOSE) / 3;\n"
            "CCI: (TYP - MA(TYP, N)) / (0.015 * AVEDEV(TYP, N));"
        ),
    },
    {
        'acCode': 'ATR',
        'name': 'ATR 真实波幅',
        'description': '真实波动幅度均值',
        'formula_type': 0,
        'params': [('N', 14.0)],
        'source': (
            "TR := MAX(MAX((HIGH - LOW), ABS(REF(CLOSE, 1) - HIGH)), ABS(REF(CLOSE, 1) - LOW));\n"
            "ATR: MA(TR, N);"
        ),
    },
    {
        'acCode': 'OBV',
        'name': 'OBV 能量潮',
        'description': '累积成交量能量潮指标',
        'formula_type': 0,
        'params': [],
        'source': "OBV: SUM(IF(CLOSE > REF(CLOSE, 1), VOL, IF(CLOSE < REF(CLOSE, 1), -VOL, 0)), 0);",
    },
    {
        'acCode': 'BBI',
        'name': 'BBI 多空指标',
        'description': '多空综合均线指标',
        'formula_type': 0,
        'params': [('M1', 3.0), ('M2', 6.0), ('M3', 12.0), ('M4', 24.0)],
        'source': "BBI: (MA(CLOSE, M1) + MA(CLOSE, M2) + MA(CLOSE, M3) + MA(CLOSE, M4)) / 4;",
    },
    {
        'acCode': 'PSY',
        'name': 'PSY 心理线',
        'description': '投资者心理买卖倾向指标',
        'formula_type': 0,
        'params': [('N', 12.0), ('M', 6.0)],
        'source': (
            "PSY: COUNT(CLOSE > REF(CLOSE, 1), N) / N * 100;\n"
            "PSYMA: MA(PSY, M);"
        ),
    },
    {
        'acCode': 'ROC',
        'name': 'ROC 变动速率',
        'description': '价格变动率指标',
        'formula_type': 0,
        'params': [('N', 12.0), ('M', 6.0)],
        'source': (
            "ROC: (CLOSE - REF(CLOSE, N)) / REF(CLOSE, N) * 100;\n"
            "MAROC: MA(ROC, M);"
        ),
    },
    {
        'acCode': 'MTM',
        'name': 'MTM 动量线',
        'description': '经典动量动力指标',
        'formula_type': 0,
        'params': [('N', 12.0), ('M', 6.0)],
        'source': (
            "MTM: CLOSE - REF(CLOSE, N);\n"
            "MTMMA: MA(MTM, M);"
        ),
    },
    {
        'acCode': 'VOL',
        'name': 'VOL 成交量',
        'description': '成交量及均量线指标',
        'formula_type': 0,
        'params': [('M1', 5.0), ('M2', 10.0)],
        'source': (
            "VOLUME: VOL, VOLSTICK;\n"
            "MAVOL1: MA(VOL, M1);\n"
            "MAVOL2: MA(VOL, M2);"
        ),
    },
    {
        'acCode': 'DMA',
        'name': 'DMA 平均差',
        'description': '快慢均线差值指标',
        'formula_type': 0,
        'params': [('SHORT', 10.0), ('LONG', 50.0), ('MID', 10.0)],
        'source': (
            "DDD: MA(CLOSE, SHORT) - MA(CLOSE, LONG);\n"
            "AMA: MA(DDD, MID);"
        ),
    },
    {
        'acCode': 'TRIX',
        'name': 'TRIX 三重指数平滑均线',
        'description': '三重指数平滑移动平均指标',
        'formula_type': 0,
        'params': [('N', 12.0), ('M', 9.0)],
        'source': (
            "TR := EMA(EMA(EMA(CLOSE, N), N), N);\n"
            "TRIX: (TR - REF(TR, 1)) / REF(TR, 1) * 100;\n"
            "MATRIX: MA(TRIX, M);"
        ),
    },

    # -------------------------------------------------------------
    # formula_type == 1: Selection Formulas (xg)
    # -------------------------------------------------------------
    {
        'acCode': 'UPN',
        'name': '连涨N日选股',
        'description': '连续 N 个交易日收盘价上涨',
        'formula_type': 1,
        'params': [('N', 3.0)],
        'source': "EVERY(CLOSE > REF(CLOSE, 1), N)",
    },
    {
        'acCode': 'DOWNN',
        'name': '连跌N日选股',
        'description': '连续 N 个交易日收盘价下跌',
        'formula_type': 1,
        'params': [('N', 3.0)],
        'source': "EVERY(CLOSE < REF(CLOSE, 1), N)",
    },
    {
        'acCode': 'MA_CROSS',
        'name': '均线金叉选股',
        'description': '短期均线上穿长期均线金叉',
        'formula_type': 1,
        'params': [('N1', 5.0), ('N2', 20.0)],
        'source': "CROSS(MA(CLOSE, N1), MA(CLOSE, N2))",
    },
    {
        'acCode': 'MACD_CROSS',
        'name': 'MACD金叉选股',
        'description': 'DIF 上穿 DEA 金叉信号',
        'formula_type': 1,
        'params': [('SHORT', 12.0), ('LONG', 26.0), ('MID', 9.0)],
        'source': (
            "DIF := EMA(CLOSE, SHORT) - EMA(CLOSE, LONG);\n"
            "DEA := EMA(DIF, MID);\n"
            "CROSS(DIF, DEA);"
        ),
    },
    {
        'acCode': 'KDJ_CROSS',
        'name': 'KDJ低位金叉选股',
        'description': 'KDJ 低位超卖区间 K 线上穿 D 线金叉',
        'formula_type': 1,
        'params': [('N', 9.0), ('M1', 3.0), ('M2', 3.0), ('THRESHOLD', 30.0)],
        'source': (
            "RSV := (CLOSE - LLV(LOW, N)) / (HHV(HIGH, N) - LLV(LOW, N)) * 100;\n"
            "K := SMA(RSV, M1, 1);\n"
            "D := SMA(K, M2, 1);\n"
            "CROSS(K, D) AND K < THRESHOLD;"
        ),
    },
    {
        'acCode': 'BOLL_BREAK',
        'name': '突破布林上轨选股',
        'description': '收盘价上穿布林带上轨',
        'formula_type': 1,
        'params': [('N', 20.0), ('P', 2.0)],
        'source': (
            "MID := MA(CLOSE, N);\n"
            "UPPER := MID + P * STD(CLOSE, N);\n"
            "CROSS(CLOSE, UPPER);"
        ),
    },
    {
        'acCode': 'VOL_BREAK',
        'name': '放量突破N日高点选股',
        'description': '突破前期高点且成交量明显放大',
        'formula_type': 1,
        'params': [('N', 20.0), ('RATE', 1.5)],
        'source': "CROSS(CLOSE, REF(HHV(HIGH, N), 1)) AND VOL > REF(MA(VOL, 5), 1) * RATE;",
    },
    {
        'acCode': 'RSI_OVERSOLD',
        'name': 'RSI超卖金叉选股',
        'description': 'RSI 超卖区间金叉信号',
        'formula_type': 1,
        'params': [('N1', 6.0), ('N2', 12.0), ('THRESHOLD', 30.0)],
        'source': (
            "LC := REF(CLOSE, 1);\n"
            "RSI1 := SMA(MAX(CLOSE - LC, 0), N1, 1) / SMA(ABS(CLOSE - LC), N1, 1) * 100;\n"
            "RSI2 := SMA(MAX(CLOSE - LC, 0), N2, 1) / SMA(ABS(CLOSE - LC), N2, 1) * 100;\n"
            "CROSS(RSI1, RSI2) AND RSI1 < THRESHOLD;"
        ),
    },

    # -------------------------------------------------------------
    # formula_type == 2: Expert Systems (exp)
    # -------------------------------------------------------------
    {
        'acCode': 'MACD',
        'name': 'MACD 专家交易系统',
        'description': '基于 MACD 交叉的多空买卖交易系统',
        'formula_type': 2,
        'params': [('SHORT', 12.0), ('LONG', 26.0), ('MID', 9.0)],
        'source': (
            "DIF := EMA(CLOSE, SHORT) - EMA(CLOSE, LONG);\n"
            "DEA := EMA(DIF, MID);\n"
            "ENTERLONG: CROSS(DIF, DEA);\n"
            "EXITLONG: CROSS(DEA, DIF);\n"
            "SIGNAL: IF(ENTERLONG, 1, IF(EXITLONG, -1, 0));"
        ),
    },
    {
        'acCode': 'MA',
        'name': 'MA 双均线专家交易系统',
        'description': '基于双均线交叉的多空买卖交易系统',
        'formula_type': 2,
        'params': [('SHORT', 5.0), ('LONG', 20.0)],
        'source': (
            "MA_S := MA(CLOSE, SHORT);\n"
            "MA_L := MA(CLOSE, LONG);\n"
            "ENTERLONG: CROSS(MA_S, MA_L);\n"
            "EXITLONG: CROSS(MA_L, MA_S);\n"
            "SIGNAL: IF(ENTERLONG, 1, IF(EXITLONG, -1, 0));"
        ),
    },
    {
        'acCode': 'KDJ',
        'name': 'KDJ 专家交易系统',
        'description': '基于 KDJ 指标交叉的多空买卖交易系统',
        'formula_type': 2,
        'params': [('N', 9.0), ('M1', 3.0), ('M2', 3.0)],
        'source': (
            "RSV := (CLOSE - LLV(LOW, N)) / (HHV(HIGH, N) - LLV(LOW, N)) * 100;\n"
            "K := SMA(RSV, M1, 1);\n"
            "D := SMA(K, M2, 1);\n"
            "ENTERLONG: CROSS(K, D);\n"
            "EXITLONG: CROSS(D, K);\n"
            "SIGNAL: IF(ENTERLONG, 1, IF(EXITLONG, -1, 0));"
        ),
    },
    {
        'acCode': 'BBI',
        'name': 'BBI 多空专家交易系统',
        'description': '基于 BBI 均线突破的多空买卖交易系统',
        'formula_type': 2,
        'params': [('M1', 3.0), ('M2', 6.0), ('M3', 12.0), ('M4', 24.0)],
        'source': (
            "BBI_VAL := (MA(CLOSE, M1) + MA(CLOSE, M2) + MA(CLOSE, M3) + MA(CLOSE, M4)) / 4;\n"
            "ENTERLONG: CROSS(CLOSE, BBI_VAL);\n"
            "EXITLONG: CROSS(BBI_VAL, CLOSE);\n"
            "SIGNAL: IF(ENTERLONG, 1, IF(EXITLONG, -1, 0));"
        ),
    },
]


def list_local_formulas(formula_type: int | None = None) -> list[dict[str, Any]]:
    """List available built-in TDX formulas filtered optionally by formula_type."""
    results: list[dict[str, Any]] = []
    for item in BUILTIN_FORMULA_LIST:
        if formula_type is not None and item['formula_type'] != formula_type:
            continue
        results.append({
            'acCode': item['acCode'],
            'name': item['name'],
            'description': item['description'],
            'formula_type': item['formula_type'],
            'params': list(item['params']),
            'engine': 'local_vectorized',
        })
    return results


def get_local_formula_info(code: str, formula_type: int | None = None) -> dict[str, Any]:
    """Retrieve full formula definition by code (case-insensitive) and optional formula_type."""
    code_upper = code.strip().upper()
    for item in BUILTIN_FORMULA_LIST:
        if item['acCode'].upper() == code_upper:
            if formula_type is None or item['formula_type'] == formula_type:
                return {
                    'acCode': item['acCode'],
                    'name': item['name'],
                    'description': item['description'],
                    'formula_type': item['formula_type'],
                    'params': list(item['params']),
                    'default_args': {p[0]: float(p[1]) for p in item['params']},
                    'source': item['source'],
                    'engine': 'local_vectorized',
                }
    type_suffix = f" (类型: {formula_type})" if formula_type is not None else ""
    raise TdxParamError(f"未找到内置公式: '{code}'{type_suffix}")


def parse_formula_args(
    param_defs: list[tuple[str, float]],
    args_str: str = '',
    overrides: dict[str, float] | None = None,
) -> dict[str, float]:
    """Parse formula arguments supporting positional ('12,26,9') or named ('SHORT=12,LONG=26')."""
    valid_map = {name.upper(): name for name, _ in param_defs}
    result: dict[str, float] = {name: float(def_val) for name, def_val in param_defs}

    if args_str and args_str.strip():
        parts = [p.strip() for p in args_str.split(',') if p.strip()]
        pos_idx = 0
        for part in parts:
            if '=' in part:
                k, v = part.split('=', 1)
                k_clean = k.strip().upper()
                if k_clean in RESERVED_SERIES_NAMES:
                    raise TdxParamError(f"参数名称 '{k.strip()}' 与系统保留字段冲突，不允许覆盖")
                if k_clean not in valid_map:
                    raise TdxParamError(
                        f"未知公式参数: '{k.strip()}'，支持的参数为: {list(valid_map.keys())}"
                    )
                try:
                    val = float(v.strip())
                except ValueError:
                    raise TdxParamError(f"参数 '{k.strip()}' 的值无效: '{v.strip()}'，必须为有效数值")
                result[valid_map[k_clean]] = val
            else:
                if pos_idx >= len(param_defs):
                    raise TdxParamError(
                        f"传入位置参数过多：公式仅定义了 {len(param_defs)} 个参数，但传入了更多参数"
                    )
                target_name = param_defs[pos_idx][0]
                if target_name.upper() in RESERVED_SERIES_NAMES:
                    raise TdxParamError(f"参数名称 '{target_name}' 与系统保留字段冲突，不允许覆盖")
                try:
                    val = float(part)
                except ValueError:
                    raise TdxParamError(f"参数 '{target_name}' 的位置参数值无效: '{part}'，必须为数值")
                result[target_name] = val
                pos_idx += 1

    if overrides:
        for k, v in overrides.items():
            k_clean = str(k).strip().upper()
            if k_clean in RESERVED_SERIES_NAMES:
                raise TdxParamError(f"参数名称 '{k}' 与系统保留字段冲突，不允许覆盖")
            if k_clean not in valid_map:
                raise TdxParamError(
                    f"覆盖参数中包含未知参数: '{k}'，支持的参数为: {list(valid_map.keys())}"
                )
            try:
                val = float(v)
            except (ValueError, TypeError):
                raise TdxParamError(f"覆盖参数 '{k}' 的值无效: '{v}'，必须为有效数值")
            result[valid_map[k_clean]] = val

    return result


# =====================================================================
# 6. Bar Data Extraction & Single/Batch Formula Evaluators
# =====================================================================

def _extract_market_series(bars: list[dict] | pd.DataFrame) -> tuple[list[str], dict[str, pd.Series | None], int]:
    """Convert input bars or DataFrame into canonical field Series and date list."""
    if isinstance(bars, pd.DataFrame):
        df = bars
        if df.empty:
            raise TdxParamError("行情 K 线数据为空，无法执行公式计算")
        n = len(df)
        cols_lower = {str(c).lower(): c for c in df.columns}

        # Date list
        if 'date' in cols_lower:
            dates = [str(d) for d in df[cols_lower['date']].tolist()]
        elif isinstance(df.index, (pd.DatetimeIndex, pd.Index)) and not isinstance(df.index, pd.RangeIndex):
            dates = [str(d) for d in df.index.tolist()]
        else:
            dates = [str(i) for i in range(n)]

        def get_col(f_name: str) -> pd.Series | None:
            if f_name in cols_lower:
                return pd.to_numeric(df[cols_lower[f_name]], errors='coerce').astype(float)
            return None

        series_dict = {
            'close': get_col('close'),
            'open': get_col('open'),
            'high': get_col('high'),
            'low': get_col('low'),
            'volume': get_col('volume') if 'volume' in cols_lower else get_col('vol'),
            'amount': get_col('amount') if 'amount' in cols_lower else get_col('amo'),
        }
        return dates, series_dict, n

    if isinstance(bars, list):
        if not bars:
            raise TdxParamError("行情 K 线数据为空，无法执行公式计算")
        n = len(bars)
        dates = [str(b.get('date', i)) for i, b in enumerate(bars)]

        def extract_list(keys: tuple[str, ...]) -> pd.Series | None:
            for k in keys:
                if any(k in b for b in bars):
                    vals = [float(b[k]) if (k in b and b[k] is not None and math.isfinite(float(b[k]))) else np.nan for b in bars]
                    return pd.Series(vals, dtype=float)
            return None

        series_dict = {
            'close': extract_list(('close', 'c', 'Close')),
            'open': extract_list(('open', 'o', 'Open')),
            'high': extract_list(('high', 'h', 'High')),
            'low': extract_list(('low', 'l', 'Low')),
            'volume': extract_list(('volume', 'vol', 'v', 'Volume')),
            'amount': extract_list(('amount', 'amo', 'Amount')),
        }
        return dates, series_dict, n

    raise TdxParamError(f"不支持的行情数据格式: {type(bars).__name__}，必须为 list[dict] 或 pd.DataFrame")


def evaluate_formula_on_bars(
    bars: list[dict] | pd.DataFrame,
    source: str,
    *,
    params: dict[str, float] | None = None,
    return_count: int = 1,
    return_date: bool = True,
    xsflag: int = -1,
    select_mode: bool = False,
) -> dict[str, Any]:
    """Evaluate TDX formula source on a single stock's bars."""
    if return_count < 0:
        raise TdxParamError(f"return_count 必须为非负整数，当前为: {return_count}")
    if not (-1 <= xsflag <= 8):
        raise TdxParamError(f"xsflag 小数位数必须在 -1 到 8 之间，当前为: {xsflag}")

    dates, series_dict, n_bars = _extract_market_series(bars)

    clean_src = source.strip()
    if clean_src.isalnum() and not any(ch in clean_src for ch in '():;=+-*/><!&|\n\r'):
        try:
            info = get_local_formula_info(clean_src)
            clean_src = info['source']
            if params is None:
                params = {}
            for p_name, p_val in info['params']:
                if p_name not in params and p_name.upper() not in params:
                    params[p_name] = p_val
        except TdxParamError:
            pass

    statements = parse_formula_statements(clean_src, select_mode=select_mode)

    # Normalize parameters
    normalized_params: dict[str, float] = {}
    if params:
        for k, v in params.items():
            k_clean = str(k).strip().upper()
            if k_clean in RESERVED_SERIES_NAMES:
                raise TdxParamError(f"参数名称 '{k}' 与系统保留字段冲突，不允许覆盖")
            try:
                normalized_params[k_clean] = float(v)
            except (ValueError, TypeError):
                raise TdxParamError(f"参数 '{k}' 的值无效: '{v}'，必须为数值")

    context: dict[str, Any] = {
        'series': series_dict,
        'params': normalized_params,
        'locals': {},
        'n_bars': n_bars,
    }

    raw_outputs: dict[str, pd.Series] = {}

    for stmt in statements:
        val = evaluate_ast(stmt.expr, context)
        # Convert scalar to Series aligned with bars
        if not isinstance(val, pd.Series):
            val_series = pd.Series([float(val)] * n_bars, dtype=float)
        else:
            val_series = val

        # Store in locals so subsequent statements can reference it
        context['locals'][stmt.name] = val_series

        if stmt.is_output:
            raw_outputs[stmt.name] = val_series

    if not raw_outputs:
        raise TdxParamError("公式中没有定义任何输出内容 (没有 ':' 输出语句或裸表达式)")

    # Slice and format numeric series according to return_count and xsflag
    formatted: dict[str, Any] = {}
    slice_slice = slice(-return_count, None) if return_count > 0 else slice(None)

    if return_date:
        formatted['Date'] = dates[slice_slice]

    for col_name, ser in raw_outputs.items():
        arr = ser.to_numpy(dtype=float)[slice_slice]
        col_vals: list[float | None] = []
        for val in arr:
            if math.isfinite(val):
                fval = float(val)
                col_vals.append(round(fval, xsflag) if xsflag >= 0 else fval)
            else:
                col_vals.append(None)
        formatted[col_name] = col_vals

    return formatted


def evaluate_batch_formula(
    batch_bars: dict[str, list[dict] | pd.DataFrame],
    *,
    expr: str | None = None,
    formula_name: str | None = None,
    formula_type: int | None = None,
    args_str: str = '',
    params: dict[str, float] | None = None,
    return_count: int = 1,
    return_date: bool = True,
    xsflag: int = -1,
    select_mode: bool = False,
) -> dict[str, Any]:
    """Evaluate TDX formula across multiple stocks simultaneously."""
    if not isinstance(batch_bars, dict) or not batch_bars:
        raise TdxParamError("批量行情数据必须为非空字典结构 (stock -> bars)")

    has_expr = expr is not None and bool(expr.strip())
    has_name = formula_name is not None and bool(formula_name.strip())

    if has_expr == has_name:
        raise TdxParamError("必须且仅能指定 expr 或 formula_name 之一")

    # Resolve formula and parameters
    if has_name:
        assert formula_name is not None
        info = get_local_formula_info(formula_name, formula_type)
        resolved_source = info['source']
        param_defs = info['params']
        resolved_params = parse_formula_args(param_defs, args_str, params)
        is_selection = select_mode or (info['formula_type'] == 1)
        formula_label = info['acCode']
    else:
        assert expr is not None
        resolved_source = expr.strip()
        resolved_params = {}
        if params:
            for k, v in params.items():
                k_clean = str(k).strip().upper()
                if k_clean in RESERVED_SERIES_NAMES:
                    raise TdxParamError(f"参数名称 '{k}' 与系统保留字段冲突，不允许覆盖")
                try:
                    resolved_params[k_clean] = float(v)
                except (ValueError, TypeError):
                    raise TdxParamError(f"参数 '{k}' 的值无效: '{v}'")
        if args_str and args_str.strip():
            for part in args_str.split(','):
                part = part.strip()
                if not part:
                    continue
                if '=' in part:
                    k, v = part.split('=', 1)
                    k_clean = k.strip().upper()
                    if k_clean in RESERVED_SERIES_NAMES:
                        raise TdxParamError(f"参数名称 '{k_clean}' 与系统保留字段冲突，不允许覆盖")
                    try:
                        resolved_params[k_clean] = float(v.strip())
                    except (ValueError, TypeError):
                        raise TdxParamError(f"参数 '{k}' 的值无效: '{v}'")
                else:
                    raise TdxParamError("自定义表达式的 args_str 必须使用命名格式 (如 'N=5,M=10')")
        is_selection = select_mode
        formula_label = 'CUSTOM_EXPR'

    per_stock_results: dict[str, Any] = {}
    selected_stocks: list[str] = []
    summary_rows: list[dict[str, Any]] = []

    for stock, bars in batch_bars.items():
        dates_all, _, _ = _extract_market_series(bars)
        last_date = dates_all[-1] if dates_all else None

        res = evaluate_formula_on_bars(
            bars,
            resolved_source,
            params=resolved_params,
            return_count=return_count,
            return_date=return_date,
            xsflag=xsflag,
            select_mode=is_selection,
        )
        per_stock_results[stock] = res

        out_cols = [c for c in res.keys() if c != 'Date']
        if not out_cols:
            continue

        last_col = out_cols[-1]
        last_val = res[last_col][-1] if res[last_col] else None

        hit = bool(last_val is not None and last_val > 0)
        if is_selection and hit:
            selected_stocks.append(stock)

        row: dict[str, Any] = {
            'date': last_date,
            'stock': stock,
        }
        for c in out_cols:
            row[c] = res[c][-1] if res[c] else None
        if is_selection:
            row['hit'] = hit
        summary_rows.append(row)

    return {
        'engine': 'local_vectorized',
        'formula': formula_label,
        'source': resolved_source,
        'params': resolved_params,
        'total_evaluated': len(batch_bars),
        'selected_stocks': selected_stocks,
        'selected_count': len(selected_stocks),
        'results': per_stock_results,
        'summary_rows': summary_rows,
    }
