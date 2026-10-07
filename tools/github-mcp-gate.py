"""Launch only the approved and unchanged Desk repository. No stdout except MCP."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mc.desk_connect.github_activation import start
if __name__=='__main__':
    try:
        scope,pid,name,fp=sys.argv[1:]
        start(scope,None if pid=='-' else pid,name,fp)
    except Exception as e:
        print(f'[desk-connect] repository start refused: {e}',file=sys.stderr)
        sys.exit(1)
