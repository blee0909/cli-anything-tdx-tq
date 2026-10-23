"""Stable errors shared by the CLI, domain services and SDK adapter."""

class TdxError(Exception):
    code = "TDX_ERROR"
    retryable = False

    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details

class TdxInitError(TdxError):
    code = "SDK_UNAVAILABLE"

class TdxDataError(TdxError):
    code = "SDK_DATA_ERROR"

class TdxIncompleteDataError(TdxDataError):
    code = "DATA_INCOMPLETE"

    def __init__(self, message, *, details, data):
        super().__init__(message, details=details)
        self.data = data

class TdxReadTimeoutError(TdxDataError):
    code = "SDK_TIMEOUT"
    retryable = True

class TdxParamError(TdxError, ValueError):
    code = "INVALID_PARAMETER"

class TdxExecutionUnknownError(TdxError):
    code = "TRADE_STATE_UNKNOWN"

class TdxAccountStateUnknownError(TdxError):
    code = "ACCOUNT_STATE_UNKNOWN"

class TdxOperationUnknownError(TdxError):
    code = "OPERATION_STATE_UNKNOWN"

class TdxConfigError(TdxError):
    code = "CONFIG_ERROR"
