"""mc_tunnel/build.rs must not put the client secret in a tracked file
(MC-1179 pass 12 second wave, TL49). Static checks on the source; the build
itself was run by hand with cargo (see the commit message)."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TUNNEL = ROOT / 'mc_tunnel'


def _build_rs():
    return (TUNNEL / 'build.rs').read_text(encoding='utf-8')


def test_build_script_writes_to_out_dir_not_src():
    text = _build_rs()
    assert 'OUT_DIR' in text
    assert not re.search(r'Path::new\("src"\)', text)


def test_main_includes_the_generated_file_from_out_dir():
    main = (TUNNEL / 'src' / 'main.rs').read_text(encoding='utf-8')
    assert 'include!(concat!(env!("OUT_DIR"), "/client_secret.rs"))' in main


def test_no_client_secret_file_is_tracked_in_src():
    out = subprocess.run(['git', 'ls-files', 'mc_tunnel/src'], cwd=ROOT,
                         capture_output=True, text=True, timeout=30).stdout
    assert 'client_secret.rs' not in out


def test_one_key_variable_without_the_other_is_a_build_error():
    text = _build_rs()
    assert text.count('set both or neither') == 2
    assert '(Some(_), None)' in text and '(None, Some(_))' in text


def test_comments_do_not_describe_an_ignore_that_is_not_there():
    gi = (TUNNEL / '.gitignore').read_text(encoding='utf-8')
    assert 'git checkout src/client_secret.rs' not in gi
    assert "gitignore'd" not in _build_rs()
