"""Real-time subscribe_hq streaming engine."""
import datetime
import json
import logging
import queue
import time
from typing import Iterator

from .errors import TdxParamError
from .market import stock_code

logger = logging.getLogger('cli_anything.tdx_tq')

MAX_SUBSCRIBE_STOCKS = 100


def stream_quotes(
    tdx,
    stocks: list[str],
    *,
    with_snapshot: bool = True,
    fields: list[str] | None = None,
    debounce_ms: int = 500,
    duration: float | None = None,
    max_events: int | None = None,
    poll_interval: float = 0.1,
    clock=time.monotonic,
) -> Iterator[dict]:
    """Stream real-time quote updates from TDX subscribe_hq.

    Parameters:
        tdx: TqAdapter or SDK instance with subscribe_hq and unsubscribe_hq.
        stocks: List of stock symbols (up to 100).
        with_snapshot: Whether to fetch market snapshot on main thread.
        fields: Optional field list for snapshot.
        debounce_ms: Per-symbol debounce window in milliseconds.
        duration: Maximum run duration in seconds.
        max_events: Maximum number of events to yield.
        poll_interval: Queue poll timeout in seconds.
        clock: Monotonic clock function.
    """
    if not isinstance(stocks, list) or len(stocks) == 0:
        raise TdxParamError("订阅股票列表不能为空")
    if len(stocks) > MAX_SUBSCRIBE_STOCKS:
        raise TdxParamError(f"订阅股票数超过上限 {MAX_SUBSCRIBE_STOCKS} (当前 {len(stocks)})")

    normalized_stocks = [stock_code(s) for s in stocks]

    if debounce_ms is None or debounce_ms < 0:
        raise TdxParamError("debounce_ms 必须为非负整数")

    if duration is not None and duration <= 0:
        raise TdxParamError("duration 必须为正数")

    if max_events is not None and (not isinstance(max_events, int) or max_events < 1):
        raise TdxParamError("max_events 必须为正整数")

    if poll_interval <= 0:
        raise TdxParamError("poll_interval 必须为正数")

    debounce_sec = debounce_ms / 1000.0
    event_queue: queue.Queue = queue.Queue()

    def on_data(datas):
        ts = clock()
        payload = None
        if isinstance(datas, bytes):
            try:
                datas = datas.decode('utf-8')
            except UnicodeDecodeError:
                datas = datas.decode('gbk', errors='ignore')
        if isinstance(datas, str):
            try:
                payload = json.loads(datas)
            except Exception:
                payload = {'raw': datas}
        elif isinstance(datas, dict):
            payload = datas
        else:
            payload = {'raw': str(datas)}
        event_queue.put((ts, payload))

    subscribed = False
    try:
        tdx.subscribe_hq(stock_list=normalized_stocks, callback=on_data)
        subscribed = True

        seq = 0
        start_time = clock()
        last_emitted: dict[str, float] = {}

        while True:
            if max_events is not None and seq >= max_events:
                break

            if duration is not None:
                elapsed = clock() - start_time
                if elapsed >= duration:
                    break
                timeout = min(poll_interval, max(0.001, duration - elapsed))
            else:
                timeout = poll_interval

            try:
                received_ts, payload = event_queue.get(timeout=timeout)
            except queue.Empty:
                continue

            code = payload.get('Code') or payload.get('code')
            if not code:
                if len(normalized_stocks) == 1:
                    code = normalized_stocks[0]
                else:
                    code = str(payload.get('raw', ''))

            try:
                code = stock_code(code)
            except Exception:
                pass

            last_ts = last_emitted.get(code)
            if last_ts is not None and (received_ts - last_ts) < debounce_sec:
                continue
            last_emitted[code] = received_ts

            seq += 1

            snap = None
            if with_snapshot and code:
                try:
                    if fields:
                        snap = tdx.get_market_snapshot(stock_code=code, field_list=fields)
                    else:
                        snap = tdx.get_market_snapshot(stock_code=code)
                except TypeError:
                    try:
                        snap = tdx.get_market_snapshot(code)
                    except Exception as exc:
                        logger.debug("获取快照失败 (%s): %s", code, exc)
                        snap = None
                except Exception as exc:
                    logger.debug("获取快照失败 (%s): %s", code, exc)
                    snap = None

            iso_now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            yield {
                'event': 'quote_update',
                'stock': code,
                'timestamp': iso_now,
                'seq': seq,
                'snapshot': snap,
            }

    finally:
        if subscribed:
            try:
                tdx.unsubscribe_hq(stock_list=normalized_stocks)
            except Exception as exc:
                logger.warning("取消订阅失败: %s", exc)
