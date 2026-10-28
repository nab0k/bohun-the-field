#!/usr/bin/env python3
"""Single reviewed FREE_ONLY runtime. Never installs SQL or enables a schedule."""
import argparse
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from collectors.intelligence_pipeline.cloud.runtime import live_run,validate_config,verify_revision,reconcile
from collectors.intelligence_pipeline.cloud.postgres import open_rpc
from collectors.intelligence_pipeline.cloud.notion import NotionDelivery


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha",required=True)
    parser.add_argument("--config",required=True)
    parser.add_argument("--allow-live",action="store_true")
    parser.add_argument("--reconcile-key")
    args=parser.parse_args(argv)
    if not args.allow_live:
        print("REFUSED: LIVE_ACCESS_NOT_ENABLED",file=sys.stderr);return 2
    try:
        with Path(args.config).open('rb') as handle:raw=handle.read(256*1024+1)
        if len(raw)>256*1024:raise ValueError("CONFIG_BOUND")
        config=validate_config(json.loads(raw))
        if args.reconcile_key:
            if config['expected_sha']!=args.expected_sha:raise ValueError("REVISION_MISMATCH")
            verify_revision(args.expected_sha)
            rpc=open_rpc(os.environ['BOHUN_ANALYSIS_DSN'])
            notion=NotionDelivery(os.environ['BOHUN_NOTION_TOKEN'],config['notion_parent'])
            result=reconcile(rpc,notion,config['run_scope'],args.reconcile_key)
        else:result=live_run(config,allow_live=True,expected_sha=args.expected_sha)
        print(json.dumps(result,sort_keys=True))
        delivered=result.get('delivery')
        return 0 if delivered=='DELIVERED' or (isinstance(delivered,list) and delivered and all(s=='DELIVERED' for s in delivered)) else 2
    except Exception:
        print("STOPPED: UNCONFIRMED; read DB/budget/outbox by ID before any repeat.",file=sys.stderr);return 2


if __name__=='__main__':sys.exit(main())
