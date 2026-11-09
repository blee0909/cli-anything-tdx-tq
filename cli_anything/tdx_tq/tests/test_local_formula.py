"""Unit and integration tests for Phase 4 Core Local Vectorized TDX Formula Compiler.

Verifies:
- Tokenizer, zero-eval recursive-descent parser, and AST evaluator
- Mathematical accuracy of 28+ MyTT/TDX primitives (SMA, EMA, MA, MACD, KDJ, RSI, etc.)
- TDX multi-statement syntax, drawing modifier stripping, block comments
- Division-by-zero safety, sandbox security, parameter shadowing rejection
- Built-in formula library (zb, xg, exp) and batch formula evaluation
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from cli_anything.tdx_tq.core.errors import TdxParamError
from cli_anything.tdx_tq.core.local_formula import (
    BUILTIN_FORMULA_LIST,
    RESERVED_SERIES_NAMES,
    evaluate_batch_formula,
    evaluate_formula_on_bars,
    get_local_formula_info,
    list_local_formulas,
    parse_formula_args,
    safe_comp,
    safe_div,
    strip_tdx_comments,
    tdx_avedev,
    tdx_barslast,
    tdx_between,
    tdx_count,
    tdx_cross,
    tdx_dma,
    tdx_ema,
    tdx_every,
    tdx_exist,
    tdx_hhv,
    tdx_hhvbars,
    tdx_if,
    tdx_llv,
    tdx_llvbars,
    tdx_ma,
    tdx_slope,
    tdx_sma,
    tdx_sum,
    tdx_wma,
    tokenize,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def sample_bars() -> list[dict[str, Any]]:
    """30-bar synthetic OHLCV dataset with known upward/downward swings."""
    bars: list[dict[str, Any]] = []
    base_price = 10.0
    for i in range(1, 31):
        # Sine wave superimposed on trend
        swing = math.sin(i / 3.0) * 1.5
        trend = i * 0.2
        c = round(base_price + trend + swing, 2)
        o = round(c - 0.1, 2)
        h = round(c + 0.3, 2)
        l = round(c - 0.3, 2)
        v = float(1000 + i * 50)
        a = round(c * v, 2)
        bars.append({
            'date': f'2023-01-{i:02d}',
            'open': o,
            'high': h,
            'low': l,
            'close': c,
            'volume': v,
            'amount': a,
        })
    return bars


@pytest.fixture
def sample_dataframe(sample_bars: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(sample_bars)


# =====================================================================
# 1. Mathematical Accuracy Tests of Core TDX Primitives
# =====================================================================

def test_ma_calculation():
    s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    res = tdx_ma(s, 3)
    assert pd.isna(res.iloc[0])
    assert pd.isna(res.iloc[1])
    assert pytest.approx(res.iloc[2]) == 20.0
    assert pytest.approx(res.iloc[3]) == 30.0
    assert pytest.approx(res.iloc[4]) == 40.0

    with pytest.raises(TdxParamError, match="MA函数的计算周期 N 必须大于0"):
        tdx_ma(s, 0)


def test_ema_calculation():
    s = pd.Series([10.0, 20.0, 30.0])
    res = tdx_ema(s, 3)
    # alpha = 2 / (3 + 1) = 0.5
    # y0 = 10.0
    # y1 = 0.5 * 20 + 0.5 * 10 = 15.0
    # y2 = 0.5 * 30 + 0.5 * 15 = 22.5
    assert pytest.approx(res.iloc[0]) == 10.0
    assert pytest.approx(res.iloc[1]) == 15.0
    assert pytest.approx(res.iloc[2]) == 22.5

    with pytest.raises(TdxParamError, match="EMA函数的计算周期 N 必须大于0"):
        tdx_ema(s, -1)


def test_sma_recursive_calculation():
    """Authentic TDX recursive SMA: Y_t = (M*X_t + (N-M)*Y_{t-1}) / N."""
    s = pd.Series([np.nan, 10.0, 20.0, 30.0])
    res = tdx_sma(s, 3, 1)

    assert pd.isna(res.iloc[0])
    assert pytest.approx(res.iloc[1]) == 10.0
    # y2 = (1 * 20 + 2 * 10) / 3 = 40 / 3
    assert pytest.approx(res.iloc[2]) == 40.0 / 3.0
    # y3 = (1 * 30 + 2 * (40/3)) / 3 = 170 / 9
    assert pytest.approx(res.iloc[3]) == 170.0 / 9.0

    # Validation: M must be > 0 and <= N, N > 0
    with pytest.raises(TdxParamError, match="SMA函数的参数必须满足"):
        tdx_sma(s, 3, 4)
    with pytest.raises(TdxParamError, match="SMA函数的参数必须满足"):
        tdx_sma(s, 0, 1)


def test_wma_calculation():
    """Weights: 1, 2, ..., N."""
    s = pd.Series([10.0, 20.0, 30.0])
    res = tdx_wma(s, 3)
    # (1*10 + 2*20 + 3*30) / (1+2+3) = (10 + 40 + 90) / 6 = 140 / 6
    assert pd.isna(res.iloc[0])
    assert pd.isna(res.iloc[1])
    assert pytest.approx(res.iloc[2]) == 140.0 / 6.0


def test_dma_calculation():
    """DMA: Y_t = A_t * X_t + (1 - A_t) * Y_{t-1}."""
    s = pd.Series([10.0, 20.0, 30.0])
    res = tdx_dma(s, 0.2)
    assert pytest.approx(res.iloc[0]) == 10.0
    # y1 = 0.2 * 20 + 0.8 * 10 = 12.0
    assert pytest.approx(res.iloc[1]) == 12.0
    # y2 = 0.2 * 30 + 0.8 * 12 = 15.6
    assert pytest.approx(res.iloc[2]) == 15.6


def test_hhv_llv_and_bars():
    s = pd.Series([10.0, 15.0, 12.0, 18.0, 14.0])
    hhv3 = tdx_hhv(s, 3)
    llv3 = tdx_llv(s, 3)
    assert hhv3.tolist() == [10.0, 15.0, 15.0, 18.0, 18.0]
    assert llv3.tolist() == [10.0, 10.0, 10.0, 12.0, 12.0]

    # Expanding window when N = 0
    hhv0 = tdx_hhv(s, 0)
    assert hhv0.tolist() == [10.0, 15.0, 15.0, 18.0, 18.0]

    # Bars since highest / lowest
    hhv_b = tdx_hhvbars(s, 3)
    llv_b = tdx_llvbars(s, 3)
    assert hhv_b.tolist() == [0.0, 0.0, 1.0, 0.0, 1.0]
    assert llv_b.tolist() == [0.0, 1.0, 2.0, 1.0, 2.0]


def test_cross():
    """CROSS(A, B): (prev_a <= prev_b) & (curr_a > curr_b) with finite check."""
    a = pd.Series([10.0, 12.0, 15.0, 14.0])
    b = pd.Series([11.0, 12.0, 13.0, 16.0])
    res = tdx_cross(a, b)
    assert res.tolist() == [0.0, 0.0, 1.0, 0.0]


def test_count_every_exist():
    cond = pd.Series([0.0, 1.0, 1.0, 1.0, 0.0])
    cnt = tdx_count(cond, 3)
    evy = tdx_every(cond, 3)
    ext = tdx_exist(cond, 3)

    assert cnt.tolist() == [0.0, 1.0, 2.0, 3.0, 2.0]
    assert evy.tolist() == [0.0, 0.0, 0.0, 1.0, 0.0]
    assert ext.tolist() == [0.0, 1.0, 1.0, 1.0, 1.0]


def test_barslast():
    cond = pd.Series([0.0, 1.0, 0.0, 0.0, 1.0, 0.0])
    res = tdx_barslast(cond)
    assert pd.isna(res.iloc[0])
    assert res.iloc[1] == 0.0
    assert res.iloc[2] == 1.0
    assert res.iloc[3] == 2.0
    assert res.iloc[4] == 0.0
    assert res.iloc[5] == 1.0


def test_avedev():
    """Mean absolute deviation."""
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    res = tdx_avedev(s, 5)
    # mean = 3.0, deviations = [2, 1, 0, 1, 2], sum = 6, mean = 1.2
    assert pd.isna(res.iloc[0])
    assert pytest.approx(res.iloc[4]) == 1.2


def test_slope():
    """Rolling linear regression slope."""
    s = pd.Series([10.0, 20.0, 30.0, 40.0])
    res = tdx_slope(s, 3)
    assert pd.isna(res.iloc[0])
    assert pd.isna(res.iloc[1])
    assert pytest.approx(res.iloc[2]) == 10.0
    assert pytest.approx(res.iloc[3]) == 10.0

    with pytest.raises(TdxParamError, match="SLOPE函数的计算周期 N 必须大于等于2"):
        tdx_slope(s, 1)


def test_between_and_if():
    s = pd.Series([5.0, 10.0, 15.0, 25.0])
    btn = tdx_between(s, 10.0, 20.0)
    assert btn.tolist() == [0.0, 1.0, 1.0, 0.0]

    # Reversed bounds min(A, B) <= X <= max(A, B)
    btn_rev = tdx_between(s, 20.0, 10.0)
    assert btn_rev.tolist() == [0.0, 1.0, 1.0, 0.0]

    cond = pd.Series([1.0, 0.0, 1.0, 0.0])
    res_if = tdx_if(cond, 100.0, 200.0)
    assert res_if.tolist() == [100.0, 200.0, 100.0, 200.0]


# =====================================================================
# 2. Syntax, Comments, Drawing Modifiers & Sandbox Security
# =====================================================================

def test_strip_comments():
    src = "{头注释} A := C; {行中注释} B: A + 1; {尾注释}"
    stripped = strip_tdx_comments(src)
    assert "{头注释}" not in stripped
    assert "A := C;" in stripped
    assert "B: A + 1;" in stripped

    # Nested comments
    nested = "{外层 {内层} 注释} RESULT: 1;"
    assert strip_tdx_comments(nested).strip() == "RESULT: 1;"

    # Unclosed comment error
    with pytest.raises(TdxParamError, match="存在未闭合的注释符号"):
        strip_tdx_comments("{未闭合注释 C + 1")

    with pytest.raises(TdxParamError, match="遇到未匹配的闭合注释符号"):
        strip_tdx_comments("C + 1 }")


def test_strip_drawing_modifiers(sample_bars: list[dict[str, Any]]):
    """Trailing TDX modifiers like COLORWHITE, LINETHICK2, NODRAW must be cleanly ignored."""
    src = (
        "DIF: EMA(C, 12) - EMA(C, 26), COLORWHITE, LINETHICK2;\n"
        "DEA: EMA(DIF, 9), COLORYELLOW, NODRAW;\n"
        "MACD: (DIF - DEA) * 2, COLORSTICK;"
    )
    res = evaluate_formula_on_bars(sample_bars, src, return_count=1)
    assert 'DIF' in res
    assert 'DEA' in res
    assert 'MACD' in res
    assert len(res['DIF']) == 1


def test_intermediate_vs_output_variables(sample_bars: list[dict[str, Any]]):
    """':=' creates hidden variables; ':' creates output series."""
    src = (
        "HIDDEN_X := C * 2;\n"
        "OUTPUT_Y: HIDDEN_X + 10;\n"
    )
    res = evaluate_formula_on_bars(sample_bars, src, return_count=5)
    assert 'HIDDEN_X' not in res
    assert 'OUTPUT_Y' in res
    assert len(res['OUTPUT_Y']) == 5


def test_bare_expression_output_naming(sample_bars: list[dict[str, Any]]):
    """Bare expression defaults to 'RESULT', or 'SELECT' in select_mode."""
    src = "CROSS(MA(C, 5), MA(C, 10))"
    res1 = evaluate_formula_on_bars(sample_bars, src, select_mode=False)
    assert 'RESULT' in res1

    res2 = evaluate_formula_on_bars(sample_bars, src, select_mode=True)
    assert 'SELECT' in res2


def test_operators_and_case_insensitivity(sample_bars: list[dict[str, Any]]):
    """Case-insensitivity, <>, !=, ==, =, AND, &&, OR, ||, NOT, !."""
    src = (
        "t1: c == close;\n"
        "t2: c = close;\n"
        "t3: c != o;\n"
        "t4: c <> o;\n"
        "t5: (c > o) and (v > 1000);\n"
        "t6: (c > o) && (v > 1000);\n"
        "t7: (c > o) or (v > 1000);\n"
        "t8: (c > o) || (v > 1000);\n"
        "t9: not(c == o);\n"
        "t10: !(c == o);"
    )
    res = evaluate_formula_on_bars(sample_bars, src, return_count=1)
    assert res['T1'] == [1.0]
    assert res['T2'] == [1.0]
    assert res['T3'] == res['T4']
    assert res['T5'] == res['T6']
    assert res['T7'] == res['T8']
    assert res['T9'] == res['T10']


def test_safe_division_and_inf(sample_bars: list[dict[str, Any]]):
    """Safe division yields NaN / None, never ZeroDivisionError or Inf."""
    src = (
        "DIV_ZERO_SERIES: C / 0;\n"
        "DIV_ZERO_SCALAR: 100 / 0;\n"
        "DIV_VALID: 10 / 2;"
    )
    res = evaluate_formula_on_bars(sample_bars, src, return_count=1)
    assert res['DIV_ZERO_SERIES'] == [None]
    assert res['DIV_ZERO_SCALAR'] == [None]
    assert res['DIV_VALID'] == [5.0]


def test_sandbox_security(sample_bars: list[dict[str, Any]]):
    """Malicious Python attribute access or eval attempts must raise TdxParamError."""
    # Attribute access dot
    with pytest.raises(TdxParamError, match="不支持的字符"):
        evaluate_formula_on_bars(sample_bars, "C.__class__")

    # String literal / import
    with pytest.raises(TdxParamError, match="不支持的字符"):
        evaluate_formula_on_bars(sample_bars, "__import__('os')")

    # Unknown function
    with pytest.raises(TdxParamError, match="未支持的函数"):
        evaluate_formula_on_bars(sample_bars, "SYSTEM_EXEC(C)")


def test_missing_ohlcv_field_error():
    """Formula referencing OPEN when bars lack 'open' raises TdxParamError."""
    bars_no_open = [{'date': '2023-01-01', 'close': 10.0}]
    # Referencing C works fine
    res = evaluate_formula_on_bars(bars_no_open, "RES: C * 2;")
    assert res['RES'] == [20.0]

    # Referencing OPEN raises clear error
    with pytest.raises(TdxParamError, match="公式引用了字段 OPEN，但行情数据中缺少 open 字段"):
        evaluate_formula_on_bars(bars_no_open, "RES: OPEN + C;")


def test_disallow_shadowing_reserved_series(sample_bars: list[dict[str, Any]]):
    """Parameters cannot shadow reserved names C, O, H, L, V, etc."""
    with pytest.raises(TdxParamError, match="与系统保留字段冲突"):
        parse_formula_args([('N', 20.0)], overrides={'C': 10.0})

    with pytest.raises(TdxParamError, match="与系统保留字段冲突"):
        evaluate_formula_on_bars(sample_bars, "MA(C, 5)", params={'CLOSE': 10.0})


# =====================================================================
# 3. Parameter Parsing & Built-in Formula Library
# =====================================================================

def test_parse_formula_args():
    p_defs = [('SHORT', 12.0), ('LONG', 26.0), ('MID', 9.0)]

    # Defaults
    d = parse_formula_args(p_defs)
    assert d == {'SHORT': 12.0, 'LONG': 26.0, 'MID': 9.0}

    # Positional
    p = parse_formula_args(p_defs, '10,20,5')
    assert p == {'SHORT': 10.0, 'LONG': 20.0, 'MID': 5.0}

    # Named
    n = parse_formula_args(p_defs, 'LONG=30')
    assert n == {'SHORT': 12.0, 'LONG': 30.0, 'MID': 9.0}

    # Excess positional
    with pytest.raises(TdxParamError, match="传入位置参数过多"):
        parse_formula_args(p_defs, '10,20,5,30')

    # Unknown named
    with pytest.raises(TdxParamError, match="未知公式参数"):
        parse_formula_args(p_defs, 'EXTRA=5')

    # Non-numeric
    with pytest.raises(TdxParamError, match="必须为有效数值"):
        parse_formula_args(p_defs, 'SHORT=abc')


def test_formula_library_lookup():
    all_formulas = list_local_formulas()
    assert len(all_formulas) >= 30

    zb_list = list_local_formulas(formula_type=0)
    xg_list = list_local_formulas(formula_type=1)
    exp_list = list_local_formulas(formula_type=2)

    assert len(zb_list) >= 18
    assert len(xg_list) >= 8
    assert len(exp_list) >= 4

    # Lookup by code
    macd_info = get_local_formula_info('MACD', formula_type=0)
    assert macd_info['acCode'] == 'MACD'
    assert 'SHORT' in macd_info['default_args']

    # Case-insensitive
    kdj_info = get_local_formula_info('kdj', formula_type=0)
    assert kdj_info['acCode'] == 'KDJ'

    with pytest.raises(TdxParamError, match="未找到内置公式"):
        get_local_formula_info('NON_EXISTENT_FORMULA')


# =====================================================================
# 4. Complex Built-in Technical Indicators & Selection Evaluation
# =====================================================================

def test_evaluate_macd(sample_bars: list[dict[str, Any]]):
    info = get_local_formula_info('MACD', formula_type=0)
    res = evaluate_formula_on_bars(sample_bars, info['source'], params=info['default_args'], return_count=0)
    assert 'DIF' in res and 'DEA' in res and 'MACD' in res
    assert len(res['DIF']) == len(sample_bars)
    # Check that MACD = (DIF - DEA) * 2
    dif = res['DIF'][-1]
    dea = res['DEA'][-1]
    macd = res['MACD'][-1]
    assert pytest.approx(macd) == (dif - dea) * 2.0


def test_evaluate_kdj(sample_bars: list[dict[str, Any]]):
    info = get_local_formula_info('KDJ', formula_type=0)
    res = evaluate_formula_on_bars(sample_bars, info['source'], params=info['default_args'], return_count=0)
    assert 'K' in res and 'D' in res and 'J' in res
    # J = 3*K - 2*D
    k = res['K'][-1]
    d = res['D'][-1]
    j = res['J'][-1]
    assert pytest.approx(j) == 3.0 * k - 2.0 * d


def test_evaluate_rsi(sample_bars: list[dict[str, Any]]):
    info = get_local_formula_info('RSI', formula_type=0)
    res = evaluate_formula_on_bars(sample_bars, info['source'], params=info['default_args'], return_count=5)
    assert 'RSI1' in res and 'RSI2' in res and 'RSI3' in res
    for val in res['RSI1']:
        if val is not None:
            assert 0.0 <= val <= 100.0


def test_evaluate_boll(sample_bars: list[dict[str, Any]]):
    info = get_local_formula_info('BOLL', formula_type=0)
    res = evaluate_formula_on_bars(sample_bars, info['source'], params=info['default_args'], return_count=1)
    assert 'BOLL' in res and 'UB' in res and 'LB' in res
    boll = res['BOLL'][0]
    ub = res['UB'][0]
    lb = res['LB'][0]
    assert ub > boll > lb


def test_evaluate_cci_and_atr(sample_bars: list[dict[str, Any]]):
    cci_info = get_local_formula_info('CCI', formula_type=0)
    res_cci = evaluate_formula_on_bars(sample_bars, cci_info['source'], params=cci_info['default_args'], return_count=1)
    assert 'CCI' in res_cci
    assert res_cci['CCI'][0] is not None

    atr_info = get_local_formula_info('ATR', formula_type=0)
    res_atr = evaluate_formula_on_bars(sample_bars, atr_info['source'], params=atr_info['default_args'], return_count=1)
    assert 'ATR' in res_atr
    assert res_atr['ATR'][0] > 0.0


# =====================================================================
# 5. Batch Formula Evaluation API (Indicator, Screening, Expert)
# =====================================================================

def test_evaluate_batch_formula_indicator_mode(sample_bars: list[dict[str, Any]]):
    # Two stocks
    bars2 = list(sample_bars)
    batch = {
        'sh600000': sample_bars,
        'sz000001': bars2,
    }

    res = evaluate_batch_formula(batch, formula_name='MA', args_str='5,10,20,60', return_count=2, xsflag=2)
    assert res['engine'] == 'local_vectorized'
    assert res['formula'] == 'MA'
    assert res['total_evaluated'] == 2
    assert len(res['results']['sh600000']['MA1']) == 2
    assert len(res['summary_rows']) == 2

    # Verify summary rows format
    row0 = res['summary_rows'][0]
    assert row0['stock'] == 'sh600000'
    assert 'MA1' in row0 and 'MA2' in row0
    assert 'date' in row0


def test_evaluate_batch_formula_select_mode(sample_bars: list[dict[str, Any]]):
    # Stock 1 has strong upward trend in last 3 bars
    stock1_bars = [
        {'date': '2023-01-01', 'close': 10.0, 'open': 9.8, 'high': 10.2, 'low': 9.7, 'volume': 1000},
        {'date': '2023-01-02', 'close': 11.0, 'open': 10.1, 'high': 11.2, 'low': 10.0, 'volume': 1200},
        {'date': '2023-01-03', 'close': 12.0, 'open': 11.1, 'high': 12.2, 'low': 11.0, 'volume': 1500},
        {'date': '2023-01-04', 'close': 13.0, 'open': 12.1, 'high': 13.2, 'low': 12.0, 'volume': 1800},
    ]
    # Stock 2 falls on the last bar
    stock2_bars = [
        {'date': '2023-01-01', 'close': 10.0, 'open': 9.8, 'high': 10.2, 'low': 9.7, 'volume': 1000},
        {'date': '2023-01-02', 'close': 11.0, 'open': 10.1, 'high': 11.2, 'low': 10.0, 'volume': 1200},
        {'date': '2023-01-03', 'close': 12.0, 'open': 11.1, 'high': 12.2, 'low': 11.0, 'volume': 1500},
        {'date': '2023-01-04', 'close': 11.5, 'open': 12.0, 'high': 12.1, 'low': 11.3, 'volume': 800},
    ]

    batch = {
        'sh600000': stock1_bars,
        'sz000001': stock2_bars,
    }

    # UPN (consecutive 3 up bars)
    res = evaluate_batch_formula(batch, formula_name='UPN', args_str='N=3')
    assert res['total_evaluated'] == 2
    assert res['selected_stocks'] == ['sh600000']
    assert res['selected_count'] == 1

    # Check summary rows contain 'hit' boolean
    rows = {r['stock']: r for r in res['summary_rows']}
    assert rows['sh600000']['hit'] is True
    assert rows['sz000001']['hit'] is False


def test_evaluate_batch_custom_expr(sample_bars: list[dict[str, Any]]):
    batch = {'sh600000': sample_bars}
    # Custom screening expression
    res = evaluate_batch_formula(
        batch,
        expr="CROSS(C, MA(C, 5))",
        select_mode=True,
    )
    assert res['formula'] == 'CUSTOM_EXPR'
    assert 'SELECT' in res['summary_rows'][0]


def test_evaluate_batch_expert_system(sample_bars: list[dict[str, Any]]):
    batch = {'sh600000': sample_bars}
    res = evaluate_batch_formula(batch, formula_name='MACD', formula_type=2)
    row = res['summary_rows'][0]
    assert 'ENTERLONG' in row
    assert 'EXITLONG' in row
    assert 'SIGNAL' in row


def test_evaluate_batch_validation_errors(sample_bars: list[dict[str, Any]]):
    batch = {'sh600000': sample_bars}

    # Neither expr nor formula_name
    with pytest.raises(TdxParamError, match="必须且仅能指定 expr 或 formula_name 之一"):
        evaluate_batch_formula(batch)

    # Both expr and formula_name
    with pytest.raises(TdxParamError, match="必须且仅能指定 expr 或 formula_name 之一"):
        evaluate_batch_formula(batch, expr="C > O", formula_name="MA")

    # Empty batch
    with pytest.raises(TdxParamError, match="批量行情数据必须为非空字典结构"):
        evaluate_batch_formula({})


def test_return_count_and_xsflag_options(sample_bars: list[dict[str, Any]]):
    # return_count=0 returns all bars
    res_all = evaluate_formula_on_bars(sample_bars, "MA(C, 5)", return_count=0)
    assert len(res_all['RESULT']) == len(sample_bars)
    assert len(res_all['Date']) == len(sample_bars)

    # return_date=False omits 'Date'
    res_no_date = evaluate_formula_on_bars(sample_bars, "MA(C, 5)", return_date=False)
    assert 'Date' not in res_no_date

    # xsflag rounding
    res_round = evaluate_formula_on_bars(sample_bars, "C * 1.0055", xsflag=2, return_count=1)
    val = res_round['RESULT'][0]
    assert len(str(val).split('.')[1]) <= 2

    # Invalid options
    with pytest.raises(TdxParamError, match="return_count 必须为非负整数"):
        evaluate_formula_on_bars(sample_bars, "C", return_count=-1)

    with pytest.raises(TdxParamError, match="xsflag 小数位数必须在 -1 到 8 之间"):
        evaluate_formula_on_bars(sample_bars, "C", xsflag=10)


def test_math_functions_coverage(sample_bars: list[dict[str, Any]]):
    """Test SQRT, LN/LOG, EXP, POW, ROUND, CEILING, FLOOR, INTPART, SIGN, BARSCOUNT."""
    src = (
        "R_ABS: ABS(-10.5);\n"
        "R_SQRT: SQRT(16);\n"
        "R_SQRT_NEG: SQRT(-4);\n"
        "R_LN: LN(EXP(2));\n"
        "R_LOG: LOG(EXP(3));\n"
        "R_POW: POW(2, 3);\n"
        "R_ROUND: ROUND(3.14159, 2);\n"
        "R_CEIL: CEILING(3.14);\n"
        "R_FLOOR: FLOOR(3.89);\n"
        "R_INTPART: INTPART(-3.89);\n"
        "R_SIGN: SIGN(-5.5);\n"
        "R_COUNT: BARSCOUNT(C);"
    )
    res = evaluate_formula_on_bars(sample_bars, src, return_count=1)
    assert res['R_ABS'] == [10.5]
    assert res['R_SQRT'] == [4.0]
    assert res['R_SQRT_NEG'] == [None]
    assert pytest.approx(res['R_LN'][0]) == 2.0
    assert pytest.approx(res['R_LOG'][0]) == 3.0
    assert res['R_POW'] == [8.0]
    assert res['R_ROUND'] == [3.14]
    assert res['R_CEIL'] == [4.0]
    assert res['R_FLOOR'] == [3.0]
    assert res['R_INTPART'] == [-3.0]
    assert res['R_SIGN'] == [-1.0]
    assert res['R_COUNT'] == [float(len(sample_bars))]


def test_std_and_stdp():
    s = pd.Series([10.0, 20.0, 30.0])
    # sample std (ddof=1) vs population std (ddof=0)
    res_std = evaluate_formula_on_bars(
        [{'date': f'd{i}', 'close': v} for i, v in enumerate([10.0, 20.0, 30.0])],
        "S1: STD(C, 3); S2: STDP(C, 3);",
        return_count=1,
    )
    assert pytest.approx(res_std['S1'][0]) == 10.0
    assert pytest.approx(res_std['S2'][0]) == math.sqrt(200.0 / 3.0)


def test_sum_primitive():
    s = [{'date': f'd{i}', 'close': float(i)} for i in range(1, 6)]
    res = evaluate_formula_on_bars(s, "S_ROLL: SUM(C, 3); S_CUM: SUM(C, 0);", return_count=0)
    assert res['S_ROLL'] == [None, None, 6.0, 9.0, 12.0]
    assert res['S_CUM'] == [1.0, 3.0, 6.0, 10.0, 15.0]


def test_wrong_function_arg_count_error(sample_bars: list[dict[str, Any]]):
    with pytest.raises(TdxParamError, match="函数 MA 需要 2 个参数"):
        evaluate_formula_on_bars(sample_bars, "MA(C)")

    with pytest.raises(TdxParamError, match="函数 CROSS 需要 2 个参数"):
        evaluate_formula_on_bars(sample_bars, "CROSS(C, MA(C, 5), 10)")


def test_all_builtin_formulas_execution(sample_bars: list[dict[str, Any]]):
    """Verify that every single formula in BUILTIN_FORMULA_LIST evaluates successfully."""
    batch = {'sh600000': sample_bars}
    for item in BUILTIN_FORMULA_LIST:
        res = evaluate_batch_formula(
            batch,
            formula_name=item['acCode'],
            formula_type=item['formula_type'],
            return_count=1,
        )
        assert res['formula'] == item['acCode']
        assert len(res['summary_rows']) == 1
        stock_out = res['results']['sh600000']
        # Must produce at least one output column besides Date
        cols = [k for k in stock_out.keys() if k != 'Date']
        assert len(cols) >= 1


def test_batch_return_date_false_summary_rows(sample_bars: list[dict[str, Any]]):
    """summary_rows['date'] must ALWAYS have the bar date string even when return_date=False."""
    batch = {'sh600000': sample_bars}
    res = evaluate_batch_formula(batch, formula_name='MA', return_date=False)
    assert 'Date' not in res['results']['sh600000']
    assert res['summary_rows'][0]['date'] == sample_bars[-1]['date']
    assert res['summary_rows'][0]['date'] is not None


def test_token_repr_and_scientific_numbers():
    t = tokenize("1.5e-2 + 1e5 - 2.5E+3")[0]
    assert "Token" in repr(t)
    assert t.type == 'NUMBER'

    # Invalid float in tokenizer
    with pytest.raises(TdxParamError, match="无法解析数字"):
        tokenize("1.2.3")


def test_ensure_series_coverage():
    from cli_anything.tdx_tq.core.local_formula import _ensure_series

    s1 = _ensure_series([1.0, 2.0, 3.0])
    assert isinstance(s1, pd.Series)

    s2 = _ensure_series(5.0, length=3)
    assert len(s2) == 3 and s2.iloc[0] == 5.0

    s3 = _ensure_series(10.0, index=pd.Index(['a', 'b']))
    assert list(s3.index) == ['a', 'b']

    with pytest.raises(TdxParamError, match="无法将数据类型"):
        _ensure_series("invalid_str")


def test_scalar_and_series_binary_combinations():
    from cli_anything.tdx_tq.core.local_formula import safe_and, safe_comp, safe_div, safe_or

    # Scalar vs scalar
    assert safe_div(10.0, 2.0) == 5.0
    assert math.isnan(safe_div(10.0, 0.0))
    assert math.isnan(safe_div(10.0, float('nan')))
    assert safe_comp('>', 10.0, 5.0) == 1.0
    assert safe_comp('<', 10.0, 5.0) == 0.0
    assert math.isnan(safe_comp('>', 10.0, float('nan')))
    assert safe_and(1.0, 2.0) == 1.0
    assert safe_and(1.0, 0.0) == 0.0
    assert safe_or(1.0, 0.0) == 1.0
    assert safe_or(0.0, 0.0) == 0.0

    with pytest.raises(TdxParamError, match="未知比较运算符"):
        safe_comp('UNKNOWN_OP', 1.0, 2.0)

    # Scalar vs Series & Series vs Scalar
    s = pd.Series([10.0, 20.0])
    div_s_num = safe_div(s, 2.0)
    assert div_s_num.tolist() == [5.0, 10.0]

    div_num_s = safe_div(100.0, s)
    assert div_num_s.tolist() == [10.0, 5.0]

    comp_s_num = safe_comp('>=', s, 15.0)
    assert comp_s_num.tolist() == [0.0, 1.0]

    comp_num_s = safe_comp('<=', 15.0, s)
    assert comp_num_s.tolist() == [0.0, 1.0]

    and_s_num = safe_and(s, 1.0)
    assert and_s_num.tolist() == [1.0, 1.0]

    or_s_num = safe_or(s, 0.0)
    assert or_s_num.tolist() == [1.0, 1.0]


def test_primitives_edge_cases(sample_bars: list[dict[str, Any]]):
    from cli_anything.tdx_tq.core.local_formula import (
        tdx_abs,
        tdx_avedev,
        tdx_barslastcount,
        tdx_ceiling,
        tdx_dma,
        tdx_exp,
        tdx_floor,
        tdx_hhv,
        tdx_hhvbars,
        tdx_ifn,
        tdx_intpart,
        tdx_llv,
        tdx_llvbars,
        tdx_ln,
        tdx_pow,
        tdx_round,
        tdx_sign,
        tdx_sma,
        tdx_sqrt,
        tdx_std,
        tdx_stdp,
        tdx_wma,
    )

    s = pd.Series([10.0, 20.0, 30.0])
    nan_s = pd.Series([np.nan, np.nan])

    # All-nan SMA / DMA / HHV / LLV
    assert tdx_sma(nan_s, 3, 1).isna().all()
    assert tdx_dma(nan_s, 0.5).isna().all()
    assert tdx_hhv(nan_s, 2).isna().all()
    assert tdx_llv(nan_s, 2).isna().all()
    assert tdx_hhvbars(nan_s, 2).isna().all()
    assert tdx_llvbars(nan_s, 2).isna().all()

    # DMA with Series alpha and NaN alpha
    dma_series = tdx_dma(s, pd.Series([0.1, 0.2, 0.3]))
    assert len(dma_series) == 3
    dma_nan = tdx_dma(s, pd.Series([0.1, np.nan, 0.3]))
    assert pd.isna(dma_nan.iloc[1])

    # Negative N errors
    with pytest.raises(TdxParamError, match="HHV函数的周期 N 必须为非负整数"):
        tdx_hhv(s, -1)
    with pytest.raises(TdxParamError, match="LLV函数的周期 N 必须为非负整数"):
        tdx_llv(s, -1)
    with pytest.raises(TdxParamError, match="HHVBARS函数的周期 N 必须为非负整数"):
        tdx_hhvbars(s, -1)
    with pytest.raises(TdxParamError, match="LLVBARS函数的周期 N 必须为非负整数"):
        tdx_llvbars(s, -1)
    with pytest.raises(TdxParamError, match="SUM函数的周期 N 必须为非负整数"):
        tdx_sum(s, -1)
    with pytest.raises(TdxParamError, match="COUNT函数的周期 N 必须为非负整数"):
        tdx_count(s, -1)
    with pytest.raises(TdxParamError, match="EVERY函数的周期 N 必须大于0"):
        tdx_every(s, 0)
    with pytest.raises(TdxParamError, match="EXIST函数的周期 N 必须大于0"):
        tdx_exist(s, 0)
    with pytest.raises(TdxParamError, match="WMA函数的计算周期 N 必须大于0"):
        tdx_wma(s, 0)
    with pytest.raises(TdxParamError, match="STD函数的周期 N 必须大于0"):
        tdx_std(s, 0)
    with pytest.raises(TdxParamError, match="STDP函数的周期 N 必须大于0"):
        tdx_stdp(s, 0)
    with pytest.raises(TdxParamError, match="AVEDEV函数的周期 N 必须大于0"):
        tdx_avedev(s, 0)

    # CROSS length mismatch
    with pytest.raises(TdxParamError, match="CROSS函数两个序列长度不一致"):
        tdx_cross(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0, 3.0]))

    # BARSLASTCOUNT and IFN
    c_bars = tdx_barslastcount(pd.Series([1.0, 1.0, 0.0, 1.0]))
    assert c_bars.tolist() == [1.0, 2.0, 0.0, 1.0]

    ifn_res = tdx_ifn(pd.Series([1.0, 0.0]), 100.0, 200.0)
    assert ifn_res.tolist() == [200.0, 100.0]

    # Math scalar inputs
    assert tdx_abs(-5.0) == 5.0
    assert tdx_sqrt(9.0) == 3.0
    assert math.isnan(tdx_sqrt(-1.0))
    assert tdx_ln(1.0) == 0.0
    assert math.isnan(tdx_ln(-1.0))
    assert math.isnan(tdx_exp(10000))
    assert tdx_pow(2.0, 3.0) == 8.0
    assert math.isnan(tdx_pow(-2.0, 0.5))
    assert tdx_round(3.14159) == 3.14
    assert tdx_ceiling(2.1) == 3.0
    assert tdx_floor(2.9) == 2.0
    assert tdx_intpart(-3.8) == -3.0
    assert tdx_sign(0.0) == 0.0


def test_parser_and_ast_errors(sample_bars: list[dict[str, Any]]):
    from cli_anything.tdx_tq.core.local_formula import (
        NumberNode,
        VarNode,
        evaluate_ast,
        parse_formula_statements,
    )

    with pytest.raises(TdxParamError, match="公式内容为空"):
        parse_formula_statements("")

    with pytest.raises(TdxParamError, match="缺少表达式内容"):
        parse_formula_statements("A :=")

    with pytest.raises(TdxParamError, match="存在多余内容"):
        parse_formula_statements("A := 1 2;")

    with pytest.raises(TdxParamError, match="缺少表达式内容"):
        parse_formula_statements("A :")

    with pytest.raises(TdxParamError, match="缺少有效表达式"):
        parse_formula_statements("A : , COLORWHITE;")

    with pytest.raises(TdxParamError, match="存在无法识别的符号"):
        parse_formula_statements("A : 1 2;")

    with pytest.raises(TdxParamError, match="公式包含空的表达式语句"):
        parse_formula_statements(", COLORRED;")

    with pytest.raises(TdxParamError, match="缺少匹配的右括号"):
        parse_formula_statements("RES: (C + 1;")

    with pytest.raises(TdxParamError, match="公式中没有定义任何输出内容"):
        evaluate_formula_on_bars(sample_bars, "HIDDEN := C + 1;")

    # AST unknown node
    with pytest.raises(TdxParamError, match="未知 AST 节点类型"):
        evaluate_ast(object(), {'locals': {}, 'params': {}, 'series': {}})

    # Single formula code resolution in evaluate_formula_on_bars
    res_code = evaluate_formula_on_bars(sample_bars, "MACD")
    assert 'DIF' in res_code


def test_dataframe_formats_and_market_series_extraction():
    # DataFrame with DatetimeIndex
    dates = pd.date_range('2023-01-01', periods=3)
    df_dt = pd.DataFrame({'close': [10.0, 11.0, 12.0], 'open': [9.0, 10.0, 11.0]}, index=dates)
    res_dt = evaluate_formula_on_bars(df_dt, "MA(C, 2);", return_count=0)
    assert len(res_dt['Date']) == 3

    # DataFrame with RangeIndex without date column
    df_range = pd.DataFrame({'close': [10.0, 11.0], 'vol': [1000, 2000], 'amo': [10000, 20000]})
    res_range = evaluate_formula_on_bars(df_range, "VOL + AMO;", return_count=0)
    assert res_range['RESULT'] == [11000.0, 22000.0]

    # DataFrame empty
    with pytest.raises(TdxParamError, match="行情 K 线数据为空"):
        evaluate_formula_on_bars(pd.DataFrame(), "C;")

    # List empty
    with pytest.raises(TdxParamError, match="行情 K 线数据为空"):
        evaluate_formula_on_bars([], "C;")

    # Invalid bars type
    with pytest.raises(TdxParamError, match="不支持的行情数据格式"):
        evaluate_formula_on_bars(12345, "C;")


def test_batch_expr_argument_and_parameter_errors(sample_bars: list[dict[str, Any]]):
    batch = {'sh600000': sample_bars}

    # Custom expr with positional args_str (must be named)
    with pytest.raises(TdxParamError, match="自定义表达式的 args_str 必须使用命名格式"):
        evaluate_batch_formula(batch, expr="MA(C, N)", args_str="10")

    # Custom expr with invalid args_str value
    with pytest.raises(TdxParamError, match="的值无效"):
        evaluate_batch_formula(batch, expr="MA(C, N)", args_str="N=abc")

    # Custom expr args_str shadowing reserved name
    with pytest.raises(TdxParamError, match="与系统保留字段冲突"):
        evaluate_batch_formula(batch, expr="MA(C, 5)", args_str="C=10")

    # Custom expr params shadowing reserved name
    with pytest.raises(TdxParamError, match="与系统保留字段冲突"):
        evaluate_batch_formula(batch, expr="MA(C, 5)", params={'CLOSE': 10})

    # Custom expr params invalid numeric value
    with pytest.raises(TdxParamError, match="的值无效"):
        evaluate_batch_formula(batch, expr="MA(C, N)", params={'N': 'invalid'})

    # Batch bars not a dict
    with pytest.raises(TdxParamError, match="批量行情数据必须为非空字典结构"):
        evaluate_batch_formula("not_a_dict", formula_name="MA")


def test_thorough_branch_coverage(sample_bars: list[dict[str, Any]]):
    """Cover all remaining branches in local_formula.py to reach >= 93% statements and >= 86% branches."""
    from cli_anything.tdx_tq.core.local_formula import (
        BinaryOpNode,
        NumberNode,
        UnaryOpNode,
        VarNode,
        _ensure_series,
        evaluate_ast,
        parse_formula_args,
        parse_formula_statements,
        safe_not,
        tdx_abs,
        tdx_barslast,
        tdx_between,
        tdx_ceiling,
        tdx_cross,
        tdx_dma,
        tdx_exp,
        tdx_floor,
        tdx_hhv,
        tdx_if,
        tdx_intpart,
        tdx_llv,
        tdx_ln,
        tdx_max,
        tdx_min,
        tdx_pow,
        tdx_ref,
        tdx_round,
        tdx_sign,
        tdx_sma,
        tdx_sqrt,
        tdx_sum,
        tokenize,
    )

    # 1. Tokenizer >= and <=
    toks = tokenize("A >= 10; B <= 20; A == 10; A != 5; A <> 6; A && B; A || B;")
    types = [t.type for t in toks]
    assert 'GE' in types and 'LE' in types and 'EQ' in types and 'NEQ' in types

    # 2. Parser function call with no args & unexpected expression term
    stmt_no_args = parse_formula_statements("X: DRAWNULL();")
    assert stmt_no_args[0].expr.func_name == 'DRAWNULL'

    with pytest.raises(TdxParamError, match="无法解析的表达式项"):
        parse_formula_statements("X: / 5;")

    # 3. Empty statement between semicolons & trailing tokens
    stmts_empty = parse_formula_statements("A: 1;; B: 2;")
    assert len(stmts_empty) == 2

    with pytest.raises(TdxParamError, match="表达式后存在无法识别的符号"):
        parse_formula_statements("1 + 2 3;")

    # 4. _ensure_series with integer Series and ndarray
    s_int = _ensure_series(pd.Series([1, 2], dtype=int))
    assert pd.api.types.is_float_dtype(s_int.dtype)
    s_arr = _ensure_series(np.array([1.5, 2.5]))
    assert len(s_arr) == 2

    # 5. safe_not on scalar
    assert safe_not(1.0) == 0.0
    assert safe_not(0.0) == 1.0
    assert safe_not(float('nan')) == 1.0

    # 6. tdx_ref N=0 returns copy
    s = pd.Series([10.0, 20.0])
    ref0 = tdx_ref(s, 0)
    assert ref0.tolist() == [10.0, 20.0]

    # 7. tdx_sma with middle NaN and recovery
    s_nan_mid = pd.Series([10.0, np.nan, 20.0, 30.0])
    sma_mid = tdx_sma(s_nan_mid, 3, 1)
    assert pd.isna(sma_mid.iloc[1])
    assert sma_mid.iloc[2] == 20.0

    # 8. tdx_dma with y non-finite recovery
    s_dma_mid = pd.Series([10.0, np.nan, 20.0])
    dma_res = tdx_dma(s_dma_mid, 0.5)
    assert pd.isna(dma_res.iloc[1])
    assert dma_res.iloc[2] == 15.0

    # 9. tdx_hhv and tdx_llv N=0
    assert tdx_hhv(s, 0).tolist() == [10.0, 20.0]
    assert tdx_llv(s, 0).tolist() == [10.0, 10.0]

    # 10. tdx_sum N=0
    assert tdx_sum(s, 0).tolist() == [10.0, 30.0]

    # 11. tdx_cross single-element broadcasting
    assert tdx_cross(pd.Series([15.0]), pd.Series([10.0, 20.0])).tolist() == [0.0, 0.0]
    assert tdx_cross(pd.Series([10.0, 20.0]), pd.Series([15.0])).tolist() == [0.0, 1.0]

    # 12. tdx_barslast all-false
    assert tdx_barslast(pd.Series([0.0, 0.0])).isna().all()

    # 13. tdx_if both Series vs both scalars
    if_both_s = tdx_if(pd.Series([1.0, 0.0]), pd.Series([10.0, 20.0]), pd.Series([30.0, 40.0]))
    assert if_both_s.tolist() == [10.0, 40.0]

    # 14. tdx_max and tdx_min combinations
    assert tdx_max(s, 15.0).tolist() == [15.0, 20.0]
    assert tdx_min(s, 15.0).tolist() == [10.0, 15.0]
    assert tdx_max(10.0, 20.0) == 20.0
    assert tdx_min(10.0, 20.0) == 10.0

    # 15. Math functions on Series and scalars
    assert tdx_abs(s).tolist() == [10.0, 20.0]
    assert tdx_sqrt(pd.Series([4.0, -1.0])).iloc[0] == 2.0
    assert pd.isna(tdx_sqrt(pd.Series([4.0, -1.0])).iloc[1])
    assert tdx_ln(pd.Series([1.0, -1.0])).iloc[0] == 0.0
    assert pd.isna(tdx_ln(pd.Series([1.0, -1.0])).iloc[1])
    assert tdx_exp(s).iloc[0] == math.exp(10.0)
    assert tdx_pow(s, 2.0).tolist() == [100.0, 400.0]
    assert tdx_round(pd.Series([1.234, 5.678]), 1).tolist() == [1.2, 5.7]
    assert tdx_ceiling(pd.Series([1.2, 2.8])).tolist() == [2.0, 3.0]
    assert tdx_floor(pd.Series([1.2, 2.8])).tolist() == [1.0, 2.0]
    assert tdx_intpart(pd.Series([-1.8, 2.8])).tolist() == [-1.0, 2.0]
    assert tdx_sign(pd.Series([-5.0, 0.0, 5.0])).tolist() == [-1.0, 0.0, 1.0]

    # 16. tdx_between NaN branches
    assert pd.isna(tdx_between(pd.Series([np.nan]), 1.0, 2.0).iloc[0])
    assert pd.isna(tdx_between(pd.Series([1.5]), pd.Series([np.nan]), 2.0).iloc[0])
    assert pd.isna(tdx_between(pd.Series([1.5]), 1.0, pd.Series([np.nan])).iloc[0])
    assert pd.isna(tdx_between(pd.Series([1.5]), np.nan, 2.0).iloc[0])
    assert pd.isna(tdx_between(pd.Series([1.5]), 1.0, np.nan).iloc[0])

    # 17. AST evaluation unary operators & constants
    ctx = {
        'locals': {},
        'params': {},
        'series': {
            'close': pd.Series([10.0, 20.0]),
            'open': pd.Series([9.0, 19.0]),
            'high': pd.Series([11.0, 21.0]),
            'low': pd.Series([8.0, 18.0]),
            'volume': pd.Series([1000.0, 2000.0]),
            'amount': pd.Series([10000.0, 40000.0]),
        },
    }
    assert evaluate_ast(VarNode('TRUE'), ctx) == 1.0
    assert evaluate_ast(VarNode('FALSE'), ctx) == 0.0
    assert evaluate_ast(VarNode('AMO'), ctx).iloc[0] == 10000.0
    assert evaluate_ast(VarNode('AMOUNT'), ctx).iloc[0] == 10000.0
    assert evaluate_ast(VarNode('VOL'), ctx).iloc[0] == 1000.0
    assert evaluate_ast(VarNode('VOLUME'), ctx).iloc[0] == 1000.0
    assert evaluate_ast(VarNode('V'), ctx).iloc[0] == 1000.0
    assert evaluate_ast(VarNode('HIGH'), ctx).iloc[0] == 11.0
    assert evaluate_ast(VarNode('H'), ctx).iloc[0] == 11.0
    assert evaluate_ast(VarNode('LOW'), ctx).iloc[0] == 8.0
    assert evaluate_ast(VarNode('L'), ctx).iloc[0] == 8.0

    # Unary plus and minus
    u_plus = evaluate_ast(UnaryOpNode('+', VarNode('C')), ctx)
    assert u_plus.iloc[0] == 10.0
    u_minus = evaluate_ast(UnaryOpNode('-', VarNode('C')), ctx)
    assert u_minus.iloc[0] == -10.0
    u_minus_num = evaluate_ast(UnaryOpNode('-', NumberNode(5.0)), ctx)
    assert u_minus_num == -5.0
    u_not = evaluate_ast(UnaryOpNode('NOT', VarNode('C')), ctx)
    assert u_not.iloc[0] == 0.0

    with pytest.raises(TdxParamError, match="未知一元运算符"):
        evaluate_ast(UnaryOpNode('~', NumberNode(1.0)), ctx)

    with pytest.raises(TdxParamError, match="未知二元运算符"):
        evaluate_ast(BinaryOpNode('^', NumberNode(1.0), NumberNode(2.0)), ctx)

    # 18. parse_formula_args error branches
    with pytest.raises(TdxParamError, match="与系统保留字段冲突"):
        parse_formula_args([('C', 10.0)], '5')

    with pytest.raises(TdxParamError, match="必须为数值"):
        parse_formula_args([('N', 10.0)], 'abc')

    with pytest.raises(TdxParamError, match="与系统保留字段冲突"):
        parse_formula_args([('N', 10.0)], 'C=5')

    with pytest.raises(TdxParamError, match="必须为有效数值"):
        parse_formula_args([('N', 10.0)], 'N=abc')

    with pytest.raises(TdxParamError, match="与系统保留字段冲突"):
        parse_formula_args([('N', 10.0)], overrides={'C': 5.0})

    with pytest.raises(TdxParamError, match="必须为有效数值"):
        parse_formula_args([('N', 10.0)], overrides={'N': 'invalid'})

    # 20. evaluate_ast missing field and wrong arg count
    ctx_missing = {'locals': {}, 'params': {}, 'series': {'close': None}}
    with pytest.raises(TdxParamError, match="缺少 close 字段"):
        evaluate_ast(VarNode('C'), ctx_missing)

    from cli_anything.tdx_tq.core.local_formula import CallNode
    with pytest.raises(TdxParamError, match="函数 ABS 需要 1 个参数"):
        evaluate_ast(CallNode('ABS', []), ctx)
    with pytest.raises(TdxParamError, match="函数 ABS 需要 1 个参数"):
        evaluate_ast(CallNode('ABS', [NumberNode(1.0), NumberNode(2.0)]), ctx)

    # 21. parse_formula_args unknown override & invalid override float
    with pytest.raises(TdxParamError, match="包含未知参数"):
        parse_formula_args([('N', 10.0)], overrides={'UNKNOWN_PARAM': 1.0})

    with pytest.raises(TdxParamError, match="必须为有效数值"):
        parse_formula_args([('N', 10.0)], overrides={'N': 'abc'})

    # 22. evaluate_batch_formula with empty output formula
    batch = {'sh600000': sample_bars}
    # Test batch evaluate where stock has no output besides date (if possible)
    res_batch = evaluate_batch_formula(batch, expr="RES: C;", select_mode=True)
    assert len(res_batch['summary_rows']) == 1
