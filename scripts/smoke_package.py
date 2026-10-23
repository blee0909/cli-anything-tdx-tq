"""Run with the installed wheel's Python from outside the source checkout."""
import importlib.metadata
import importlib.resources
import json
from pathlib import Path
import subprocess
import sys

import cli_anything.tdx_tq as package


def main():
    distribution=importlib.metadata.distribution('cli-anything-tdx-tq')
    assert package.__version__==distribution.version
    assert importlib.resources.files(package).joinpath('skills/SKILL.md').is_file()
    assert not any('/tests/' in str(p).replace('\\','/') for p in distribution.files)
    assert {'tdx-tq','cli-anything-tdx-tq'} <= {p.name for p in distribution.entry_points}
    for name in ('tdx-tq','cli-anything-tdx-tq'):
        executable=Path(sys.executable).parent / (name+'.exe')
        result=subprocess.run([str(executable),'--version'],capture_output=True,text=True,encoding='utf-8',timeout=15)
        assert result.returncode==0 and package.__version__ in result.stdout,result
    result=subprocess.run([sys.executable,'-m','cli_anything.tdx_tq.tdx_tq_cli','status','--json'],
                          capture_output=True,text=True,encoding='utf-8',timeout=15)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['session_initialized'] is False
    preview=subprocess.run([sys.executable,'-m','cli_anything.tdx_tq.tdx_tq_cli','--tdx-path','missing-sdk',
                            'trade','order','-s','600519.SH','-t','buy','-v','100','-p','10','--json'],
                           capture_output=True,text=True,encoding='utf-8',timeout=15)
    assert preview.returncode==0,preview.stderr
    assert json.loads(preview.stdout)['status']=='preview'
    print(json.dumps({'version':package.__version__,'module':package.__file__,'resources':'ok'},ensure_ascii=False))


if __name__=='__main__':main()
