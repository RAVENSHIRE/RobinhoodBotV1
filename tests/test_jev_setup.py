from dataclasses import replace
import io

from dotenv import dotenv_values

from hoodscout.config import load_config
from hoodscout.jev_setup import save_settings, setup_jev


def test_local_settings_preserve_existing_values_and_parse_key_safely(tmp_path):
    path=tmp_path/'.env'
    path.write_text('# User settings\nETH_USD=1234\nDATA_DIR=./data/paper\nDECISION_PROVIDER=stub\n')
    cfg=replace(load_config(),typesafe_api_key="synthetic'key",decision_provider='jev')
    save_settings(path,cfg)
    values=dotenv_values(path)
    assert values['ETH_USD']=='1234' and values['DATA_DIR']=='./data/paper'
    assert values['TYPESAFE_API_KEY']=="synthetic'key" and values['DECISION_PROVIDER']=='jev'
    assert '# User settings' in path.read_text()
    assert not list(tmp_path.glob('.hoodscout-jev-*.tmp'))


def test_failed_connection_does_not_save_key(tmp_path,monkeypatch,capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'pyproject.toml').write_text('')
    (tmp_path/'src/hoodscout').mkdir(parents=True)
    path=tmp_path/'.env';path.write_text('DECISION_PROVIDER=stub\n')
    monkeypatch.setattr('hoodscout.jev_setup.sys.stdin.isatty',lambda:True)
    monkeypatch.setattr('hoodscout.jev_setup.getpass.getpass',lambda prompt:'not-a-real-key')
    monkeypatch.setattr('hoodscout.jev_setup.check_jev',lambda cfg:1)
    assert setup_jev(load_config())==1
    assert path.read_text()=='DECISION_PROVIDER=stub\n'
    assert 'not-a-real-key' not in capsys.readouterr().out
