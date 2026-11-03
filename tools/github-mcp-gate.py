"""Launch only the approved and unchanged Desk repository. No stdout except MCP."""
import os
import sys
from pathlib import Path
# This process starts inside the approved repository. On Windows a bare program name is looked up in the
# current directory first, so switch that off before anything is spawned (start() also resolves absolute paths).
os.environ['NoDefaultCurrentDirectoryInExePath']='1'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mc.desk_connect.github_activation import start
if __name__=='__main__':
    try:
        scope,pid,name,fp=sys.argv[1:]
        start(scope,None if pid=='-' else pid,name,fp)
    except Exception as e:
        print(f'[desk-connect] repository start refused: {e}',file=sys.stderr)
        sys.exit(1)
