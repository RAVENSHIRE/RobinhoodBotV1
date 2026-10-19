"""Use the existing local key, verify one synthetic response, then select Gemini."""
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import tempfile

from dotenv import set_key
from .providers import engine_from_config
from .store import Store


def check_gemini(cfg):
    result = engine_from_config(replace(cfg, decision_provider='gemini'), Store(cfg.data_dir)).check_connection()
    print(json.dumps(result, indent=2))
    return 0 if result['model_status']['connected'] else 1


def setup_gemini(cfg):
    path = Path('.env')
    if not path.is_file() or not Path('src/hoodscout').is_dir():
        print('Run gemini-setup from the HoodScout project containing your .env.')
        return 1
    print('Checking Gemini with one small synthetic inference; it may use API credits.')
    print('The existing GEMINI_API_KEY is used without displaying it. No transactions are sent.')
    if check_gemini(cfg):
        print('Gemini check failed. Provider settings were not changed.')
        return 1
    handle, name = tempfile.mkstemp(prefix='.hoodscout-gemini-', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(handle, 'wb') as file:
            file.write(path.read_bytes())
        for key, value in {'DECISION_PROVIDER': 'gemini', 'GEMINI_MODEL': cfg.gemini_model,
                           'LIVE_MODE': 'false'}.items():
            set_key(str(temp), key, value, quote_mode='always')
        os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
        os.replace(temp, path)
    except OSError:
        print('Gemini responded, but .env could not be saved. Check file permissions.')
        return 1
    finally:
        temp.unlink(missing_ok=True)
    print('Gemini selected for PAPER decisions. Restart HoodScout to load the settings.')
    return 0
