"""Trading requests and responses. No automatic submission retries."""
import math
import numbers
from dataclasses import dataclass

from .errors import TdxError, TdxExecutionUnknownError, TdxParamError
from .market import stock_code
from .sdk import validate_result


@dataclass(frozen=True)
class OrderRequest:
    stock: str
    direction: str
    volume: int
    price: float
    price_type: int = 0

    def validate(self) -> None:
        stock_code(self.stock)
        if self.direction not in ('buy', 'sell'):
            raise TdxParamError("买卖方向必须为 buy 或 sell")
        if isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume <= 0:
            raise TdxParamError("委托数量必须为正整数")
        if not math.isfinite(self.price) or self.price < 0:
            raise TdxParamError("委托价格必须为有限非负数")
        if self.price_type not in (0, 1):
            raise TdxParamError("当前支持的报价方式为 0=限价、1=市价，请先核实客户端能力")
        if self.price_type == 0 and self.price <= 0:
            raise TdxParamError("限价委托的价格必须大于零；市价委托需显式指定 --price-type 1")

    def as_dict(self, account_id: int | None) -> dict:
        self.validate()
        return dict(account_id=account_id, stock=stock_code(self.stock), direction=self.direction,
                    volume=self.volume, price=self.price, price_type=self.price_type)


def submit_order(sdk, request: OrderRequest, account_id: int) -> dict:
    request.validate()
    if isinstance(account_id, bool) or not isinstance(account_id, numbers.Integral) or account_id < 0:
        raise TdxParamError("交易账户 ID 无效")
    try:
        result = sdk.order_stock(account_id=account_id, stock_code=stock_code(request.stock),
                                 order_type=0 if request.direction == 'buy' else 1,
                                 order_volume=request.volume, price_type=request.price_type,
                                 price=request.price)
    except TdxError:
        raise
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        raise TdxExecutionUnknownError("提交未取得确认回执，委托状态未知；请查询核对，勿直接重复提交") from exc
    return validate_result(result, 'order_stock', trade=True)


def cancel_order(sdk, account_id: int, order_id: str, stock: str = '') -> dict:
    if isinstance(account_id, bool) or not isinstance(account_id, numbers.Integral) or account_id < 0 or not order_id.strip():
        raise TdxParamError("撤单需要有效账户和非空订单号")
    stock = stock_code(stock) if stock else ''
    try:
        result = sdk.cancel_order_stock(account_id=account_id, stock_code=stock, order_id=order_id.strip())
    except TdxError:
        raise
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        raise TdxExecutionUnknownError("撤单未取得确认回执，状态未知；请查询委托记录核对") from exc
    return validate_result(result, 'cancel_order_stock', trade=True)
