"""Combine intraday limit details with the client's recent event dates."""
import datetime
import re

from .errors import TdxDataError
from .sdk import validate_result


RECENT_FIELDS = ['HqDate', 'ZTDate_Recent', 'DTDate_Recent']


def _event_date(info, field):
    value = info.get(field)
    if value is None or value == '':
        return None, 'unavailable'
    if not isinstance(value, bool) and value in (0, '0', '00000000'):
        return None, 'no_record'
    text = str(value)
    if not re.fullmatch(r'[0-9]{8}', text):
        raise TdxDataError(f'{field} 返回无效日期: {value!r}')
    try:
        date = datetime.datetime.strptime(text, '%Y%m%d').date()
    except ValueError as exc:
        raise TdxDataError(f'{field} 返回无效日期: {value!r}') from exc
    return date.isoformat(), 'found'


def get_limit_summary(tdx, stocks):
    """Preserve client event dates, including values outside its documented window."""
    current = validate_result(tdx.get_zdt_data(stock_list=stocks), 'get_zdt_data', require_data=True)
    if not isinstance(current, dict):
        raise TdxDataError('涨跌停明细必须为证券字典')
    result = {}
    for code in stocks:
        details = current.get(code)
        if not isinstance(details, dict) or not details:
            raise TdxDataError(f'{code} 缺少涨跌停明细')
        validate_result(details, 'get_zdt_data')
        info = validate_result(tdx.get_more_info(stock_code=code, field_list=RECENT_FIELDS.copy()),
                               'get_more_info')
        if not isinstance(info, dict):
            raise TdxDataError(f'{code} 最近涨跌停日期响应必须为字段字典')
        as_of, _ = _event_date(info, 'HqDate')
        up, up_status = _event_date(info, 'ZTDate_Recent')
        down, down_status = _event_date(info, 'DTDate_Recent')
        if as_of and any(date and date > as_of for date in (up, down)):
            raise TdxDataError(f'{code} 最近涨跌停日期晚于客户端行情日期')
        outside = []
        if as_of:
            reference = datetime.date.fromisoformat(as_of)
            try:
                lower = reference.replace(year=reference.year - 2)
            except ValueError:  # February 29 in a leap year.
                lower = reference.replace(year=reference.year - 2, day=28)
            outside = [field for field, date in [('last_limit_up_date', up), ('last_limit_down_date', down)]
                       if date and date < lower.isoformat()]
        result[code] = {
            **details,
            'last_limit_up_date': up,
            'last_limit_down_date': down,
            'history': {
                'scope': 'sdk_recent_events',
                'documented_lookback_years': 2,
                'outside_documented_window': outside,
                'source': 'get_more_info',
                'as_of': as_of,
                'limit_up_status': up_status,
                'limit_down_status': down_status,
            },
        }
    return result
