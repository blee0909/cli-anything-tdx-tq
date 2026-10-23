"""Isolate personal configuration and make real-client reads opt-in."""
import pytest



def pytest_addoption(parser):
    parser.addoption('--live', action='store_true', help='Run read-only integration tests against a logged-in TDX client')


def pytest_collection_modifyitems(config, items):
    if not config.getoption('--live'):
        for item in items:
            if item.get_closest_marker('live'):
                item.add_marker(pytest.mark.skip(reason='需要显式 --live 启用真实客户端读取'))


@pytest.fixture(autouse=True)
def isolate_config_and_session(tmp_path, monkeypatch, request):
    from cli_anything.tdx_tq.core.config import Config, config
    from cli_anything.tdx_tq.core.session import session
    actual_path = config.tdx_path
    path = tmp_path / 'config.json'
    monkeypatch.setenv('TDX_CONFIG_FILE', str(path))
    if request.node.get_closest_marker('live'):
        monkeypatch.setenv('TDX_PATH', actual_path)
    else:
        monkeypatch.delenv('TDX_PATH', raising=False)
    monkeypatch.setattr(Config, 'DEFAULT_CONFIG_FILE', path)
    for name, value in (('_config_file', path), ('_tdx_path', None), ('_override_path', None), ('load_error', None)):
        monkeypatch.setattr(config, name, value)
    for name, value in (('_tdx', None), ('_initialized', False), ('_loaded_path', None)):
        monkeypatch.setattr(session, name, value)
    yield
    session.close()
