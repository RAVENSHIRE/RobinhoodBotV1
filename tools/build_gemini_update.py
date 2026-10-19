"""Build a Windows update from known prior file hashes; never bundle runtime secrets."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def digest(data):
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def main():
    with zipfile.ZipFile(ROOT/'downloads/hoodscout-astra-update.zip') as previous:
        old = json.loads(previous.read('manifest.json'))
        updater = previous.read('apply_update.py').decode()
    updater = updater.replace(
        "print('Next: run .venv Python with -m pytest -q, then -m hoodscout.cli jev-setup to verify JEV locally. Keep LIVE_MODE=false.')",
        "print('Next: run tests, then hoodscout gemini-setup using your existing local GEMINI_API_KEY. Keep LIVE_MODE=false.')")
    updater = updater.replace(
        "print('New: swap activity tracking, JEV credit prefilter and the dashboard kill switch. See README.md.')",
        "print('New: Gemini research adapter with a daily call cap and reverse-quote paper exits. See README.md.')")
    known = {e['path']: set((e['before'] if isinstance(e['before'],list) else [e['before']]) + [e['after']])
             for e in old['files']}
    names = set(known)
    names.update(str(p.relative_to(ROOT)).replace('\\','/')
                 for folder in ('src','tests') for p in (ROOT/folder).rglob('*')
                 if p.is_file() and p.suffix in ('.py','.html','.json') and '__pycache__' not in p.parts)
    manifest, payload = [], {}
    for name in sorted(names):
        data = (ROOT/name).read_bytes()
        accepted = known.get(name, {None})
        prior = subprocess.run(['git','show',f'HEAD:{name}'],cwd=ROOT,capture_output=True)
        if prior.returncode == 0:
            accepted.add(digest(prior.stdout))
        manifest.append({'path':name,'before':sorted(accepted,key=lambda x:x or ''),'after':digest(data)})
        payload[name] = data
    output = ROOT/'downloads/hoodscout-gemini-update.zip'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('apply_update.py',updater)
        z.writestr('manifest.json',json.dumps({'files':manifest},indent=2))
        z.write(ROOT/'downloads/Update-and-Start-Gemini.cmd','Update-and-Start-Gemini.cmd')
        z.write(ROOT/'downloads/GEMINI-WINDOWS.md','GEMINI-WINDOWS.md')
        for name,data in payload.items():
            z.writestr('payload/'+name,data)
    print(output.name,hashlib.sha256(output.read_bytes()).hexdigest(),len(payload),'files')

if __name__ == '__main__':
    main()
